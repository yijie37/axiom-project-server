import requests
import json

# Common header for all API requests
def get_common_headers():
    """
    Returns the common headers used in Twitter API requests.
    
    Returns:
        dict: Common headers for API requests.
    """
    return {
        "accept": "*/*",
        "accept-encoding": "gzip, deflate, br, zstd",
        "accept-language": "zh-CN,zh;q=0.9,en-US;q=0.8,en;q=0.7",
        "content-type": "application/json",
        "origin": "https://x.com",
        "referer": "https://x.com/",
        "sec-ch-ua": '"Chromium";v="134", "Not:A-Brand";v="24", "Google Chrome";v="134"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"macOS"',
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "cross-site",
        "user-agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36"
    }

def get_previous_name(username):
    """
    Make a request to get previous name information for a specific Twitter username.
    
    Args:
        username (str): The Twitter username to check for previous names.
        
    Returns:
        dict: The response data from the API.
    """
    url = "https://ai.xplore3.com/api/91edd400-9c4a-0eb5-80ce-9d32973f2c49/prevname_query"
    
    # Get common headers
    headers = get_common_headers()
    
    # Prepare payload
    payload = {"x_user_name": username}
    
    # Make the POST request
    response = requests.post(url, headers=headers, json=payload)
    
    # Check if the request was successful
    if response.status_code == 200:
        return response.json()
    else:
        print(f"Error: {response.status_code} {response.text} get_previous_name")
        return None

def get_mentions_ca(username):
    """
    Make a request to get CA mentions information for a specific Twitter username.
    
    Args:
        username (str): The Twitter username to check for CA mentions.
        
    Returns:
        dict: The response data from the API.
    """
    url = "https://ai.xplore3.com/api/91edd400-9c4a-0eb5-80ce-9d32973f2c49/mention_ca"
    
    # Get common headers
    headers = get_common_headers()
    
    # Prepare payload
    payload = {"x_user_name": username}
    
    # Make the POST request
    response = requests.post(url, headers=headers, json=payload)
    
    # Check if the request was successful
    if response.status_code == 200:
        return response.json()
    else:
        print(f"Error: {response.status_code} get_mentions_ca")
        print(response.text)
        return None

def get_twitter_labels(username):
    """
    Make a request to get Twitter labels for a specific username.
    
    Args:
        username (str): The Twitter username to get labels for.
        
    Returns:
        dict: The response data from the API.
    """
    url = "https://ai.xplore3.com/api/91edd400-9c4a-0eb5-80ce-9d32973f2c49/twitter_labels"
    
    # Get common headers
    headers = get_common_headers()
    
    # Prepare payload
    payload = {"xusername": username}
    
    # Make the POST request
    response = requests.post(url, headers=headers, json=payload)
    
    # Check if the request was successful
    if response.status_code == 200:
        return response.json()
    else:
        print(f"Error: {response.status_code} {response.text} get_twitter_labels")
        return None

def get_twitter_user_followers(username):
    """
    Make a request to get Twitter user information for a specific username.
    
    Args:
        username (str): The Twitter username to get information for.
        
    Returns:
        dict: The response data from the API.
    """
    url = f"https://kota.chaineye.tools/api/plugin/twitter/info?username={username}"
    
    # Get common headers
    headers = get_common_headers()
    
    # Make the GET request
    response = requests.get(url, headers=headers)
    
    # Check if the request was successful
    if response.status_code == 200:
        # print("get_twitter_user_followers success")
        return response.json()
    else:
        print(f"Error: {response.status_code} {response.text} get_twitter_user_followers")
        return None


# # 获取Twitter完整信息函数
# def get_twitter_complete_info(username: str) -> Dict[str, Any]:
#     """获取Twitter用户的完整信息，并行执行三个函数并存储结果到Redis DB 3"""
#     result = {}
    
#     # 记录日志
#     # logger.info(f"正在获取Twitter用户信息: {username}")
    
#     try:
        
#         # 获取当前时间戳
#         current_time = time.time()
        
#         # 先检查Redis是否已有完整信息
#         cached_info = twitter_redis.hgetall(f"twitter:{username}:complete_info")
        
