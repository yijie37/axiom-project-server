import time
import redis
import json
import traceback
import os
import datetime
from utils.twitter_user_info import get_twitter_user_info
from utils.wasm_caller import PumpPillAPI
from utils.pumppill import send_pump_pill_request

# 获取当前文件所在目录
current_dir = os.path.dirname(os.path.abspath(__file__))
# 构建 pkg 目录的完整路径
pkg_dir = os.path.join(current_dir, "pkg")

# 创建全局 PumpPillAPI 实例
pump_pill_api = PumpPillAPI(pkg_dir, use_nodejs=True)

# 创建全局 Redis 客户端
twitter_redis = redis.Redis(db=7, decode_responses=True)

# 名人名单集合的 Redis key
CELEBRITY_SET_KEY = "twitter:celebrities"

def is_celebrity(username: str) -> bool:
    """
    检查用户是否在名人名单中
    
    Args:
        username (str): Twitter用户名
        
    Returns:
        bool: 是否是名人
    """
    return twitter_redis.sismember(CELEBRITY_SET_KEY, username.lower())

def get_user_info_from_redis(username: str) -> str:
    """
    从Redis中获取用户ID
    
    Args:
        username (str): Twitter用户名
        
    Returns:
        str: 用户ID，如果不存在则返回None
    """
    try:
        user_data = twitter_redis.get(username)
        
        if user_data:
            data = json.loads(user_data)
            return data
        return None
    except Exception as e:
        print(f"Error getting user_id from Redis: {e}")
        return None

def get_twitter_user(username: str):
    """
    获取Twitter用户的完整信息，包括基本信息和pump信息
    限制每天成功调用次数不超过800次。
    """
    try:
        # ==== 新增：每天调用次数限制 ====
        today = datetime.datetime.utcnow().strftime('%Y%m%d')
        count_key = f"twitter_user_success_{today}"
        current_count = twitter_redis.get(count_key)
        if current_count is not None and int(current_count) >= 800:
            print(f"[限制] 今日get_twitter_user成功调用次数已达上限800，拒绝调用 {username}")
            return None
        # ==== 结束 ====

        # 1. 首先尝试从Redis获取user_id
        user_info = get_user_info_from_redis(username)
        
        # 2. 如果Redis中没有，则获取用户基本信息
        if user_info is None or not user_info.get('id', None):
            user_info_from_api = get_twitter_user_info(username)
            if not user_info_from_api or user_info_from_api.get('status') != 'success':
                error_msg = f"Failed to get basic info for {username}"
                raise ValueError(error_msg)
            user_id = user_info_from_api['data']['id']
        else:
            user_id = user_info['id']
        
        # 3. 获取签名 - 使用全局 PumpPillAPI 实例
        sign = pump_pill_api.encrypt_data(username, user_id)
        
        # 4. 获取pump信息
        pump_info = send_pump_pill_request(username, user_id, sign)
        if not pump_info or pump_info.get('code') != 1:
            raise ValueError(f"Failed to get pump info for {username}")
        
        # 5. 合并信息
        data = get_user_info_from_redis(username)
        
        # ==== 新增：成功后自增计数 ====
        twitter_redis.incr(count_key)
        twitter_redis.expire(count_key, 60*60*48)  # 2天过期，防止key堆积
        # ==== 结束 ====
        
        return data
        
    except Exception as e:
        print(f"Error getting user info for {username}: {e}, {traceback.format_exc()}")
        return None

if __name__ == "__main__":
    # 测试函数
    start_time = time.time()
    # result = get_twitter_user("elonmusk")
    # result = get_twitter_user("aeyakovenko")
    result = get_twitter_user("frankdegods")
    end_time = time.time()
    
    print(f"\nTotal time taken: {end_time - start_time} seconds")
    if result:
        print("\nBasic Info:")
        print(f"ID: {result['id']}")
        print(f"Followers: {result['followerCount']}")
        print(f"Following: {result['followingCount']}")
        
        print("\nPump Info:")
        print(f"Title: {result['title']}")
        print(f"Followed KOLs: {result['followed_kol_count']}")

    # print(result)
