from fastapi import FastAPI, WebSocket, HTTPException, Depends, Query
from fastapi.middleware.cors import CORSMiddleware
import redis
import json
import os
import time
import signal
import sys
import multiprocessing
import threading
import atexit
from dotenv import load_dotenv
from typing import List, Dict, Any, Set, Optional, Tuple
import asyncio
from pprint import pprint
from datetime import datetime, timedelta
import logging
import requests
from bs4 import BeautifulSoup
import aiohttp
import uuid
import warnings
# Import the get_token_info function from pumpfun_comments.py
from pumpfun_comments import get_token_info

# 禁用urllib3的InsecureRequestWarning警告
from urllib3.exceptions import InsecureRequestWarning
warnings.filterwarnings('ignore', category=InsecureRequestWarning)

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# 禁用uvicorn的访问日志
import uvicorn.config
uvicorn.config.LOGGING_CONFIG["loggers"]["uvicorn.access"]["level"] = "ERROR"

# Load environment variables
load_dotenv()

# Redis configuration
REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
REDIS_DB = int(os.getenv("REDIS_DB", "6"))
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD", None)

# Initialize Redis client
redis_client = redis.Redis(
    host=REDIS_HOST,
    port=REDIS_PORT,
    db=REDIS_DB,
    password=REDIS_PASSWORD,
    decode_responses=True
)

# Initialize FastAPI app
app = FastAPI(title="Axiom Project Server")

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, replace with specific origins
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Store connected WebSocket clients
connected_clients: List[WebSocket] = []

# Store recent projects, used for deduplication
recent_projects: Dict[str, datetime] = {}  # tokenContract -> timestamp
TIME_WINDOW = timedelta(minutes=5)  # 5 minutes time window

# Active projects tracking
active_projects: Dict[str, Dict[str, Any]] = {}  # mint_address -> project_data
project_history: Dict[str, List[Dict[str, Any]]] = {}  # mint_address -> [history of token_info]
active_project_broadcasts: Set[str] = set()  # Set of mint_addresses that have been broadcast as active
ACTIVITY_CHECK_INTERVAL = 60  # Check activity every 60 seconds
ACTIVITY_THRESHOLD = 3  # Need 3 consecutive activity records to determine activity

# Process control
background_process = None
stop_event = multiprocessing.Event()

# 验证合约地址是否可能有效
def is_valid_contract_address(contract_address: str) -> bool:
    """检查合约地址格式是否可能有效"""
    if not contract_address:
        return False
    
    # 检查合约地址长度是否合理
    if len(contract_address) < 5 or len(contract_address) > 64:
        logger.warning(f"合约地址长度异常: '{contract_address}' (长度: {len(contract_address)})")
        return False
    
    # 检查是否包含无效字符
    valid_chars = set("0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ")
    if not all(c in valid_chars for c in contract_address):
        invalid_chars = [c for c in contract_address if c not in valid_chars]
        logger.warning(f"合约地址包含无效字符: '{contract_address}', 无效字符: {invalid_chars}")
        return False
    
    return True

# Function to fetch pump.fun description
async def fetch_pump_description(token_contract: str) -> str:
    """Fetch description from pump.fun for a given token contract."""
    # 生成请求跟踪ID
    request_id = str(uuid.uuid4())[:8]
    
    # 验证合约地址
    if not is_valid_contract_address(token_contract):
        return None
        
    try:
        # 构建URL
        url = f"https://pump.fun/coin/{token_contract}?include-nsfw=true"
        
        # 使用requests库发送请求
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
        }
        
        # 发送请求，设置5秒超时
        response = requests.get(url, headers=headers, timeout=5, verify=False)
        
        # 检查响应状态
        if response.status_code != 200:
            return None
        
        # 解析HTML内容
        html_content = response.text
        soup = BeautifulSoup(html_content, 'html.parser')
        
        # 提取描述信息
        description = None
        
        # 尝试从meta description获取
        meta_description = soup.find('meta', attrs={'name': 'description'})
        if meta_description:
            description = meta_description.get('content')
            return description
        
        # 尝试从og:description获取
        og_description = soup.find('meta', attrs={'property': 'og:description'})
        if og_description:
            description = og_description.get('content')
            return description
        
        # 如果没有找到描述，尝试从其他元素获取
        desc_divs = soup.find_all('div', class_='text-lg')
        for div in desc_divs:
            text = div.get_text().strip()
            if text:
                return text
        
        # 尝试从段落获取
        paragraphs = soup.find_all('p')
        for p in paragraphs:
            text = p.get_text().strip()
            if text and len(text) > 30:
                return text
        
        return None
        
    except requests.exceptions.RequestException:
        return None
    except Exception:
        return None

