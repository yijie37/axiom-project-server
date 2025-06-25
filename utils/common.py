from typing import List, Dict, Any, Set, Optional, Tuple
import re
import logging
import requests
from bs4 import BeautifulSoup
import uuid
import warnings
import asyncio
import json
import time

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

# 提取Twitter用户名函数
def extract_twitter_handle(url: str) -> Optional[str]:
    """从Twitter URL中提取用户名"""
    # 处理 x.com 或 twitter.com 的URL格式
    pattern = r'(?:https?:\/\/)?(?:www\.)?(?:twitter\.com|x\.com)\/([a-zA-Z0-9_]+)(?:\/status\/\d+)?'
    match = re.search(pattern, url)
    if match:
        return match.group(1)
    return None

def extract_tweet_info(text: str) -> Optional[Tuple[str, str]]:
    """
    从文本中提取 x.com/twitter.com 推文的 user 和 tweet_id
    """
    pattern = r"(?:https?://)?(?:www\.)?(?:x\.com|twitter\.com)/([a-zA-Z0-9_]+)/status/(\d+)"
    match = re.search(pattern, text)
    if match:
        return match.group(1), match.group(2)
    return None

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
        # response.raise_for_status()
        
        # 初始化结果
        result = {'mint': token_contract}
        html_str = response.text
        
        coin_json_pattern = r'coin\\\":\s*({[^{]*\\\"mint\\\":\\\"' + re.escape(token_contract) + r'\\\"[^}]+})'
        
        # for pattern in coin_json_patterns:
        coin_match = re.search(coin_json_pattern, html_str)
        if coin_match:
            try:
                coin_json_str = coin_match.group(1)
                # 找到完整的JSON对象
                open_braces = 1  # 已经找到了开始的 {
                end_pos = len(coin_json_str)
                
                for i in range(1, len(coin_json_str)):
                    if coin_json_str[i] == '{':
                        open_braces += 1
                    elif coin_json_str[i] == '}':
                        open_braces -= 1
                        if open_braces == 0:
                            end_pos = i + 1
                            break
                
                coin_json_str = coin_json_str[:end_pos]
                coin_json_str = coin_json_str.replace('\\\"', '"').replace('\\\\', '\\')
                
                try:
                    data = json.loads(coin_json_str)
                    result = {k: data[k] for k in ['description', 'creator'] if k in data}
                    return result
                except json.JSONDecodeError:
                    pass
            except Exception:
                pass
    except requests.exceptions.RequestException:
        return None
    except Exception:
        return None

# Function to fetch bonk.fun description and creator
async def fetch_bonk_description(token_contract: str) -> Optional[Dict[str, str]]:
    """Fetch description and creator from bonk.fun for a given token contract."""
    # 生成请求跟踪ID
    request_id = str(uuid.uuid4())[:8]
    
    # 验证合约地址
    if not is_valid_contract_address(token_contract):
        logger.warning(f"[{request_id}] 无效的合约地址: {token_contract}")
        return None
        
    try:
        # 构建URL
        url = f"https://launch-mint-v1.raydium.io/get/by/mints?ids={token_contract}"
        
        # 使用requests库发送请求
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
            "Accept": "application/json",
            "Accept-Language": "en-US,en;q=0.5",
        }
        
        logger.info(f"[{request_id}] 正在获取bonk.fun信息: {token_contract}")
        
        # 发送请求，设置10秒超时
        response = requests.get(url, headers=headers, timeout=10, verify=False)
        
        if response.status_code == 200:
            data = response.json()
            
            # 检查响应结构
            if data.get("success") and "data" in data and "rows" in data["data"]:
                rows = data["data"]["rows"]
                if len(rows) > 0:
                    token_info = rows[0]
                    
                    # 提取description和creator
                    description = token_info.get("description", "")
                    creator = token_info.get("creator", "")
                    
                    if creator:
                        logger.info(f"[{request_id}] 成功获取bonk.fun信息: creator={creator}, description长度={len(description)}")
                        return {
                            "description": description,
                            "creator": creator
                        }
                    else:
                        logger.warning(f"[{request_id}] Token信息中未找到creator字段")
                else:
                    logger.warning(f"[{request_id}] 未找到token信息")
            else:
                logger.warning(f"[{request_id}] API响应格式异常: {data}")
        else:
            logger.error(f"[{request_id}] API请求失败，状态码: {response.status_code}")
            
    except requests.exceptions.Timeout:
        logger.error(f"[{request_id}] 请求超时: {token_contract}")
    except requests.exceptions.RequestException as e:
        logger.error(f"[{request_id}] 请求异常: {e}")
    except Exception as e:
        logger.error(f"[{request_id}] 未知异常: {e}")
    
    return None

if __name__ == "__main__":
    # 测试pump.fun功能
    s = time.time()
    d = fetch_pump_description("DUnxXSAEZg9uVGw7yuiYCtVSgb53JH3vHJgK9fr5pump")
    print("pump.fun测试: ", time.time() - s)
    print(d)
    
    # 测试bonk.fun功能
    import asyncio
    async def test_bonk():
        s = time.time()
        d = await fetch_bonk_description("AtortPA9SVbkKmdzu5zg4jxgkR4howvPshorA9jYbonk")
        print("bonk.fun测试: ", time.time() - s)
        print(d)
    
    asyncio.run(test_bonk())