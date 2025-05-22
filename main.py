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
import re
# Import the get_token_info function from pumpfun_comments.py
from pumpfun_comments import get_token_info
# Import Twitter utils functions
from utils.xutils import get_previous_name, get_mentions_ca, get_twitter_labels, get_twitter_user_followers

# 禁用urllib3的InsecureRequestWarning警告
from urllib3.exceptions import InsecureRequestWarning
warnings.filterwarnings('ignore', category=InsecureRequestWarning)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
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

# 可配置的活跃判断参数
PROJECT_WAIT_TIME = int(os.getenv("PROJECT_WAIT_TIME", "180"))  # 默认3分钟 (180秒)
ATH_MARKET_CAP_THRESHOLD = float(os.getenv("ATH_MARKET_CAP_THRESHOLD", "40"))  # 默认40
ACTIVITY_CHECK_INTERVAL = int(os.getenv("ACTIVITY_CHECK_INTERVAL", "180"))  # 默认3分钟检查一次活跃状态
INACTIVE_THRESHOLD = int(os.getenv("INACTIVE_THRESHOLD", "3"))  # 连续几次判断不活跃后剔除，默认3次
MAX_PROJECT_AGE = int(os.getenv("MAX_PROJECT_AGE", "600"))  # 项目最大年龄，超过则不再判断，默认10分钟(600秒)
TWEET_REPEAT_INTERVAL = int(os.getenv("TWEET_REPEAT_INTERVAL", "300"))  # 5分钟

# Initialize Redis client
redis_client = redis.Redis(
    host=REDIS_HOST,
    port=REDIS_PORT,
    db=REDIS_DB,
    password=REDIS_PASSWORD,
    decode_responses=True
)

