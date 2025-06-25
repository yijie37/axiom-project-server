from fastapi import FastAPI, WebSocket, HTTPException, Depends, Query
from fastapi.middleware.cors import CORSMiddleware
import redis
import json
import os
import time
import signal
import sys
import multiprocessing
from dotenv import load_dotenv
from typing import List, Dict, Any, Set
import asyncio
from pprint import pprint
from datetime import datetime, timedelta
import logging
import warnings
import traceback
# from pumpfun_comments import get_token_info
from utils.twitter_utils import get_twitter_user
from utils.twitter_user_info import get_twitter_community_info
from utils.common import extract_twitter_handle, extract_tweet_info, fetch_pump_description, fetch_bonk_description
from contextlib import asynccontextmanager
import traceback

# 禁用urllib3的InsecureRequestWarning警告
from urllib3.exceptions import InsecureRequestWarning
warnings.filterwarnings('ignore', category=InsecureRequestWarning)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    handlers=[
        logging.StreamHandler(),  # Output to console
        logging.FileHandler('main.log')  # Also save to file
    ]
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

# Redis keys for project views
PROJECT_VIEWS_SET = "project_views:set"  # 存储所有project_view的ID
PROJECT_VIEW_HASH = "project_view:"  # 存储具体的project_view数据
PROJECT_VIEWS_BY_TIME = "project_views:by_time"  # 按时间排序的project_view集合

# 可配置的活跃判断参数
PROJECT_WAIT_TIME = int(os.getenv("PROJECT_WAIT_TIME", "180"))  # 默认3分钟 (180秒)
ATH_MARKET_CAP_THRESHOLD = float(os.getenv("ATH_MARKET_CAP_THRESHOLD", "40"))  # 默认40
ACTIVITY_CHECK_INTERVAL = int(os.getenv("ACTIVITY_CHECK_INTERVAL", "180"))  # 默认3分钟检查一次活跃状态
INACTIVE_THRESHOLD = int(os.getenv("INACTIVE_THRESHOLD", "3"))  # 连续几次判断不活跃后剔除，默认3次
MAX_PROJECT_AGE = int(os.getenv("MAX_PROJECT_AGE", "600"))  # 项目最大年龄，超过则不再判断，默认10分钟(600秒)
TWEET_REPEAT_INTERVAL = int(os.getenv("TWEET_REPEAT_INTERVAL", "300"))  # 5分钟

# 统计数据的Redis key
STATS_TOTAL_PROJECTS = "stats:total_projects"
STATS_YOUTUBE_PROJECTS = "stats:youtube_projects"
STATS_TIKTOK_PROJECTS = "stats:tiktok_projects"
STATS_INSTAGRAM_PROJECTS = "stats:instagram_projects"
STATS_NO_SOCIAL_PROJECTS = "stats:no_social_projects"
STATS_NOT_BROADCAST_PROJECTS = "stats:not_broadcast_projects"  # 新增：不广播的项目数
STATS_TWITTER_API_CALLS = "stats:twitter_api_calls"  # 新增：Twitter API调用次数

# Initialize Redis client
redis_client = redis.Redis(
    host=REDIS_HOST,
    port=REDIS_PORT,
    db=REDIS_DB,
    password=REDIS_PASSWORD,
    decode_responses=True
)