# Broadcast to all connected clients
async def broadcast_to_clients(data: Dict[str, Any]):
    # If it's a new project and the contract address ends with "pump", fetch description
    if data.get("type") == "new_project" and "data" in data:
        project = data["data"]
        contract_address = project.get('contractAddress', '')
        if contract_address and contract_address.lower().endswith('pump'):
            # 获取pump.fun描述
            pump_desc = await fetch_pump_description(contract_address)
            
            if pump_desc:
                project['pump_desc'] = pump_desc
    
    # 记录广播状态
    # clients_count = len(connected_clients)
    # if clients_count > 0:
        # logger.info(f"广播消息到 {clients_count} 个连接的客户端")
    
    disconnected_clients = []
    broadcast_errors = 0
    
    for client in connected_clients:
        try:
            await client.send_json(data)
        except Exception as e:
            disconnected_clients.append(client)
            broadcast_errors += 1
            logger.warning(f"广播到客户端失败: {e}")
    
    # Remove disconnected clients
    if disconnected_clients:
        for client in disconnected_clients:
            if client in connected_clients:
                connected_clients.remove(client)
        logger.info(f"已移除 {len(disconnected_clients)} 个断开连接的客户端, 剩余 {len(connected_clients)} 个客户端")
    
    if broadcast_errors:
        logger.warning(f"广播过程中发生了 {broadcast_errors} 个错误")

# Broadcast active project to clients
async def broadcast_active_project(mint_address: str, token_info: Dict[str, Any]):
    """Broadcast an active project to all connected clients"""
    await broadcast_to_clients({
        "type": "active_project", 
        "data": {
            "mint_address": mint_address,
            "token_info": token_info
        }
    })
    # logger.info(f"广播活跃项目: {mint_address}")
    # Add to set of broadcast active projects
    active_project_broadcasts.add(mint_address)

# Broadcast inactive project to clients
async def broadcast_inactive_project(mint_address: str):
    """Broadcast an inactive project to all connected clients"""
    await broadcast_to_clients({
        "type": "inactive_project",
        "data": {
            "mint_address": mint_address
        }
    })
    # logger.info(f"广播不活跃项目: {mint_address}")
    # Remove from set of broadcast active projects
    if mint_address in active_project_broadcasts:
        active_project_broadcasts.remove(mint_address)

# Check if a project is active based on its history
def is_project_active(mint_address: str) -> Tuple[bool, Optional[str]]:
    """
    Check if a project is active based on its history.
    
    Returns a tuple (is_active, reason)
    where reason is a string explaining why the project is considered active
    """
    if mint_address not in project_history:
        return False, None
    
    history = project_history[mint_address]
    
    # Need at least 3 records to determine activity
    if len(history) < 3:
        # If not enough history but is_currently_live is True, consider it active
        if history and history[-1].get('is_currently_live', False):
            return True, "is_currently_live is True"
        return False, None
    
    # Check last 3 records (from newest to oldest)
    last_records = history[-3:]
    
    # Check if is_currently_live is True in the latest record
    if last_records[-1].get('is_currently_live', False):
        return True, "is_currently_live is True"
    
    # Check if reply_count changed in the last 3 records
    reply_counts = [record.get('reply_count') for record in last_records if 'reply_count' in record]
    if len(reply_counts) == 3 and len(set(reply_counts)) > 1:
        return True, "reply_count changed"
    
    # Check if ath_market_cap changed in the last 3 records
    ath_caps = [record.get('ath_market_cap') for record in last_records if 'ath_market_cap' in record]
    if len(ath_caps) == 3 and len(set(ath_caps)) > 1:
        return True, "ath_market_cap changed"
    
    return False, None