#         # 如果有缓存数据且数据不超过1天，直接返回
#         if cached_info and "data" in cached_info and "timestamp" in cached_info:
#             cached_timestamp = float(cached_info["timestamp"])
#             if current_time - cached_timestamp < 86400:  # 数据不超过1天
#                 # logger.info(f"使用Redis缓存中的Twitter用户信息: {username} (缓存时间: {int(current_time - cached_timestamp)}秒)")
#                 try:
#                     return json.loads(cached_info["data"])
#                 except Exception as e:
#                     logger.error(f"解析Redis缓存的Twitter数据时出错: {str(e)}")
#                     # 解析失败，继续请求新数据
        
#         # 创建并行执行的线程
#         import concurrent.futures
        
#         def fetch_previous_name():
#             try:
#                 prev_name_info = get_previous_name(username)
#                 # 存储到Redis
#                 if prev_name_info:
#                     twitter_redis.hset(
#                         f"twitter:{username}:previous_names",
#                         mapping={
#                             "data": json.dumps(prev_name_info),
#                             "timestamp": current_time
#                         }
#                     )
#                 return prev_name_info
#             except Exception as e:
#                 logger.error(f"获取曾用名信息出错: {str(e)}")
#                 return None
        
#         def fetch_mentions_ca():
#             mentions_ca_info = None
#             try:
#                 mentions_ca_info = get_mentions_ca(username)
#                 if mentions_ca_info["success"]:
#                     if "data" in mentions_ca_info:
#                         mentioned_contracts_set = set(mentions_ca_info["data"])
#                         if mentioned_contracts_set:
#                             twitter_redis.sadd(f"twitter:{username}:mentioned_contracts_set", *mentioned_contracts_set)
#                 return mentions_ca_info 
#             except Exception as e:
#                 logger.error(f"获取提及CA信息出错: {str(e)}, {username}, {mentions_ca_info}")
#                 return None
        
#         def fetch_followers_info():
#             try:
#                 followers_info = get_twitter_user_followers(username)
#                 # 存储到Redis
#                 if followers_info:
#                     twitter_redis.hset(
#                         f"twitter:{username}:followers_info",
#                         mapping={
#                             "data": json.dumps(followers_info),
#                             "timestamp": current_time
#                         }
#                     )
#                 return followers_info
#             except Exception as e:
#                 logger.error(f"获取关注者信息出错: {str(e)}")
#                 return None
        
#         # 使用线程池并行执行三个函数
#         with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
#             # 提交任务到线程池
#             prev_name_future = executor.submit(fetch_previous_name)
#             mentions_ca_future = executor.submit(fetch_mentions_ca)
#             followers_future = executor.submit(fetch_followers_info)
            
#             # 等待所有任务完成并获取结果
#             prev_name_info = prev_name_future.result()
#             mentions_ca_info = mentions_ca_future.result()
#             followers_info = followers_future.result()
        
#         # 处理曾用名信息
#         if prev_name_info and prev_name_info.get("success"):
#             result["previous_names"] = prev_name_info.get("data", [])
        
#         # 处理提及的CA信息
#         if mentions_ca_info and mentions_ca_info.get("success"):
#             result["mentioned_contracts"] = len(mentions_ca_info.get("data", []))
        
#         # 处理关注者信息
#         if followers_info and followers_info.get("code") == 200:
#             kol_data = followers_info.get("data", {}).get("kolFollow", {})
#             result["kols"] = {
#                 "globalKolFollowersCount": kol_data.get("globalKolFollowersCount", 0),
#                 "cnKolFollowersCount": kol_data.get("cnKolFollowersCount", 0),
#                 "topKolFollowersCount": kol_data.get("topKolFollowersCount", 0),
#                 "globalKolFollowers": [f.get("username") for f in kol_data.get("globalKolFollowers", [])],
#                 "cnKolFollowers": [f.get("username") for f in kol_data.get("cnKolFollowers", [])],
#                 "topKolFollowers": [f.get("username") for f in kol_data.get("topKolFollowers", [])]
#             }
        
#         # 存储完整的合并结果到Redis
#         twitter_redis.hset(
#             f"twitter:{username}:complete_info",
#             mapping={
#                 "data": json.dumps(result),
#                 "timestamp": current_time
#             }
#         )
        
#         # 返回合并的结果
#         return result
        
#     except Exception as e:
#         logger.error(f"获取Twitter用户信息时出错: {str(e)}")
#         return {"error": str(e)}