# Initialize Twitter Redis client (DB 7)
twitter_redis = redis.Redis(
    host=REDIS_HOST,
    port=REDIS_PORT,
    db=7,
    password=REDIS_PASSWORD,
    decode_responses=True
)

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan event handler for FastAPI"""
    # Startup
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
    
    # Start retry task
    retry_task_handle = asyncio.create_task(retry_task())
    logger.info("Started message retry task")
    
    logger.info("Server startup complete")
    
    yield
    
    # Shutdown
    logger.info("Server shutting down")
    
    # Cancel retry task
    retry_task_handle.cancel()
    try:
        await retry_task_handle
    except asyncio.CancelledError:
        logger.info("Retry task cancelled successfully")
    
    # 设置停止事件
    stop_event.set()
    
    # 等待所有工作进程结束
    for process in worker_processes:
        if process.is_alive():
            try:
                # 直接使用kill信号
                process.kill()
                process.join(timeout=1)  # 给进程1秒来退出
            except Exception as e:
                logger.error(f"Error terminating process {process.pid}: {e}")
    
    logger.info("All worker processes terminated")

# Initialize FastAPI app
app = FastAPI(title="Axiom Project Server", lifespan=lifespan)

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 允许所有源访问
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Store connected WebSocket clients
connected_clients: List[WebSocket] = []

# Store connected WebSocket clients with their message tracking
client_message_tracking: Dict[WebSocket, Dict[str, Any]] = {}  # WebSocket -> tracking info

# Global message ID counter
message_id_counter = 0
message_id_lock = asyncio.Lock()

# Pending acknowledgments: message_id -> {client: (timestamp, retry_count)}
pending_acks: Dict[str, Dict[WebSocket, tuple[float, int]]] = {}

# Store recent projects, used for deduplication
recent_projects: Dict[str, datetime] = {}  # tokenContract -> timestamp
TIME_WINDOW = timedelta(minutes=10)  # 10 minutes time window

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
worker_processes = []
is_shutting_down = False

# 添加一个集合来跟踪被手动删除的项目
manually_removed_projects: Set[str] = set()  # 存储被手动删除的mint_address

# 全局变量: 控制是否获取X用户信息以及获取范围
FETCH_X_INFO = "all"  # 可选值: "none", "launchacoin", "all"

# Generate unique message ID
async def generate_message_id() -> str:
    """Generate a unique message ID"""
    global message_id_counter
    async with message_id_lock:
        message_id_counter += 1
        return f"msg_{int(time.time())}_{message_id_counter}"

# Handle acknowledgment from client
async def handle_acknowledgment(websocket: WebSocket, message_id: str):
    """Handle acknowledgment from client"""
    try:
        if message_id in pending_acks and websocket in pending_acks[message_id]:
            # 获取消息数据以检查是否是项目消息
            message_data = redis_client.get(f"pending_message:{message_id}")
            is_project_message = False
            project_name = "Unknown"
            token_name = "Unknown"
            
            if message_data:
                try:
                    original_message = json.loads(message_data)
                    if original_message.get("type") == "new_project" and "data" in original_message:
                        is_project_message = True
                        project_name = original_message["data"].get("name", "Unknown")
                        token_name = original_message["data"].get("symbol", "Unknown")
                except:
                    pass
            
            # Get retry count for logging
            timestamp, retry_count = pending_acks[message_id][websocket]
            
            # Remove this client's pending ack
            del pending_acks[message_id][websocket]
            
            # If no more pending acks for this message, clean up
            if not pending_acks[message_id]:
                del pending_acks[message_id]
                # Clean up Redis
                redis_client.delete(f"pending_message:{message_id}")
                
            # Log acknowledgment with retry info
            if is_project_message:
                logger.info(f"收到项目消息确认: '{project_name}' (代币: {token_name}, 消息ID: {message_id}, 重发次数: {retry_count})")
            else:
                logger.info(f"收到消息确认: {message_id} (重发次数: {retry_count})")
                    
    except Exception as e:
        logger.error(f"Error handling acknowledgment: {e}")

# Retry mechanism for unacknowledged messages
async def retry_unacknowledged_messages():
    """Retry sending unacknowledged messages"""
    current_time = time.time()
    retry_timeout = 5.0  # 5 seconds timeout
    
    messages_to_remove = []
    retry_count = 0
    
    for message_id, client_timestamps in pending_acks.items():
        clients_to_remove = []
        
        for client, (timestamp, retry_count) in client_timestamps.items():
            if current_time - timestamp > retry_timeout:
                # Check if we've already retried too many times
                if retry_count >= 2:
                    # Max retries reached, remove from pending
                    clients_to_remove.append(client)
                    logger.warning(f"消息 {message_id} 已达到最大重发次数(2次)，停止重发")
                    continue
                
                # Time to retry
                if client in connected_clients:
                    try:
                        # Get the original message data from Redis or reconstruct
                        message_data = redis_client.get(f"pending_message:{message_id}")
                        if message_data:
                            original_message = json.loads(message_data)
                            
                            # 检查是否是项目消息，如果是则打印详细信息
                            if original_message.get("type") == "new_project" and "data" in original_message:
                                project_data = original_message["data"]
                                project_name = project_data.get("name", "Unknown")
                                contract_address = project_data.get("contractAddress", "Unknown")
                                token_name = project_data.get("symbol", "Unknown")
                                time_since_sent = current_time - timestamp
                                
                                logger.warning(
                                    f"项目消息未收到确认，准备重发(第{retry_count + 1}次): "
                                    f"项目名='{project_name}', "
                                    f"代币='{token_name}', "
                                    f"合约地址='{contract_address}', "
                                    f"消息ID='{message_id}', "
                                    f"发送时间={time_since_sent:.1f}秒前, "
                                    f"客户端连接数={len(connected_clients)}"
                                )
                            
                            await client.send_json(original_message)
                            # Update timestamp and retry count
                            pending_acks[message_id][client] = (current_time, retry_count + 1)
                            retry_count += 1
                            
                            # 如果是项目消息，打印重发成功日志
                            if original_message.get("type") == "new_project" and "data" in original_message:
                                project_data = original_message["data"]
                                project_name = project_data.get("name", "Unknown")
                                token_name = project_data.get("symbol", "Unknown")
                                logger.info(f"项目消息重发成功(第{retry_count}次): '{project_name}' (代币: {token_name}, 消息ID: {message_id})")
                            else:
                                logger.info(f"Retried message {message_id} to client (attempt {retry_count})")
                                
                        else:
                            # Message data not found, remove from pending
                            clients_to_remove.append(client)
                            logger.warning(f"消息数据未找到，无法重发: {message_id}")
                    except Exception as e:
                        logger.warning(f"Failed to retry message {message_id}: {e}")
                        clients_to_remove.append(client)
                else:
                    # Client disconnected, remove from pending
                    clients_to_remove.append(client)
                    logger.info(f"客户端已断开连接，移除待确认消息: {message_id}")
        
        # Remove clients that failed or disconnected
        for client in clients_to_remove:
            if client in pending_acks[message_id]:
                del pending_acks[message_id][client]
        
        # If no more clients for this message, mark for removal
        if not pending_acks[message_id]:
            messages_to_remove.append(message_id)
    
    # Remove empty message entries
    for message_id in messages_to_remove:
        del pending_acks[message_id]
        # Clean up Redis
        redis_client.delete(f"pending_message:{message_id}")
    
    # 如果有重发操作，打印统计信息
    if retry_count > 0:
        logger.info(f"重发统计: 本次重发了 {retry_count} 条消息")
        
        # 统计达到最大重发次数的消息
        max_retry_reached = 0
        for message_id, client_timestamps in pending_acks.items():
            for client, (timestamp, retry_count) in client_timestamps.items():
                if retry_count >= 2:
                    max_retry_reached += 1
        
        if max_retry_reached > 0:
            logger.warning(f"有 {max_retry_reached} 条消息已达到最大重发次数(2次)")

# Background task for retry mechanism
async def retry_task():
    """Background task to handle message retries"""
    while True:
        try:
            await retry_unacknowledged_messages()
            await asyncio.sleep(2)  # Check every 2 seconds
        except asyncio.CancelledError:
            logger.info("Retry task cancelled")
            break
        except Exception as e:
            logger.error(f"Error in retry task: {e}")
            await asyncio.sleep(5)  # Wait longer on error

# Broadcast to all connected clients
async def broadcast_to_clients(data: Dict[str, Any]):
    # Generate message ID
    message_id = await generate_message_id()
    data_with_id = {**data, "messageId": message_id}
    
    # 如果是项目消息，打印详细信息
    if data.get("type") == "new_project" and "data" in data:
        project_data = data["data"]
        project_name = project_data.get("name", "Unknown")
        contract_address = project_data.get("contractAddress", "Unknown")
        token_name = project_data.get("symbol", "Unknown")
    
    # Store message in Redis for potential retry
    redis_client.setex(
        f"pending_message:{message_id}",
        60,  # 1 minute expiration
        json.dumps(data_with_id)
    )
    
    # Initialize pending acks for this message
    pending_acks[message_id] = {}
    
    # 记录广播状态
    # clients_count = len(connected_clients)
    # if clients_count > 0:
        # logger.info(f"广播消息到 {clients_count} 个连接的客户端")
    
    disconnected_clients = []
    broadcast_errors = 0
    broadcast_success = 0
    
    for client in connected_clients:
        try:
            await client.send_json(data_with_id)
            # Add to pending acks
            pending_acks[message_id][client] = (time.time(), 0)
            broadcast_success += 1
        except Exception as e:
            disconnected_clients.append(client)
            broadcast_errors += 1
            logger.warning(f"广播到客户端失败: {e}")
    
    # Remove disconnected clients
    if disconnected_clients:
        for client in disconnected_clients:
            if client in connected_clients:
                connected_clients.remove(client)
            # Also remove from client_message_tracking
            if client in client_message_tracking:
                del client_message_tracking[client]
        logger.info(f"已移除 {len(disconnected_clients)} 个断开连接的客户端, 剩余 {len(connected_clients)} 个客户端")
    
    if broadcast_errors:
        logger.warning(f"广播过程中发生了 {broadcast_errors} 个错误")
    

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

def worker_process(stop_event):
    """工作进程函数"""
    try:
        # 设置工作进程的信号处理
        signal.signal(signal.SIGINT, signal.SIG_IGN)  # 忽略 SIGINT
        signal.signal(signal.SIGTERM, signal.SIG_IGN)  # 忽略 SIGTERM
        
        logger.info(f"Worker process {os.getpid()} started")
        
        # 运行工作进程
        while True:
            # 检查停止事件
            if stop_event.is_set():
                logger.info(f"Worker process {os.getpid()} received stop signal")
                break
                
            try:
                # 使用更短的睡眠间隔，以便更快地响应停止信号
                time.sleep(0.1)
            except Exception as e:
                logger.error(f"Worker process {os.getpid()} sleep error: {e}")
                break
            
    except Exception as e:
        logger.error(f"Worker process {os.getpid()} error: {e}")
    finally:
        logger.info(f"Worker process {os.getpid()} exiting")
        # 确保进程立即退出
        os._exit(0)

def handle_exit_signal(signum, frame):
    """Handle exit signals like SIGINT (Ctrl+C) and SIGTERM"""
    global is_shutting_down
    
    # 如果已经在关闭过程中，忽略重复的信号
    if is_shutting_down:
        logger.info("Already in shutdown process, ignoring signal")
        return
        
    is_shutting_down = True
    
    if signum == signal.SIGTERM:
        logger.info(f"Received SIGTERM signal, shutting down...")
    elif signum == signal.SIGINT:
        logger.info(f"Received SIGINT signal (Ctrl+C), shutting down...")
    else:
        logger.info(f"Received signal {signum}, shutting down...")
    
    # 设置停止事件
    stop_event.set()
    
    # 确保所有资源都被清理
    try:
        # 关闭Redis连接
        redis_client.close()
        twitter_redis.close()
        logger.info("Redis connections closed")
    except Exception as e:
        logger.error(f"Error during cleanup: {e}")
    
    # 等待所有工作进程结束
    for process in worker_processes:
        if process.is_alive():
            try:
                # 直接使用kill信号
                process.kill()
                process.join(timeout=1)  # 给进程1秒来退出
            except Exception as e:
                logger.error(f"Error terminating process {process.pid}: {e}")
    
    logger.info("All worker processes terminated")
    
    # 强制退出
    os._exit(0)

# 恢复 WebSocket 端点
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    connected_clients.append(websocket)
    client_message_tracking[websocket] = {
        "connected_at": time.time(),
        "last_activity": time.time(),
        "message_count": 0
    }
    logger.info(f"新的WebSocket连接建立，当前连接数: {len(connected_clients)}")
    
    try:
        while True:
            # Keep the connection alive and handle incoming messages
            data = await websocket.receive_text()
            try:
                # Try to parse the data as JSON
                message = json.loads(data)
                
                # Handle acknowledgment messages
                if message.get("type") == "ack":
                    message_id = message.get("messageId")
                    if message_id:
                        await handle_acknowledgment(websocket, message_id)
                        # Update client activity
                        client_message_tracking[websocket]["last_activity"] = time.time()
                        continue
                
                # Handle regular project data
                project = message
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
                
                # Update client activity
                client_message_tracking[websocket]["last_activity"] = time.time()
                client_message_tracking[websocket]["message_count"] += 1
                
            except json.JSONDecodeError:
                logger.error("JSON解析错误")
            except Exception as e:
                logger.error(f"处理消息时出错: {e}")
                
    except Exception as e:
        logger.error(f"WebSocket错误: {e}")
        if websocket in connected_clients:
            connected_clients.remove(websocket)
            logger.info(f"WebSocket连接断开，当前连接数: {len(connected_clients)}")
        
        # Clean up client tracking
        if websocket in client_message_tracking:
            del client_message_tracking[websocket]

# 判断part字段的函数
def determine_part_value(project: Dict[str, Any]) -> int:
    """
    判断part字段的值
    如果project的name转小写后，是contractAddress的后缀或前缀（pump，bonk，boop后缀除外），则part为1，否则为0
    """
    project_name = project.get('name', '').lower()
    contract_address = project.get('contractAddress', '').lower()
    
    if not project_name or not contract_address:
        return 0
    
    # 排除的后缀
    excluded_suffixes = ['pump', 'bonk', 'boop']
    
    # 检查是否是后缀（排除特定后缀）
    for suffix in excluded_suffixes:
        if contract_address.endswith(suffix):
            return 0
    
    # 检查name是否是contractAddress的后缀
    if contract_address.endswith(project_name):
        return 1
    
    # 检查name是否是contractAddress的前缀
    if contract_address.startswith(project_name):
        return 1
    
    return 0

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
    
    # 添加part字段
    normalized["part"] = determine_part_value(normalized)
    
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

async def fetch_and_broadcast_community_info(normalized_project: Dict[str, Any], community_id: str):
    try:
        # 检查Redis缓存
        community_key = f"community:{community_id}"
        community_info = None
        
        # 尝试从Redis获取缓存的社区信息
        cached_info = redis_client.get(community_key)
        if cached_info:
            try:
                community_info = json.loads(cached_info)
                logger.info(f"Using cached community info for {community_id}")
            except json.JSONDecodeError:
                community_info = None
        
        # 如果缓存不存在或无效，则调用API获取
        if not community_info:
            community_info = get_twitter_community_info(community_id)
            logger.info(f"community_info: {community_info['community_info']['comm_name']}, {community_info['community_info']['created_at']}")
            if community_info and community_info.get('status') == 'success':
                # 缓存社区信息，设置16天过期时间
                redis_client.setex(community_key, 3600 * 24 * 16, json.dumps(community_info))
                logger.info(f"Cached new community info for {community_id}")
        
        if community_info and community_info.get('status') == 'success':
            broadcast_info = {}
            broadcast_info["type"] = "community_info_update"
            broadcast_info["data"] = {
                "contractAddress": normalized_project["contractAddress"],
                "community_info": community_info['community_info']
            }
            # logger.info(f"Broadcasted Community info {normalized_project['name']}: {broadcast_info}")
            logger.info(f"Broadcasted Community info {normalized_project['name']}")
            await broadcast_to_clients(broadcast_info)

    except Exception as e:
        logger.error(f"Error fetching Community info for {community_id}: {traceback.format_exc()} \n {community_info}")

# 添加一个新的异步函数来处理Twitter信息获取和广播
async def fetch_and_broadcast_twitter_info(normalized_project: Dict[str, Any], twitter_handle: str):
    """异步获取Twitter信息并广播"""
    try:
        # 先检查是否是名人
        is_celebrity = twitter_redis.sismember("twitter:celebrities", twitter_handle.lower())
        twitter_info = None
        
        if is_celebrity:
            # 如果是名人，直接从 Redis 获取用户信息
            user_info = twitter_redis.get(twitter_handle)
            if user_info:
                try:
                    twitter_info = json.loads(user_info)
                    # logger.info(f"Using celebrity info for {twitter_handle}")
                except json.JSONDecodeError:
                    twitter_info = None
        else:
            # 如果不是名人，检查缓存
            if twitter_handle != "i" and twitter_handle != "search" and twitter_handle != "intent":
                expire_key = f"{twitter_handle}:expire"
                
                # 检查缓存是否存在
                if twitter_redis.exists(expire_key):
                    # 缓存存在，直接从 Redis 获取用户信息
                    user_info = twitter_redis.get(twitter_handle)
                    if user_info:
                        try:
                            twitter_info = json.loads(user_info)
                            logger.info(f"Using cached Twitter info for {twitter_handle}")
                        except json.JSONDecodeError:
                            twitter_info = None
                else:
                    # 缓存不存在，调用 API 获取信息
                    twitter_info = get_twitter_user(twitter_handle)
                    if twitter_info:
                        # 增加Twitter API调用计数
                        redis_client.incr(STATS_TWITTER_API_CALLS)
                        # 设置缓存过期时间
                        twitter_redis.setex(expire_key, 3600 * 24 * 10, "1")
                        logger.info(f"Set new cache for {twitter_handle}")
        
        # 如果获取到Twitter信息，更新项目并广播
        if twitter_info:
            # 创建项目副本并添加Twitter信息
            project_with_twitter = normalized_project.copy()
            project_with_twitter["twitter_info"] = twitter_info
            
            # 广播更新后的项目信息
            broadcast_info = {}
            broadcast_info["type"] = "twitter_info_update"
            broadcast_info["data"] = {
                "contractAddress": normalized_project["contractAddress"],
                "twitter_info": twitter_info
            }
            # logger.info(f"Broadcasted Twitter info for {normalized_project['name']}: {broadcast_info}")
            await broadcast_to_clients(broadcast_info)
            
    except Exception as e:
        # print error stack
        logger.error(f"Error fetching Twitter info for {twitter_handle}: {traceback.format_exc()}")
        # logger.error(f"Error fetching Twitter info for {twitter_handle}: {e}")

# 修改 add_meme_project 函数中的相关部分
@app.post("/api/meme-projects")
async def add_meme_project(project: Dict[str, Any]):
    try:
        # Normalize project fields to match frontend expectations
        normalized_project = normalize_project(project)
        name = normalized_project["name"]
        s = time.time()
        # logger.info(f"name: {name}")
        
        # 更新统计数据
        update_project_stats(normalized_project)
        
        # 检查是否有社交媒体
        has_hover_tweet = bool(normalized_project.get("hoverTweet"))
        has_links = bool(normalized_project.get("links"))
        has_pump_only = False
        has_social_media = False
        
        
        if has_links:
            links = normalized_project["links"]
            if isinstance(links, dict):
                # 检查是否只有pump.fun链接
                has_pump_only = all(
                    "pump.fun" in v for v in links.values() if isinstance(v, str)
                ) and len(links) > 0
                
                # 检查是否有社交媒体链接
                social_platforms = ["twitter.com", "x.com", "t.me", "discord.com", "discord.gg"]
                has_social_media = any(
                    any(platform in v for platform in social_platforms)
                    for v in links.values() if isinstance(v, str)
                )
        
        # 判断是否有社交媒体
        has_social_media = has_hover_tweet or (has_links and not has_pump_only) or has_social_media
        
        # # 如果无社交媒体且part为0，不广播并增加不广播计数
        # if not has_social_media and not normalized_project["website"] and normalized_project["part"] == 0:
        #     redis_client.incr(STATS_NOT_BROADCAST_PROJECTS)
        #     return {"status": "ignored", "message": "Project ignored due to no social media and part=0"}

        # 获取dev字段和dev_history
        contract_address = normalized_project.get('contractAddress', '')
        
        # 处理pump.fun项目
        if contract_address and contract_address.lower().endswith('pump'):
            # 获取pump.fun描述
            pump_info = await fetch_pump_description(contract_address)
            
            if pump_info:
                normalized_project['pump_desc'] = pump_info.get('description', '')
                normalized_project['dev'] = pump_info.get('creator', '')
        
        # 处理bonk.fun项目
        elif contract_address and contract_address.lower().endswith('bonk'):
            # 获取bonk.fun描述
            bonk_info = await fetch_bonk_description(contract_address)
            
            if bonk_info:
                normalized_project['pump_desc'] = bonk_info.get('description', '')
                normalized_project['dev'] = bonk_info.get('creator', '')
        
        # 添加dev_history字段
        if normalized_project.get('dev'):
            # 从Redis DB 6读取creator_tokens信息
            creator_tokens_key = f"creator_tokens:{normalized_project['dev']}"
            creator_tokens_data = redis_client.get(creator_tokens_key)
            
            if creator_tokens_data:
                try:
                    creator_tokens = json.loads(creator_tokens_data)
                    if isinstance(creator_tokens, list) and len(creator_tokens) > 0:
                        # 提取所有symbol字段
                        dev_history = [token.get('symbol', '') for token in creator_tokens if token.get('symbol')]
                        normalized_project['dev_history'] = dev_history
                    else:
                        normalized_project['dev_history'] = []
                except (json.JSONDecodeError, TypeError) as e:
                    logger.warning(f"解析creator_tokens数据失败: {e}")
                    normalized_project['dev_history'] = []
            else:
                normalized_project['dev_history'] = []
        else:
            normalized_project['dev_history'] = []

        # 立即广播项目信息（不包含Twitter作者信息和Twitter社区信息）
        await broadcast_to_clients({"type": "new_project", "data": normalized_project})
        # logger.info(f"broadcast: {name}: {normalized_project['contractAddress']} {time.time() - s}")
        logger.info(f"broadcast: {name} {time.time() - s}")
        
        # 检查是否需要获取Twitter信息
        should_fetch_x_info = False
        twitter_handle = None
        
        # 只有在有社交媒体的情况下才考虑获取Twitter信息
        if has_social_media:
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
            
            # 如果找到Twitter用户名，异步获取信息
            if twitter_handle:
                # 创建异步任务获取Twitter信息
                asyncio.create_task(fetch_and_broadcast_twitter_info(normalized_project, twitter_handle))
        

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
                    logger.info(f"推文 {tweet_key} 距离上次出现已超过阈值({TWEET_REPEAT_INTERVAL}s)，本次不推送 {name}")
                    redis_client.set(tweet_key, now)
                    redis_client.incr(STATS_NOT_BROADCAST_PROJECTS)  # 增加不广播计数
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

        

        # 检查xLink字段是否为社区链接
        if "xLink" in normalized_project and isinstance(normalized_project["xLink"], str):
            if normalized_project["xLink"].startswith("https://x.com/i/communities/"):
                # 提取社区ID
                community_id = normalized_project["xLink"].split("/")[-1]
                if community_id:
                    # 异步获取并广播社区信息
                    asyncio.create_task(fetch_and_broadcast_community_info(normalized_project, community_id))
        
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
            
            # 处理pump.fun项目
            if contract_address and contract_address.lower().endswith('pump'):
                # 获取pump.fun描述
                pump_info = await fetch_pump_description(contract_address)
                
                if pump_info:
                    project['pump_desc'] = pump_info.get('description', '')
                    project['dev'] = pump_info.get('creator', '')
            
            # 处理bonk.fun项目
            elif contract_address and contract_address.lower().endswith('bonk'):
                # 获取bonk.fun描述
                bonk_info = await fetch_bonk_description(contract_address)
                
                if bonk_info:
                    project['bonk_desc'] = bonk_info.get('description', '')
                    project['dev'] = bonk_info.get('creator', '')

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

def update_project_stats(normalized_project: Dict[str, Any]):
    """更新项目统计数据"""
    try:
        # 增加总项目数
        total = redis_client.incr(STATS_TOTAL_PROJECTS)
        
        # 检查website链接
        website = None
        if "links" in normalized_project and isinstance(normalized_project["links"], dict):
            website = normalized_project["links"].get("website", "")
        
        # 统计各平台项目数
        youtube_count = 0
        tiktok_count = 0
        instagram_count = 0
        no_social_count = 0
        not_broadcast_count = 0
        
        if website:
            if "www.youtube.com" in website:
                youtube_count = redis_client.incr(STATS_YOUTUBE_PROJECTS)
            if "www.tiktok.com" in website:
                tiktok_count = redis_client.incr(STATS_TIKTOK_PROJECTS)
            if "www.instagram.com" in website:
                instagram_count = redis_client.incr(STATS_INSTAGRAM_PROJECTS)
        
        # 统计无社交媒体的项目数
        has_hover_tweet = bool(normalized_project.get("hoverTweet"))
        has_links = bool(normalized_project.get("links"))
        has_pump_only = False
        
        if has_links:
            links = normalized_project["links"]
            if isinstance(links, dict):
                # 检查是否只有pump.fun链接
                has_pump_only = all(
                    "pump.fun" in v for v in links.values() if isinstance(v, str)
                ) and len(links) > 0
        
        if not has_hover_tweet and (not has_links or has_pump_only) and not website:
            no_social_count = redis_client.incr(STATS_NO_SOCIAL_PROJECTS)
        
        # 每100个项目打印一次统计值
        if total % 100 == 0:
            youtube_count = int(redis_client.get(STATS_YOUTUBE_PROJECTS) or 0)
            tiktok_count = int(redis_client.get(STATS_TIKTOK_PROJECTS) or 0)
            instagram_count = int(redis_client.get(STATS_INSTAGRAM_PROJECTS) or 0)
            no_social_count = int(redis_client.get(STATS_NO_SOCIAL_PROJECTS) or 0)
            not_broadcast_count = int(redis_client.get(STATS_NOT_BROADCAST_PROJECTS) or 0)
            twitter_api_calls = int(redis_client.get(STATS_TWITTER_API_CALLS) or 0)
            logger.info(f"统计值 [总数: {total}] [YouTube: {youtube_count}] [TikTok: {tiktok_count}] [Instagram: {instagram_count}] [无社交: {no_social_count}] [不广播: {not_broadcast_count}] [Twitter API调用: {twitter_api_calls}]")
            
    except Exception as e:
        logger.error(f"更新统计数据时出错: {str(e)}")

@app.get("/api/stats")
async def get_project_stats():
    """获取项目统计数据"""
    try:
        stats = {
            "total_projects": int(redis_client.get(STATS_TOTAL_PROJECTS) or 0),
            "youtube_projects": int(redis_client.get(STATS_YOUTUBE_PROJECTS) or 0),
            "tiktok_projects": int(redis_client.get(STATS_TIKTOK_PROJECTS) or 0),
            "instagram_projects": int(redis_client.get(STATS_INSTAGRAM_PROJECTS) or 0),
            "no_social_projects": int(redis_client.get(STATS_NO_SOCIAL_PROJECTS) or 0),
            "not_broadcast_projects": int(redis_client.get(STATS_NOT_BROADCAST_PROJECTS) or 0),
            "twitter_api_calls": int(redis_client.get(STATS_TWITTER_API_CALLS) or 0)  # 新增：Twitter API调用次数
        }
        return {"status": "success", "stats": stats}
    except Exception as e:
        logger.error(f"获取统计数据时出错: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to get stats: {str(e)}")

@app.post("/api/project-views")
async def add_project_view(project_view: Dict[str, Any]):
    """记录项目浏览信息"""
    try:
        # 验证必需字段
        required_fields = ["name", "description", "contract_address", "timestamp", "market_cap"]
        for field in required_fields:
            if field not in project_view:
                logger.info(f"missing {field}")
                raise HTTPException(status_code=400, detail=f"Missing required field: {field}")
        
        # 生成唯一ID
        view_id = f"{project_view['contract_address']}:{project_view['timestamp']}"
        
        # 存储到Redis
        # 1. 存储到集合中
        redis_client.sadd(PROJECT_VIEWS_SET, view_id)
        
        # 2. 存储详细信息
        try:
            redis_key = f"{PROJECT_VIEW_HASH}{view_id}"
            
            mapping_data = {
                "name": project_view["name"],
                "description": project_view["description"],
                "contract_address": project_view["contract_address"],
                "timestamp": project_view["timestamp"],
                "market_cap": project_view["market_cap"]
            }
            
            redis_client.hset(
                redis_key,
                mapping=mapping_data
            )
        except Exception as e:
            logger.error(f"Error storing data in Redis: {str(e)}")
            raise
        
        # 3. 添加到时间排序集合
        redis_client.zadd(
            PROJECT_VIEWS_BY_TIME,
            {view_id: float(project_view["timestamp"])}
        )
        
        return {"status": "success", "message": "Project view recorded successfully"}
    except Exception as e:
        logger.error(f"记录项目浏览信息时出错: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to record project view: {str(e)}")

def convert_market_cap_to_float(market_cap_str: str) -> float:
    """Convert market cap string (e.g. '$608K', '$1.2M', '$2.5B') to float value"""
    try:
        # Remove currency symbol and whitespace
        value = market_cap_str.strip().replace('$', '').strip()
        
        # Get the multiplier based on the suffix
        multiplier = 1
        if value.endswith('K'):
            multiplier = 1000
            value = value[:-1]
        elif value.endswith('M'):
            multiplier = 1000000
            value = value[:-1]
        elif value.endswith('B'):
            multiplier = 1000000000
            value = value[:-1]
        
        # Convert to float and apply multiplier
        return float(value) * multiplier
    except (ValueError, AttributeError) as e:
        logger.error(f"Error converting market cap '{market_cap_str}' to float: {str(e)}")
        return 0.0

@app.get("/api/project-views")
async def get_project_views(
    start_time: int = Query(..., description="Start timestamp (in seconds)"),
    end_time: int = Query(..., description="End timestamp (in seconds)")
):
    """获取指定时间范围内的项目浏览列表"""
    try:
        logger.info(f"Getting project views from {start_time} to {end_time}")
        
        # 从时间排序集合中获取指定范围内的项目ID
        view_ids = redis_client.zrangebyscore(
            PROJECT_VIEWS_BY_TIME,
            start_time,
            end_time
        )
        logger.info(f"Found {len(view_ids)} view IDs in time range")
        
        # 获取每个项目的详细信息
        project_views = []
        for view_id in view_ids:
            view_data = redis_client.hgetall(f"{PROJECT_VIEW_HASH}{view_id}")
            if view_data:
                project_views.append({
                    "name": view_data["name"],
                    "description": view_data["description"],
                    "contract_address": view_data["contract_address"],
                    "timestamp": int(view_data["timestamp"]),
                    "market_cap": convert_market_cap_to_float(view_data["market_cap"])
                })
            else:
                logger.warning(f"No data found for view_id: {view_id}")
        
        # 按时间戳排序
        project_views.sort(key=lambda x: x["timestamp"])
        
        logger.info(f"Returning {len(project_views)} project views")
        return {
            "status": "success",
            "project_views": project_views,
            "total": len(project_views)
        }
    except Exception as e:
        logger.error(f"获取项目浏览列表时出错: {str(e)}")
        logger.error(f"错误详情: {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"Failed to get project views: {str(e)}")

@app.get("/api/debug/redis")
async def debug_redis():
    """Debug endpoint to check Redis data"""
    try:
        # Check if Redis is connected
        try:
            redis_info = redis_client.info()
            logger.info("Successfully connected to Redis")
        except redis.ConnectionError as e:
            logger.error(f"Failed to connect to Redis: {str(e)}")
            raise HTTPException(status_code=500, detail=f"Redis connection error: {str(e)}")
        
        # Get all keys in the project views set
        try:
            view_ids = redis_client.smembers(PROJECT_VIEWS_SET)
            logger.info(f"Successfully retrieved {len(view_ids)} view IDs from set")
        except Exception as e:
            logger.error(f"Failed to get view IDs from set: {str(e)}")
            view_ids = set()
        
        # Get all keys in the time-sorted set
        try:
            time_sorted_views = redis_client.zrange(PROJECT_VIEWS_BY_TIME, 0, -1, withscores=True)
            logger.info(f"Successfully retrieved {len(time_sorted_views)} time-sorted views")
        except Exception as e:
            logger.error(f"Failed to get time-sorted views: {str(e)}")
            time_sorted_views = []
        
        # Try to get a sample view data
        sample_data = None
        if view_ids:
            try:
                sample_id = list(view_ids)[0]
                sample_data = redis_client.hgetall(f"{PROJECT_VIEW_HASH}{sample_id}")
                logger.info(f"Successfully retrieved sample data for ID {sample_id}")
            except Exception as e:
                logger.error(f"Failed to get sample data: {str(e)}")
        
        return {
            "status": "success",
            "redis_info": {
                "connected_clients": redis_info.get("connected_clients"),
                "used_memory": redis_info.get("used_memory_human"),
                "total_connections_received": redis_info.get("total_connections_received")
            },
            "project_views_set_size": len(view_ids),
            "time_sorted_views_size": len(time_sorted_views),
            "sample_view_ids": list(view_ids)[:5] if view_ids else [],
            "sample_time_sorted_views": time_sorted_views[:5] if time_sorted_views else [],
            "sample_data": sample_data
        }
    except Exception as e:
        logger.error(f"Debug Redis endpoint error: {str(e)}")
        logger.error(f"Error details: {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"Failed to get Redis debug info: {str(e)}")

@app.post("/api/debug/add-test-data")
async def add_test_data():
    """Add test data to Redis"""
    try:
        # Create a test project view
        test_view = {
            "name": "Test Project",
            "description": "This is a test project",
            "contract_address": "test123",
            "timestamp": int(time.time()),
            "market_cap": 1000000.0
        }
        
        # Generate view ID
        view_id = f"{test_view['contract_address']}:{test_view['timestamp']}"
        
        # Add to set
        redis_client.sadd(PROJECT_VIEWS_SET, view_id)
        logger.info(f"Added test view ID to set: {view_id}")
        
        # Store details
        redis_key = f"{PROJECT_VIEW_HASH}{view_id}"
        redis_client.hset(redis_key, mapping=test_view)
        logger.info(f"Stored test view details at key: {redis_key}")
        
        # Add to time-sorted set
        redis_client.zadd(PROJECT_VIEWS_BY_TIME, {view_id: float(test_view['timestamp'])})
        logger.info(f"Added test view to time-sorted set with score: {test_view['timestamp']}")
        
        return {
            "status": "success",
            "message": "Test data added successfully",
            "view_id": view_id
        }
    except Exception as e:
        logger.error(f"Failed to add test data: {str(e)}")
        logger.error(f"Error details: {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"Failed to add test data: {str(e)}")

# 新增API接口：记录创建者代币信息
@app.post("/api/record_creator")
# async def record_creator(creator_address: str, symbol: str, token_address: str):
async def record_creator(creator_info: Dict[str, str]):
    """记录创建者代币信息
    
    Args:
        creator_address: 创建者地址
        symbol: 代币符号
        token_address: 代币地址
        
    Returns:
        dict: 操作结果
    """
    try:
        # 验证参数
        if not creator_info or not creator_info.get("creator_address") or not creator_info.get("symbol") or not creator_info.get("token_address"):
            raise HTTPException(status_code=400, detail="所有参数都是必需的")

        creator_address = creator_info.get("creator_address")
        symbol = creator_info.get("symbol")
        token_address = creator_info.get("token_address")
        
        # 创建者代币信息
        creator_token_info = {
            "symbol": symbol,
            "token_address": token_address
        }
        
        key = f"creator_tokens:{creator_address}"
        
        # 检查key是否存在
        if redis_client.exists(key):
            # 获取现有数据
            existing_data = redis_client.get(key)
            if existing_data:
                try:
                    creator_tokens = json.loads(existing_data)
                    if not isinstance(creator_tokens, list):
                        creator_tokens = []
                except json.JSONDecodeError:
                    creator_tokens = []
            else:
                creator_tokens = []
            
            # 检查token_address是否已存在
            existing_token_addresses = [token.get("token_address") for token in creator_tokens]
            if token_address in existing_token_addresses:
                return {
                    "status": "skipped",
                    "message": f"代币 {token_address} 已存在于创建者 {creator_address} 的记录中",
                    "creator_address": creator_address,
                    "symbol": symbol,
                    "token_address": token_address
                }
            
            # 添加新代币
            creator_tokens.append(creator_token_info)
            redis_client.set(key, json.dumps(creator_tokens))
            logger.info(f"更新创建者代币: {creator_address} -> {symbol} ({token_address})")
            
            return {
                "status": "updated",    
                "message": f"成功更新创建者 {creator_address} 的代币记录",
                "creator_address": creator_address,
                "symbol": symbol,
                "token_address": token_address,
                "total_tokens": len(creator_tokens)
            }
        else:
            # 创建新的创建者记录
            creator_tokens = [creator_token_info]
            redis_client.set(key, json.dumps(creator_tokens))
            logger.info(f"创建创建者代币: {creator_address} -> {symbol} ({token_address})")
            
            return {
                "status": "created",
                "message": f"成功创建创建者 {creator_address} 的代币记录",
                "creator_address": creator_address,
                "symbol": symbol,
                "token_address": token_address,
                "total_tokens": 1
            }
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"记录创建者代币信息失败: {e}")
        raise HTTPException(status_code=500, detail=f"服务器内部错误: {str(e)}")

# 新增API接口：获取创建者的代币列表
@app.get("/api/get_tokens_by_creator")
async def get_tokens_by_creator(creator_address: str):
    """获取创建者部署的代币列表
    
    Args:
        creator_address: 创建者地址
        
    Returns:
        dict: 包含代币列表的响应
    """
    try:
        # 验证参数
        if not creator_address:
            raise HTTPException(status_code=400, detail="创建者地址是必需的")
        
        key = f"creator_tokens:{creator_address}"
        
        # 检查key是否存在
        if not redis_client.exists(key):
            return {
                "status": "not_found",
                "message": f"未找到创建者 {creator_address} 的代币记录",
                "creator_address": creator_address,
                "tokens": []
            }
        
        # 获取创建者代币数据
        creator_data = redis_client.get(key)
        if not creator_data:
            return {
                "status": "empty",
                "message": f"创建者 {creator_address} 的代币记录为空",
                "creator_address": creator_address,
                "tokens": []
            }
        
        try:
            creator_tokens = json.loads(creator_data)
            if not isinstance(creator_tokens, list):
                creator_tokens = []
        except json.JSONDecodeError:
            logger.error(f"解析创建者 {creator_address} 的代币数据失败")
            return {
                "status": "error",
                "message": "数据解析失败",
                "creator_address": creator_address,
                "tokens": []
            }
        
        # 格式化返回数据
        tokens_list = []
        for token in creator_tokens:
            tokens_list.append({
                "symbol": token.get("symbol", "Unknown"),
                "token_address": token.get("token_address", "Unknown")
            })
        
        logger.info(f"获取创建者 {creator_address} 的代币列表: {len(tokens_list)} 个代币")
        
        return {
            "status": "success",
            "message": f"成功获取创建者 {creator_address} 的代币列表",
            "creator_address": creator_address,
            "tokens": tokens_list,
            "total_count": len(tokens_list)
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取创建者代币列表失败: {e}")
        raise HTTPException(status_code=500, detail=f"服务器内部错误: {str(e)}")

# 新增API接口：检查代币是否为顶级代币
@app.get("/api/check_top_token")
async def check_top_token(token_address: str):
    """检查代币是否为顶级市值代币
    
    Args:
        token_address: 代币地址
        
    Returns:
        dict: 代币信息
    """
    try:
        # 验证参数
        if not token_address:
            raise HTTPException(status_code=400, detail="代币地址是必需的")
        
        key = f"top_token:{token_address}"
        
        # 检查key是否存在
        if not redis_client.exists(key):
            return {
                "status": "not_found",
                "message": f"代币 {token_address} 不是顶级市值代币",
                "token_address": token_address,
                "token_info": None
            }
        
        # 获取代币信息
        token_data = redis_client.get(key)
        if not token_data:
            return {
                "status": "empty",
                "message": f"代币 {token_address} 的信息为空",
                "token_address": token_address,
                "token_info": None
            }
        
        try:
            token_info = json.loads(token_data)
        except json.JSONDecodeError:
            logger.error(f"解析代币 {token_address} 的数据失败")
            return {
                "status": "error",
                "message": "数据解析失败",
                "token_address": token_address,
                "token_info": None
            }
        
        logger.info(f"检查顶级代币: {token_address} -> {token_info.get('name', 'Unknown')}")
        
        return {
            "status": "found",
            "message": f"代币 {token_address} 是顶级市值代币",
            "token_address": token_address,
            "token_info": token_info
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"检查顶级代币失败: {e}")
        raise HTTPException(status_code=500, detail=f"服务器内部错误: {str(e)}")

if __name__ == "__main__":
    import uvicorn
    
    # Configure uvicorn
    config = uvicorn.Config(
        "axiom-server:app",  # 更新为新的文件名
        host="192.168.1.2", 
        port=5001, 
        reload=False,
        log_level="info",
        access_log=False,  # 禁用HTTP请求访问日志
        workers=64,  # 使用24个工作进程
        limit_concurrency=1000,  # 限制并发连接数
        backlog=2048  # 增加等待队列大小
    )
    
    # Run the server
    try:
        server = uvicorn.Server(config)
        
        # 在主进程中注册信号处理器
        signal.signal(signal.SIGINT, handle_exit_signal)  # Ctrl+C
        signal.signal(signal.SIGTERM, handle_exit_signal)  # kill command
        
        # 启动工作进程
        for _ in range(config.workers):
            process = multiprocessing.Process(target=worker_process, args=(stop_event,))
            process.daemon = True  # 设置为守护进程
            process.start()
            worker_processes.append(process)
            logger.info(f"Started worker process {process.pid}")
        
        logger.info("Server starting...")
        # 运行服务器
        server.run()
    except KeyboardInterrupt:
        logger.info("Keyboard interrupt received")
        handle_exit_signal(signal.SIGINT, None)
    except Exception as e:
        logger.error(f"Server error: {e}")
        handle_exit_signal(signal.SIGTERM, None)
    finally:
        # 确保所有进程都被终止
        for process in worker_processes:
            if process.is_alive():
                try:
                    # 直接使用kill信号
                    process.kill()
                    process.join(timeout=1)  # 给进程1秒来退出
                except Exception as e:
                    logger.error(f"Error terminating process {process.pid}: {e}")
        logger.info("Server shutdown complete") 