# Function to fetch activity data in a separate process
def activity_monitor_process(interval=60):
    """
    Separate process for monitoring activity without blocking the main server
    """
    # Set up logging for this process
    logging.basicConfig(level=logging.INFO)
    process_logger = logging.getLogger("activity_monitor")
    process_logger.info("Activity monitor process started")
    
    # Create a Redis connection for this process
    process_redis = redis.Redis(
        host=REDIS_HOST,
        port=REDIS_PORT,
        db=REDIS_DB,
        password=REDIS_PASSWORD,
        decode_responses=True
    )
    
    # Keep running until the stop event is set
    while not stop_event.is_set():
        try:
            # 减少日志，只在开始时输出一次
            # process_logger.info("Checking projects activity...")
            
            # Get all project IDs from Redis
            project_ids = process_redis.zrevrange("meme_projects_by_time", 0, -1)
            processed_count = 0
            
            for project_id in project_ids:
                # Check if we should stop
                if stop_event.is_set():
                    break
                    
                try:
                    # Get project data
                    project_data = process_redis.get(project_id)
                    if not project_data:
                        continue
                    
                    project = json.loads(project_data)
                    mint_address = project.get('contractAddress', '')
                    
                    # Only process pump.fun tokens (they end with "pump")
                    if not mint_address or not mint_address.lower().endswith('pump'):
                        continue
                    
                    # Fetch token info using get_token_info
                    token_info = get_token_info(mint_address)
                    
                    # Skip if no token info was returned
                    if not token_info or not token_info.get('mint'):
                        continue
                    
                    # 移除详细日志
                    # process_logger.info(f"Fetched token info for {mint_address}")
                    processed_count += 1
                    
                    # Save the token info to a Redis key where the main process can access it
                    process_redis.set(
                        f"token_info:{mint_address}", 
                        json.dumps({
                            "token_info": token_info,
                            "timestamp": time.time()
                        })
                    )
                    
                    # Set a short expiry so Redis cleans up old data
                    process_redis.expire(f"token_info:{mint_address}", 3600)  # 1 hour
                    
                except Exception as e:
                    process_logger.error(f"Error processing project {project_id}: {str(e)}")
            
            # 周期性输出处理了多少个项目
            if processed_count > 0:
                process_logger.info(f"Processed {processed_count} projects")
            
        except Exception as e:
            process_logger.error(f"Activity monitor error: {str(e)}")
        
        # Sleep for the interval, but check for stop event every second
        for _ in range(interval):
            if stop_event.is_set():
                break
            time.sleep(1)
    
    process_logger.info("Activity monitor process stopped")

# Fetch data stored by the monitoring process
async def fetch_token_info_from_redis():
    """Fetch token info data stored by the monitor process and process it"""
    try:
        # Get all token_info keys
        token_info_keys = redis_client.keys("token_info:*")
        
        active_count = 0
        for key in token_info_keys:
            try:
                # Extract mint address from key
                mint_address = key.split(':', 1)[1]
                
                # Get token info
                token_info_data = redis_client.get(key)
                if not token_info_data:
                    continue
                
                data = json.loads(token_info_data)
                token_info = data.get('token_info', {})
                
                # Add to project history
                if mint_address not in project_history:
                    project_history[mint_address] = []
                
                # Add new token info to history
                project_history[mint_address].append(token_info)
                
                # Keep only the last 5 history records
                if len(project_history[mint_address]) > 5:
                    project_history[mint_address] = project_history[mint_address][-5:]
                
                # Check if project is active
                is_active, reason = is_project_active(mint_address)
                
                # 删除详细日志，只保留统计信息
                # logger.info(f"项目信息 - {mint_address}: reply_count={token_info.get('reply_count')}, "
                #            f"ath_market_cap={token_info.get('ath_market_cap')}, "
                #            f"is_currently_live={token_info.get('is_currently_live')}, "
                #            f"活跃={is_active}, 原因={reason}")
                
                if is_active:
                    active_count += 1
                    # Store in active projects if not already broadcast
                    active_projects[mint_address] = token_info
                    
                    # Broadcast if not already broadcast
                    if mint_address not in active_project_broadcasts:
                        await broadcast_active_project(mint_address, token_info)
                else:
                    # If was active but now inactive
                    if mint_address in active_projects:
                        del active_projects[mint_address]
                        
                        # Broadcast inactive status if it was previously broadcast as active
                        if mint_address in active_project_broadcasts:
                            await broadcast_inactive_project(mint_address)
                
            except Exception as e:
                logger.error(f"Error processing token info for {key}: {str(e)}")
        
        logger.info(f"Found {len(token_info_keys)} projects, {active_count} active")
        
    except Exception as e:
        logger.error(f"Error fetching token info: {str(e)}")

# Background task in the main process to periodically fetch data
async def sync_token_info_task():
    """Periodic task to sync token info from Redis"""
    try:
        while True:
            await fetch_token_info_from_redis()
            await asyncio.sleep(10)  # Run every 10 seconds
    except asyncio.CancelledError:
        logger.info("Token info sync task cancelled")