twitter_redis = redis.Redis(
    host=REDIS_HOST,
    port=REDIS_PORT,
    db=3,
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
project_first_seen: Dict[str, float] = {}  # mint_address -> timestamp of first seen
ACTIVITY_THRESHOLD = 3  # Need 3 consecutive activity records to determine activity

# 新增字典来跟踪项目已经被第一级判断过
projects_already_classified: Set[str] = set()  # 存储已经被第一级判断过的项目
# 新增字典来跟踪项目连续不活跃次数
project_inactive_count: Dict[str, int] = {}  # mint_address -> 连续不活跃次数

# Process control
background_process = None
stop_event = multiprocessing.Event()

# 添加一个集合来跟踪被手动删除的项目
manually_removed_projects: Set[str] = set()  # 存储被手动删除的mint_address

# 全局变量: 控制是否获取X用户信息以及获取范围
FETCH_X_INFO = "all"  # 可选值: "none", "launchacoin", "all"

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
    检查项目是否活跃基于新的标准:
    
    一级列表判断(只判断一次):
    - 项目存在时间必须超过 PROJECT_WAIT_TIME 秒
    - ath_market_cap 必须大于 ATH_MARKET_CAP_THRESHOLD
    - 判断一次后无论结果如何，都不再进入一级判断
    
    二级列表判断:
    - 项目必须在 ath_market_cap、reply_count 有增长或是 is_currently_live 为 True
    - 连续不活跃 INACTIVE_THRESHOLD 次后才会被剔除
    
    返回一个元组 (is_active, reason)
    """
    # 首先检查项目是否被手动删除
    if mint_address in manually_removed_projects:
        return False, "Manually removed by user"
        
    if mint_address not in project_history:
        return False, "No history"
    
    history = project_history[mint_address]
    if not history:
        return False, "Empty history"
    
    current_time = time.time()
    latest_record = history[-1]
    
    # 检查项目是否已在活跃列表中
    is_already_active = mint_address in active_projects
    
    # 情况1: 项目未经过一级判断
    if mint_address not in projects_already_classified and not is_already_active:
        # 第一级判断逻辑
        
        # 检查项目是否已经存在足够长时间
        first_seen_time = project_first_seen.get(mint_address, current_time)
        time_passed = current_time - first_seen_time
        
        if time_passed < PROJECT_WAIT_TIME:
            return False, f"Too new, only {time_passed:.1f}s old"
        
        # 检查ath_market_cap是否超过阈值
        ath_market_cap = latest_record.get('ath_market_cap')
        if not ath_market_cap or ath_market_cap < ATH_MARKET_CAP_THRESHOLD:
            # 添加到已分类集合，以后不再一级判断
            projects_already_classified.add(mint_address)
            # 保存已分类项目到Redis
            redis_client.sadd("projects_already_classified", mint_address)
            return False, f"ath_market_cap {ath_market_cap} below threshold {ATH_MARKET_CAP_THRESHOLD}"
        
        # 项目通过一级判断，添加到已分类集合
        projects_already_classified.add(mint_address)
        # 保存已分类项目到Redis
        redis_client.sadd("projects_already_classified", mint_address)
        # 设置初始不活跃计数为0
        project_inactive_count[mint_address] = 0
        return True, f"New active: ath_market_cap {ath_market_cap} > {ATH_MARKET_CAP_THRESHOLD}"
    
    # 情况2: 项目已经通过一级判断且在活跃列表中
    elif is_already_active:
        # 第二级判断逻辑: 对已在活跃列表的项目
        
        # 需要至少2条记录才能比较变化
        if len(history) < 2:
            # 如果只有一条记录但ath_market_cap超过阈值，保持活跃
            ath_market_cap = latest_record.get('ath_market_cap')
            if ath_market_cap and ath_market_cap >= ATH_MARKET_CAP_THRESHOLD:
                # 重置不活跃计数
                project_inactive_count[mint_address] = 0
                return True, f"Single record with high ath_market_cap: {ath_market_cap}"
            
            # 增加不活跃计数
            project_inactive_count[mint_address] = project_inactive_count.get(mint_address, 0) + 1
            
            # 检查是否达到不活跃阈值
            if project_inactive_count[mint_address] >= INACTIVE_THRESHOLD:
                return False, f"Not enough history records to compare after {project_inactive_count[mint_address]} checks"
            return True, f"Keeping active ({project_inactive_count[mint_address]}/{INACTIVE_THRESHOLD} inactive checks)"
        
        # 获取上一条记录和当前记录
        previous_record = history[-2]
        
        # 检查ath_market_cap是否增加
        current_ath = latest_record.get('ath_market_cap', 0)
        previous_ath = previous_record.get('ath_market_cap', 0)
        if current_ath > previous_ath:
            # 重置不活跃计数
            project_inactive_count[mint_address] = 0
            return True, f"ath_market_cap increased: {previous_ath} -> {current_ath}"
        
        # 检查reply_count是否增加
        current_replies = latest_record.get('reply_count', 0)
        previous_replies = previous_record.get('reply_count', 0)
        if current_replies > previous_replies:
            # 重置不活跃计数
            project_inactive_count[mint_address] = 0
            return True, f"reply_count increased: {previous_replies} -> {current_replies}"
        
        # 检查is_currently_live状态
        if latest_record.get('is_currently_live', False):
            # 重置不活跃计数
            project_inactive_count[mint_address] = 0
            return True, "is_currently_live is True"
        
        # 增加不活跃计数
        project_inactive_count[mint_address] = project_inactive_count.get(mint_address, 0) + 1
        
        # 如果不活跃计数未达到阈值，保持活跃
        if project_inactive_count[mint_address] < INACTIVE_THRESHOLD:
            return True, f"Keeping active ({project_inactive_count[mint_address]}/{INACTIVE_THRESHOLD} inactive checks)"
        
        # 达到不活跃阈值，从活跃列表中剔除
        return False, f"No activity increase after {INACTIVE_THRESHOLD} consecutive checks"
    
    # 情况3: 项目已经过一级判断但不在活跃列表中
    else:
        # 已分类但不在活跃列表的项目，不再重新加入活跃列表
        return False, "Already classified as inactive"

# Function to fetch activity data in a separate process
def activity_monitor_process(interval=60):
    """
    Separate process for monitoring activity without blocking the main server
    """
    # Set up logging for this process
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    process_logger = logging.getLogger("activity_monitor")
    process_logger.info("Activity monitor process started")
    process_logger.info(f"Using parameters: WAIT_TIME={PROJECT_WAIT_TIME}s, ATH_THRESHOLD={ATH_MARKET_CAP_THRESHOLD}, CHECK_INTERVAL={ACTIVITY_CHECK_INTERVAL}s")
    
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
                    
                    # 记录项目首次见到的时间
                    current_time = time.time()
                    if mint_address not in project_first_seen:
                        project_first_seen[mint_address] = current_time
                        # 将首次见到时间存入Redis以便在重启后恢复
                        process_redis.hset("project_first_seen", mint_address, current_time)
                    
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
                            "timestamp": current_time
                        })
                    )
                    
                    # Set a short expiry so Redis cleans up old data
                    process_redis.expire(f"token_info:{mint_address}", 3600)  # 1 hour
                    
                except Exception as e:
                    process_logger.error(f"Error processing project {project_id} 1: {str(e)}")
            
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
        
        # 从Redis加载首次见到时间记录
        first_seen_data = redis_client.hgetall("project_first_seen")
        for mint, timestamp in first_seen_data.items():
            project_first_seen[mint] = float(timestamp)
        
        active_count = 0
        processed_count = 0
        skipped_by_age_count = 0
        
        for key in token_info_keys:
            try:
                # Extract mint address from key
                mint_address = key.split(':', 1)[1]
                processed_count += 1
                
                # 如果项目已被手动删除，跳过处理
                if mint_address in manually_removed_projects:
                    continue
                    
                # Get token info
                token_info_data = redis_client.get(key)
                if not token_info_data:
                    continue
                
                data = json.loads(token_info_data)
                token_info = data.get('token_info', {})
                
                # 检查项目创建时间，如果超过最大年龄且不在活跃列表中，跳过处理
                current_time = time.time()
                created_timestamp = token_info.get('created_timestamp')
                
                if created_timestamp:
                    try:
                        # 尝试将created_timestamp转换为秒级时间戳
                        # 如果是毫秒时间戳，则转换为秒
                        if isinstance(created_timestamp, str):
                            created_timestamp = float(created_timestamp)
                        elif isinstance(created_timestamp, int) and created_timestamp > 1000000000000:  # 可能是毫秒时间戳
                            created_timestamp = created_timestamp / 1000
                            
                        project_age = current_time - created_timestamp
                        
                        # 如果项目年龄超过最大限制且未分类，直接跳过
                        if project_age > MAX_PROJECT_AGE and mint_address not in projects_already_classified and mint_address not in active_projects:
                            logger.debug(f"跳过处理过旧的项目 {mint_address}: 年龄 {project_age:.1f}秒 > {MAX_PROJECT_AGE}秒")
                            skipped_by_age_count += 1
                            
                            # 将项目标记为已分类，防止未来再次处理
                            projects_already_classified.add(mint_address)
                            redis_client.sadd("projects_already_classified", mint_address)
                            continue
                    except (ValueError, TypeError) as e:
                        logger.warning(f"无法解析项目 {mint_address} 的创建时间 '{created_timestamp}': {e}")
                
                # Add to project history
                if mint_address not in project_history:
                    project_history[mint_address] = []
                
                # Add new token info to history
                project_history[mint_address].append(token_info)
                
                # Keep only the last 5 history records
                if len(project_history[mint_address]) > 5:
                    project_history[mint_address] = project_history[mint_address][-5:]
                
                # # Check if project is active
                # # was_active = mint_address in active_projects
                # # is_active, reason = is_project_active(mint_address)
                
                # if is_active:
                #     active_count += 1
                    
                #     # 检查是否是新活跃的项目
                #     if not was_active:
                #         # 输出详细的新活跃项目信息
                #         logger.info(f"新的活跃项目 - {mint_address}:")
                #         logger.info(f"  名称: {token_info.get('name', '未知')}")
                #         logger.info(f"  符号: {token_info.get('symbol', '未知')}")
                #         logger.info(f"  创建时间: {token_info.get('created_timestamp', '未知')}")
                #         logger.info(f"  回复数: {token_info.get('reply_count', '未知')}")
                #         logger.info(f"  最高市值: {token_info.get('ath_market_cap', '未知')}")
                #         logger.info(f"  实时状态: {token_info.get('is_currently_live', False)}")
                #         logger.info(f"  活跃原因: {reason}")
                        
                #         if 'image_uri' in token_info:
                #             logger.info(f"  图片地址: {token_info['image_uri']}")
                        
                #         if 'description' in token_info:
                #             # 截取描述前100个字符，如果描述过长则添加省略号
                #             desc = token_info['description']
                #             if len(desc) > 100:
                #                 desc = desc[:100] + "..."
                #             logger.info(f"  描述: {desc}")
                    
                #     # Store in active projects if not already broadcast
                #     active_projects[mint_address] = token_info
                    
                #     # Broadcast if not already broadcast
                #     if mint_address not in active_project_broadcasts:
                #         await broadcast_active_project(mint_address, token_info)
                # else:
                #     # If was active but now inactive
                #     if mint_address in active_projects:
                #         logger.info(f"项目变为非活跃状态 - {mint_address}: {token_info.get('name', '未知')}, 原因: {reason}")
                #         del active_projects[mint_address]
                        
                #         # Broadcast inactive status if it was previously broadcast as active
                #         if mint_address in active_project_broadcasts:
                #             await broadcast_inactive_project(mint_address)
                
            except Exception as e:
                logger.error(f"Error processing token info for {key}: {str(e)}")
        
        logger.info(f"Found {processed_count} projects, {active_count} active, {skipped_by_age_count} skipped by age")
        
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
    global FETCH_X_INFO
    
    # 显示活跃判断配置
    logger.info(f"Using activity parameters:")
    logger.info(f"- Wait time: {PROJECT_WAIT_TIME} seconds")
    logger.info(f"- ATH market cap threshold: {ATH_MARKET_CAP_THRESHOLD}")
    logger.info(f"- Activity check interval: {ACTIVITY_CHECK_INTERVAL} seconds")
    logger.info(f"- Inactive threshold: {INACTIVE_THRESHOLD} consecutive checks")
    logger.info(f"- Max project age: {MAX_PROJECT_AGE} seconds")
    
    # 从Redis加载手动删除的项目列表
    removed_projects = redis_client.smembers("manually_removed_projects")
    for mint_address in removed_projects:
        manually_removed_projects.add(mint_address)
    logger.info(f"Loaded {len(manually_removed_projects)} manually removed projects")
    
    # 从Redis加载已分类的项目列表
    classified_projects = redis_client.smembers("projects_already_classified")
    for mint_address in classified_projects:
        projects_already_classified.add(mint_address)
    logger.info(f"Loaded {len(projects_already_classified)} previously classified projects")
    
    # 从Redis加载X信息获取范围设置
    x_info_range = redis_client.get("fetch_x_info_range")
    if x_info_range:
        FETCH_X_INFO = x_info_range
        logger.info(f"已从Redis加载X信息获取范围设置: {FETCH_X_INFO}")
    
    # Start the activity monitor process
    # start_activity_monitor()
    
    # Start the data sync task
    # asyncio.create_task(sync_token_info_task())
    
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
        "tokenContract": "contractAddress",
        "from": "from"
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
    required_fields = ["name", "symbol", "description", "chainId", "contractAddress", "from"]
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
        # Normalize project fields to match frontend expectations
        normalized_project = normalize_project(project)
        name = normalized_project["name"]
        
        # 检查是否需要获取Twitter信息
        should_fetch_x_info = False
        twitter_handle = None
        
        if FETCH_X_INFO == "all":
            should_fetch_x_info = True
        elif FETCH_X_INFO == "launchacoin" and normalized_project.get("from") == "launchacoin":
            should_fetch_x_info = True
        
        # 如果需要获取Twitter信息，尝试从项目链接或hoverTweet中提取Twitter用户名
        if should_fetch_x_info:
            # 尝试从links中提取
            if "links" in normalized_project and isinstance(normalized_project["links"], dict):
                for key, value in normalized_project["links"].items():
                    if isinstance(value, str) and ("twitter.com" in value or "x.com" in value):
                        twitter_handle = extract_twitter_handle(value)
                        if twitter_handle:
                            break
            
            # 尝试从hoverTweet中提取
            if not twitter_handle and "hoverTweet" in normalized_project and isinstance(normalized_project["hoverTweet"], str):
                twitter_handle = extract_twitter_handle(normalized_project["hoverTweet"])
            
            # 如果找到Twitter用户名，获取信息
            if twitter_handle:
                # logger.info(f"找到Twitter用户名: {twitter_handle}, 正在获取信息")
                twitter_info = get_twitter_complete_info(twitter_handle)
                
                # 将Twitter信息添加到项目中
                if twitter_info:
                    normalized_project["twitter_info"] = twitter_info
        
        # ========== 新增：推文去重与时间阈值判断 ==========
        tweet_info = None
        # 检查 links
        if "links" in normalized_project and isinstance(normalized_project["links"], dict):
            for v in normalized_project["links"].values():
                if isinstance(v, str):
                    tweet_info = extract_tweet_info(v)
                    if tweet_info:
                        break
        # 检查 hoverTweet
        if not tweet_info and "hoverTweet" in normalized_project and isinstance(normalized_project["hoverTweet"], str):
            tweet_info = extract_tweet_info(normalized_project["hoverTweet"])
        if tweet_info:
            user, tweet_id = tweet_info
            tweet_key = f"tweet:{user}:{tweet_id}"
            now = int(time.time())
            last_time = redis_client.get(tweet_key)
            if last_time:
                last_time = int(last_time)
                if now - last_time > TWEET_REPEAT_INTERVAL:
                    name = normalized_project["name"]
                    logger.info(f"推文 {tweet_key} 距离上次出现已超过阈值({TWEET_REPEAT_INTERVAL}s)，本次不推送 {name}")
                    redis_client.set(tweet_key, now)
                    return {"status": "ignored", "message": f"Tweet {tweet_key} ignored due to repeat interval"}
            else:
                redis_client.set(tweet_key, now)
        # ========== 新增结束 ==========
        
        # Generate a unique key for the project
        project_id = f"meme_project:{normalized_project.get('id', str(hash(json.dumps(normalized_project))))}"
        
        # Store project in Redis
        redis_client.set(project_id, json.dumps(normalized_project))
        
        # Add to sorted set with timestamp as score for time-based sorting
        redis_client.zadd(
            "meme_projects_by_time", 
            {project_id: float(normalized_project['timestamp'])}
        )

        if normalized_project["from"] == "launchacoin":
            twitter_redis.sadd(f"twitter:{twitter_handle}:mentioned_contracts_set", *set([normalized_project["contractAddress"]]))

        # normalized_project["twitter_info"][""] = redis_client.smembers(f"launchacoin_deployers:{twitter_handle}")
        
        # Broadcast to all connected WebSocket clients
        await broadcast_to_clients({"type": "new_project", "data": normalized_project})
        
        return {"status": "success", "message": "Project added successfully"}
    except Exception as e:
        print(f"Error processing project 2: {e}")
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

# 添加API端点用于手动删除活跃项目
@app.delete("/api/active-projects/{mint_address}")
async def remove_active_project(mint_address: str):
    """Manually remove a project from the active list"""
    try:
        # 检查项目是否在活跃列表中
        if mint_address not in active_projects:
            raise HTTPException(status_code=404, detail=f"Project with mint address {mint_address} not found in active list")
            
        # 从活跃列表中删除项目
        project_name = active_projects[mint_address].get("name", "Unknown")
        del active_projects[mint_address]
        
        # 添加到手动删除集合
        manually_removed_projects.add(mint_address)
        
        # 将手动删除的项目保存到Redis以便服务重启后保持状态
        redis_client.sadd("manually_removed_projects", mint_address)
        
        # 广播项目不活跃状态
        if mint_address in active_project_broadcasts:
            await broadcast_inactive_project(mint_address)
            
        logger.info(f"项目已被手动删除 - {mint_address}: {project_name}")
        
        return {"status": "success", "message": f"Project {project_name} ({mint_address}) has been removed from active list"}
    except Exception as e:
        logger.error(f"删除活跃项目出错: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to remove active project: {str(e)}")

# 新增API端点: 设置X信息获取范围
@app.post("/api/set-xinfo-range")
async def set_xinfo_range(range: str):
    """设置X用户信息获取的范围"""
    global FETCH_X_INFO
    
    # 验证输入值
    if range not in ["none", "launchacoin", "all"]:
        raise HTTPException(status_code=400, detail="Invalid range value. Must be 'none', 'launchacoin', or 'all'")
    
    # 设置全局变量
    FETCH_X_INFO = range
    
    # 保存设置到Redis以便服务重启后恢复
    redis_client.set("fetch_x_info_range", range)
    
    logger.info(f"已设置X信息获取范围为: {range}")
    return {"status": "success", "message": f"X info fetch range set to: {range}"}

# 提取Twitter用户名函数
def extract_twitter_handle(url: str) -> Optional[str]:
    """从Twitter URL中提取用户名"""
    # 处理 x.com 或 twitter.com 的URL格式
    pattern = r'(?:https?:\/\/)?(?:www\.)?(?:twitter\.com|x\.com)\/([a-zA-Z0-9_]+)(?:\/status\/\d+)?'
    match = re.search(pattern, url)
    if match:
        return match.group(1)
    return None

# 获取Twitter完整信息函数
def get_twitter_complete_info(username: str) -> Dict[str, Any]:
    """获取Twitter用户的完整信息，并行执行三个函数并存储结果到Redis DB 3"""
    result = {}
    
    # 记录日志
    # logger.info(f"正在获取Twitter用户信息: {username}")
    
    try:
        
        # 获取当前时间戳
        current_time = time.time()
        
        # 先检查Redis是否已有完整信息
        cached_info = twitter_redis.hgetall(f"twitter:{username}:complete_info")
        
        # 如果有缓存数据且数据不超过1天，直接返回
        if cached_info and "data" in cached_info and "timestamp" in cached_info:
            cached_timestamp = float(cached_info["timestamp"])
            if current_time - cached_timestamp < 86400:  # 数据不超过1天
                logger.info(f"使用Redis缓存中的Twitter用户信息: {username} (缓存时间: {int(current_time - cached_timestamp)}秒)")
                try:
                    return json.loads(cached_info["data"])
                except Exception as e:
                    logger.error(f"解析Redis缓存的Twitter数据时出错: {str(e)}")
                    # 解析失败，继续请求新数据
        
        # 创建并行执行的线程
        import concurrent.futures
        
        def fetch_previous_name():
            try:
                prev_name_info = get_previous_name(username)
                # 存储到Redis
                if prev_name_info:
                    twitter_redis.hset(
                        f"twitter:{username}:previous_names",
                        mapping={
                            "data": json.dumps(prev_name_info),
                            "timestamp": current_time
                        }
                    )
                return prev_name_info
            except Exception as e:
                logger.error(f"获取曾用名信息出错: {str(e)}")
                return None
        
        def fetch_mentions_ca():
            mentions_ca_info = None
            try:
                mentions_ca_info = get_mentions_ca(username)
                if mentions_ca_info["success"]:
                    if "data" in mentions_ca_info:
                        mentioned_contracts_set = set(mentions_ca_info["data"])
                        if mentioned_contracts_set:
                            twitter_redis.sadd(f"twitter:{username}:mentioned_contracts_set", *mentioned_contracts_set)
                return mentions_ca_info 
            except Exception as e:
                logger.error(f"获取提及CA信息出错: {str(e)}, {username}, {mentions_ca_info}")
                return None
        
        def fetch_followers_info():
            try:
                followers_info = get_twitter_user_followers(username)
                # 存储到Redis
                if followers_info:
                    twitter_redis.hset(
                        f"twitter:{username}:followers_info",
                        mapping={
                            "data": json.dumps(followers_info),
                            "timestamp": current_time
                        }
                    )
                return followers_info
            except Exception as e:
                logger.error(f"获取关注者信息出错: {str(e)}")
                return None
        
        # 使用线程池并行执行三个函数
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
            # 提交任务到线程池
            prev_name_future = executor.submit(fetch_previous_name)
            mentions_ca_future = executor.submit(fetch_mentions_ca)
            followers_future = executor.submit(fetch_followers_info)
            
            # 等待所有任务完成并获取结果
            prev_name_info = prev_name_future.result()
            mentions_ca_info = mentions_ca_future.result()
            followers_info = followers_future.result()
        
        # 处理曾用名信息
        if prev_name_info and prev_name_info.get("success"):
            result["previous_names"] = prev_name_info.get("data", [])
        
        # 处理提及的CA信息
        if mentions_ca_info and mentions_ca_info.get("success"):
            result["mentioned_contracts"] = len(mentions_ca_info.get("data", []))
        
        # 处理关注者信息
        if followers_info and followers_info.get("code") == 200:
            kol_data = followers_info.get("data", {}).get("kolFollow", {})
            result["kols"] = {
                "globalKolFollowersCount": kol_data.get("globalKolFollowersCount", 0),
                "cnKolFollowersCount": kol_data.get("cnKolFollowersCount", 0),
                "topKolFollowersCount": kol_data.get("topKolFollowersCount", 0),
                "globalKolFollowers": [f.get("username") for f in kol_data.get("globalKolFollowers", [])],
                "cnKolFollowers": [f.get("username") for f in kol_data.get("cnKolFollowers", [])],
                "topKolFollowers": [f.get("username") for f in kol_data.get("topKolFollowers", [])]
            }
        
        # 存储完整的合并结果到Redis
        twitter_redis.hset(
            f"twitter:{username}:complete_info",
            mapping={
                "data": json.dumps(result),
                "timestamp": current_time
            }
        )
        
        # 返回合并的结果
        return result
        
    except Exception as e:
        logger.error(f"获取Twitter用户信息时出错: {str(e)}")
        return {"error": str(e)}

def extract_tweet_info(text: str) -> Optional[Tuple[str, str]]:
    """
    从文本中提取 x.com/twitter.com 推文的 user 和 tweet_id
    """
    pattern = r"(?:https?://)?(?:www\.)?(?:x\.com|twitter\.com)/([a-zA-Z0-9_]+)/status/(\d+)"
    match = re.search(pattern, text)
    if match:
        return match.group(1), match.group(2)
    return None

if __name__ == "__main__":
    import uvicorn
    
    # Register signal handlers
    signal.signal(signal.SIGINT, handle_exit_signal)  # Ctrl+C
    signal.signal(signal.SIGTERM, handle_exit_signal)  # kill command
    
    # Configure uvicorn
    config = uvicorn.Config(
        "main:app", 
        host="192.168.1.2", 
        port=5001, 
        reload=False,
        log_level="info",
        access_log=False,  # 禁用HTTP请求访问日志
        workers=8
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
