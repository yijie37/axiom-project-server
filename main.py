from fastapi import FastAPI, WebSocket, HTTPException, Depends, Query
from fastapi.middleware.cors import CORSMiddleware
import redis
import json
import os
import time
from dotenv import load_dotenv
from typing import List, Dict, Any, Set
import asyncio
from pprint import pprint
from datetime import datetime, timedelta
import logging
import requests
from bs4 import BeautifulSoup
import aiohttp
import uuid

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
                # 打印包含pump_desc的完整项目信息
                print("\n=== 添加pump_desc后的项目数据 ===")
                print(f"名称: '{project.get('name', '没有提供名称')}'")
                print(f"符号: '{project.get('symbol', '没有提供符号')}'")
                print(f"合约地址: '{contract_address}'")
                print(f"pump_desc: '{pump_desc}'")
                print("完整项目数据:")
                pprint(project)
                print("=================================\n")
    
    # 记录广播状态
    clients_count = len(connected_clients)
    if clients_count > 0:
        logger.info(f"广播消息到 {clients_count} 个连接的客户端")
    
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

# WebSocket endpoint
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    connected_clients.append(websocket)
    
    try:
        while True:
            # Keep the connection alive
            await websocket.receive_text()
    except Exception:
        # Remove client on disconnect
        if websocket in connected_clients:
            connected_clients.remove(websocket)

# Normalize project fields to match frontend expectations
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

# API endpoint to receive meme projects from the browser plugin
@app.post("/api/meme-projects")
async def add_meme_project(project: Dict[str, Any]):
    try:
        # Print the original project data
        print("\n=== 收到的原始项目数据 ===")
        print("原始字段：", list(project.keys()))
        
        # 详细打印可能的合约地址字段
        contract_fields = ["tokenContract", "contractAddress", "address", "contract", "contract_address"]
        for field in contract_fields:
            if field in project:
                print(f"原始 {field}: '{project[field]}'")
                
        pprint(project)
        print("===============================\n")
        
        # Normalize project fields to match frontend expectations
        normalized_project = normalize_project(project)
        
        # Generate a unique key for the project
        project_id = f"meme_project:{normalized_project.get('id', str(hash(json.dumps(normalized_project))))}"
        
        # Print normalized project information
        print("\n=== 标准化后的项目数据 ===")
        print("标准化后字段：", list(normalized_project.keys()))
        print(f"项目ID: {project_id}")
        print(f"名称: '{normalized_project.get('name', '没有提供名称')}'")
        print(f"符号: '{normalized_project.get('symbol', '没有提供符号')}'")
        print(f"合约地址: '{normalized_project.get('contractAddress', '没有合约地址')}'")
        print(f"时间戳: {normalized_project['timestamp']} ({time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(normalized_project['timestamp']/1000))})")
        print("完整的标准化项目:")
        pprint(normalized_project)
        print("========================\n")
            
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

# API endpoint to get recent projects with pagination
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

# 配置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

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

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    connected_clients.append(websocket)
    logger.info(f"新的WebSocket连接建立，当前连接数: {len(connected_clients)}")
    
    try:
        while True:
            data = await websocket.receive_text()
            try:
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
                
    except WebSocketDisconnect:
        connected_clients.remove(websocket)
        logger.info(f"WebSocket连接断开，当前连接数: {len(connected_clients)}")
    except Exception as e:
        logger.error(f"WebSocket错误: {e}")
        connected_clients.remove(websocket)

@app.get("/")
async def root():
    return {"message": "WebSocket server is running"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=5001, reload=True) 