# Start the monitoring process
def start_activity_monitor():
    """Start the activity monitoring process"""
    global background_process, stop_event
    
    # Make sure the stop event is cleared
    stop_event.clear()
    
    # Start the process
    background_process = multiprocessing.Process(
        target=activity_monitor_process,
        args=(ACTIVITY_CHECK_INTERVAL,),
        daemon=True  # Set as daemon so it exits when the main process exits
    )
    background_process.start()
    logger.info(f"Started activity monitor process (PID: {background_process.pid})")

# Stop the monitoring process gracefully
def stop_activity_monitor():
    """Stop the activity monitoring process"""
    global background_process, stop_event
    
    if background_process and background_process.is_alive():
        logger.info(f"Stopping activity monitor process (PID: {background_process.pid})")
        
        # Set the stop event to signal the process
        stop_event.set()
        
        # Wait for process to terminate (with timeout)
        background_process.join(timeout=5)
        
        # If still alive, terminate more forcefully
        if background_process.is_alive():
            logger.info("Activity monitor process didn't stop gracefully, terminating...")
            background_process.terminate()
            background_process.join(timeout=2)
            
            # Last resort: kill
            if background_process.is_alive():
                logger.warning("Forcefully killing activity monitor process")
                # On Unix systems, SIGKILL forces termination
                os.kill(background_process.pid, signal.SIGKILL)
        
        logger.info("Activity monitor process stopped")

# Ensure process is stopped on exit
atexit.register(stop_activity_monitor)

# Startup event to start the background process
@app.on_event("startup")
async def startup_event():
    """Start necessary background tasks"""
    # Start the activity monitor process
    start_activity_monitor()
    
    # Start the data sync task
    asyncio.create_task(sync_token_info_task())
    
    logger.info("Server startup complete")

# Shutdown event
@app.on_event("shutdown")
async def shutdown_event():
    """Cleanup on shutdown"""
    logger.info("Server shutting down")
    stop_activity_monitor()

# Signal handler
def handle_exit_signal(signum, frame):
    """Handle exit signals like SIGINT (Ctrl+C) and SIGTERM"""
    logger.info(f"Received signal {signum}, shutting down...")
    stop_activity_monitor()
    sys.exit(0)

# 恢复 WebSocket 端点
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    connected_clients.append(websocket)
    logger.info(f"新的WebSocket连接建立，当前连接数: {len(connected_clients)}")
    
    try:
        while True:
            # Keep the connection alive and handle incoming messages
            data = await websocket.receive_text()
            try:
                # Try to parse the data as JSON
                project = json.loads(data)
                logger.info(f"收到项目数据: {project.get('name', 'Unknown')}")
                
                # 检查是否重复
                if is_duplicate_project(project):
                    logger.info(f"跳过重复项目: {project.get('name', 'Unknown')}")
                    continue
                
                # 标准化项目数据
                normalized_project = normalize_project(project)
                logger.info(f"标准化后的项目数据: {normalized_project}")
                
                # 广播给所有客户端
                await broadcast_to_clients({"type": "new_project", "data": normalized_project})
                
            except json.JSONDecodeError:
                logger.error("JSON解析错误")
            except Exception as e:
                logger.error(f"处理消息时出错: {e}")
                
    except Exception as e:
        logger.error(f"WebSocket错误: {e}")
        if websocket in connected_clients:
            connected_clients.remove(websocket)
            logger.info(f"WebSocket连接断开，当前连接数: {len(connected_clients)}")

