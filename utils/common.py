from typing import List, Dict, Any, Set, Optional, Tuple
import re
import logging
import requests
from bs4 import BeautifulSoup
import uuid
import warnings

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