# 标准化项目字段函数
def normalize_project(project: Dict[str, Any]) -> Dict[str, Any]:
    # Make a copy to avoid modifying the original
    normalized = project.copy()
    
    # Map common field variations to expected fields
    field_mappings = {
        # Common name variations
        "projectName": "name",
        "project_name": "name",
        "title": "name",
        "token_name": "name",
        "tokenName": "name",
        
        # Symbol variations
        "tokenSymbol": "symbol",
        "token_symbol": "symbol",
        "ticker": "symbol",
        
        # Description variations
        "projectDescription": "description",
        "project_description": "description",
        "desc": "description",
        "about": "description",
        
        # Logo variations
        "projectLogo": "logo",
        "project_logo": "logo",
        "image": "logo",
        "iconUrl": "logo",
        "icon_url": "logo",
        
        # Chain ID variations
        "chain": "chainId",
        "chain_id": "chainId",
        "networkId": "chainId",
        "network_id": "chainId",
        
        # Contract address variations
        "contract": "contractAddress",
        "contract_address": "contractAddress",
        "address": "contractAddress",
        "tokenAddress": "contractAddress",
        "token_address": "contractAddress",
        "tokenContract": "contractAddress"
    }
    
    # Apply field mappings
    for src_field, target_field in field_mappings.items():
        if src_field in normalized and target_field not in normalized:
            normalized[target_field] = normalized[src_field]
    
    # 进行反向检查，确保我们没有丢失任何重要字段
    if "contractAddress" not in normalized and "tokenContract" in normalized:
        normalized["contractAddress"] = normalized["tokenContract"]
    
    # Get contract address for pump.fun link updates
    token_contract = normalized.get('contractAddress', '')
    
    # Normalize links structure
    links = {}
    link_sources = [
        ("website", ["website", "websiteUrl", "website_url", "homepage", "home", "site"]),
        ("twitter", ["twitter", "twitterUrl", "twitter_url", "twitterHandle", "twitter_handle"]),
        ("telegram", ["telegram", "telegramUrl", "telegram_url", "telegramGroup", "telegram_group"]),
        ("discord", ["discord", "discordUrl", "discord_url", "discordServer", "discord_server"])
    ]
    
    for link_name, variations in link_sources:
        for var in variations:
            if var in normalized:
                links[link_name] = normalized[var]
                break
    
    # If we found links but there's no links object
    if links and "links" not in normalized:
        normalized["links"] = links
    # If there's already a links object but it's not in the right format
    elif "links" in normalized and not isinstance(normalized["links"], dict):
        normalized["links"] = {}
    
    # 更新pump.fun链接为包含合约地址的格式
    if token_contract:
        has_pump_fun_link = False
        
        # 检查links对象中的website字段
        if "links" in normalized and isinstance(normalized["links"], dict):
            if "website" in normalized["links"] and isinstance(normalized["links"]["website"], str) and "pump.fun" in normalized["links"]["website"]:
                normalized["links"]["website"] = f"https://pump.fun/coin/{token_contract}?include-nsfw=true"
                has_pump_fun_link = True
        
        # 检查单独的website字段
        if "website" in normalized and isinstance(normalized["website"], str) and "pump.fun" in normalized["website"]:
            normalized["website"] = f"https://pump.fun/coin/{token_contract}?include-nsfw=true"
            has_pump_fun_link = True
        
        # 如果contract address以pump结尾，但没有pump.fun链接，则添加一个
        if token_contract.lower().endswith('pump') and not has_pump_fun_link:
            if "links" not in normalized or not isinstance(normalized["links"], dict):
                normalized["links"] = {}
            normalized["links"]["pumpfun"] = f"https://pump.fun/coin/{token_contract}?include-nsfw=true"
    
    # Add empty string for required fields if missing to avoid frontend errors
    required_fields = ["name", "symbol", "description", "chainId", "contractAddress"]
    for field in required_fields:
        if field not in normalized:
            normalized[field] = ""
    
    # Ensure timestamp is present
    if "timestamp" not in normalized:
        normalized["timestamp"] = int(time.time() * 1000)  # Milliseconds
    
    return normalized

# 是否是重复项目检查函数
def is_duplicate_project(project: dict) -> bool:
    """检查项目是否在时间窗口内重复"""
    token_contract = project.get('tokenContract') or project.get('contractAddress')
    if not token_contract:
        return False
        
    current_time = datetime.now()
    
    # 清理过期的项目记录
    expired_tokens = [
        token for token, timestamp in recent_projects.items()
        if current_time - timestamp > TIME_WINDOW
    ]
    for token in expired_tokens:
        del recent_projects[token]
    
    # 检查是否重复
    if token_contract in recent_projects:
        logger.info(f"检测到重复项目: {token_contract}")
        return True
    
    # 记录新项目
    recent_projects[token_contract] = current_time
    return False

# 恢复根路径处理程序
@app.get("/")
async def root():
    return {"message": "WebSocket server is running"}

# 恢复API路由处理程序
@app.post("/api/meme-projects")
async def add_meme_project(project: Dict[str, Any]):
    try:
        # Print the original project data
        # print("\n=== 收到的原始项目数据 ===")
        # print("原始字段：", list(project.keys()))
        
        # 详细打印可能的合约地址字段
        contract_fields = ["tokenContract", "contractAddress", "address", "contract", "contract_address"]
        # for field in contract_fields:
        #     if field in project:
        #         print(f"原始 {field}: '{project[field]}'")
                
        
        # Normalize project fields to match frontend expectations
        normalized_project = normalize_project(project)
        
        # Generate a unique key for the project
        project_id = f"meme_project:{normalized_project.get('id', str(hash(json.dumps(normalized_project))))}"
        
        # Store project in Redis
        redis_client.set(project_id, json.dumps(normalized_project))
        
        # Add to sorted set with timestamp as score for time-based sorting
        redis_client.zadd(
            "meme_projects_by_time", 
            {project_id: float(normalized_project['timestamp'])}
        )
        
        # Broadcast to all connected WebSocket clients
        await broadcast_to_clients({"type": "new_project", "data": normalized_project})
        
        return {"status": "success", "message": "Project added successfully"}
    except Exception as e:
        print(f"Error processing project: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to process project: {str(e)}")

# 恢复获取项目列表API路由
@app.get("/api/meme-projects")
async def get_meme_projects(
    page: int = Query(1, ge=1, description="Page number, starting from 1"),
    page_size: int = Query(10, ge=1, le=100, description="Number of items per page")
):
    try:
        # Calculate start and end indices for pagination
        start = (page - 1) * page_size
        end = start + page_size - 1
        
        # Get project IDs in reverse time order (newest first)
        project_ids = redis_client.zrevrange("meme_projects_by_time", start, end)
        
        # Get total count for pagination info
        total_count = redis_client.zcard("meme_projects_by_time")
        
        # Get project details
        projects = []
        for project_id in project_ids:
            project_data = redis_client.get(project_id)
            if project_data:
                project = json.loads(project_data)
                projects.append(project)
        
        # 处理每个项目
        for project in projects:
            contract_address = project.get('contractAddress', '')
            if contract_address and contract_address.lower().endswith('pump'):
                # 获取pump.fun描述
                pump_desc = await fetch_pump_description(contract_address)
                
                if pump_desc:
                    project['pump_desc'] = pump_desc
                    # 打印包含pump_desc的完整项目信息
                    print("\n=== 添加pump_desc后的项目数据 ===")
                    print(f"名称: '{project.get('name', '没有提供名称')}'")
                    print(f"符号: '{project.get('symbol', '没有提供符号')}'")
                    print(f"合约地址: '{contract_address}'")
                    print(f"pump_desc: '{pump_desc}'")
                    print("完整项目数据:")
                    pprint(project)
                    print("=================================\n")
        
        return {
            "status": "success", 
            "projects": projects,
            "pagination": {
                "page": page,
                "page_size": page_size,
                "total_count": total_count,
                "total_pages": (total_count + page_size - 1) // page_size
            }
        }
    except Exception as e:
        import traceback
        logger.error(f"获取项目列表时出错: {e}")
        logger.error(f"错误详情: {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"Failed to get projects: {str(e)}")

# 添加获取活跃项目API路由
@app.get("/api/active-projects")
async def get_active_projects():
    """Return list of currently active projects"""
    try:
        # 将活跃项目转换为列表格式
        projects_list = [
            {"mint_address": mint_address, "token_info": token_info} 
            for mint_address, token_info in active_projects.items()
        ]
        
        # 按照回复数降序排序
        projects_list.sort(
            key=lambda p: (p["token_info"].get("reply_count", 0) if p["token_info"] else 0),
            reverse=True
        )
        
        return {
            "status": "success",
            "active_projects": projects_list
        }
    except Exception as e:
        logger.error(f"获取活跃项目列表时出错: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to get active projects: {str(e)}")

if __name__ == "__main__":
    import uvicorn
    
    # Register signal handlers
    signal.signal(signal.SIGINT, handle_exit_signal)  # Ctrl+C
    signal.signal(signal.SIGTERM, handle_exit_signal)  # kill command
    
    # Configure uvicorn
    config = uvicorn.Config(
        "main:app", 
        host="0.0.0.0", 
        port=5001, 
        reload=False,
        log_level="info",
        access_log=False,  # 禁用HTTP请求访问日志
        workers=1
    )
    
    # Run the server
    try:
        server = uvicorn.Server(config)
        server.run()
    except KeyboardInterrupt:
        logger.info("Keyboard interrupt received")
    finally:
        # Make sure process is stopped
        stop_activity_monitor()
        logger.info("Server shutdown complete") 