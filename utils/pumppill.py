import requests
import json
import time
import redis
from datetime import datetime

def get_counts_from_data(data):
    counts = {
        "names_count": data.get("names_count", 0),
        "pump_tweets_count": data.get("pump_tweets_count", 0),
        "deleted_pump_tweets_count": data.get("deleted_pump_tweets_count", 0),
        "odin_tweet_count": data.get("odin_tweet_count", 0),
        "deleted_odin_tweet_count": data.get("deleted_odin_tweet_count", 0),
        "four_tweet_count": data.get("four_tweet_count", 0),
        "deleted_four_tweet_count": data.get("deleted_four_tweet_count", 0),
        "followed_kol_count": data.get("followed_kol_count", 0),
        "boop_count": data.get("boop_count", 0),
        "deleted_boop_count": data.get("deleted_boop_count", 0),
        "believe_count": data.get("believe_count", 0),
        "deleted_believe_count": data.get("deleted_believe_count", 0)
    }
    return counts

def update_redis_with_pump_data(redis_client, screen_name, user_id, data):
    # Get existing data if any
    existing_data = redis_client.get(screen_name)
    base_data = {
        "handle": screen_name,
        "id": user_id,
        "name": "",
        "bio": "",
        "followingCount": 0,
        "followerCount": 0,
        "followedByFollowingCount": 0,
        "bannerImageUrl": "",
        "update_time": int(datetime.now().timestamp()),
        "followers": [data["screen_name"] for data in data.get("followed_kols", [])]
    }
    
    if existing_data:
        base_data.update(json.loads(existing_data))
    
    # Update with new data
    base_data.update({
        "title": data.get("title", ""),
        "counts": get_counts_from_data(data)
    })
    
    # Update Redis
    redis_client.set(screen_name, json.dumps(base_data))
    
    # Process followed_kols
    for kol in data.get("followed_kols", []):
        # Get existing KOL data if any
        existing_kol_data = redis_client.get(kol["screen_name"])
        kol_base_data = {
            "handle": kol.get("screen_name", ""),
            "id": kol.get("id_str", ""),
            "name": kol.get("name", ""),
            "bio": kol.get("description", ""),
            "followingCount": kol.get("friends_count", 0),
            "followerCount": kol.get("followers_count", 0),
            "title": kol.get("title", ""),
            "bannerImageUrl": "",
            "update_time": int(datetime.now().timestamp()),
        }
        
        # If KOL data exists, preserve existing data
        if existing_kol_data:
            kol_base_data.update(json.loads(existing_kol_data))
        
        # Update with new data while preserving bannerImageUrl
        kol_base_data.update({
            "handle": kol.get("screen_name", ""),
            "id": kol.get("id_str", ""),
            "name": kol.get("name", ""),
            "bio": kol.get("description", ""),
            "followingCount": kol.get("friends_count", 0),
            "followerCount": kol.get("followers_count", 0),
            "title": kol.get("title", ""),
            "update_time": int(datetime.now().timestamp())
        })
        
        redis_client.set(kol["screen_name"], json.dumps(kol_base_data))

def send_pump_pill_request(screen_name, user_id, sign):
    url = "https://pumpscam.com/api/v1/pumpPill"
    
    headers = {
        "authority": "pumpscam.com",
        "accept": "*/*",
        "accept-encoding": "gzip, deflate, br, zstd",
        "accept-language": "zh-CN,zh;q=0.9,en-US;q=0.8,en;q=0.7",
        "content-type": "application/json",
        "origin": "chrome-extension://fhfmioonhaaojhlicfalhidddcpcdogf",
        "priority": "u=1, i",
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "cross-site",
        "user-agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36",
        "x-code": "16inx7lzfcffm7dzg336hey1ysfujz"
    }
    
    payload = {
        "screen_name": screen_name,
        "user_id": user_id,
        "sign": sign
    }
    
    try:
        t0 = time.time()
        response = requests.post(url, headers=headers, json=payload)
        t1 = time.time()
        response.raise_for_status()  # Raise an exception for bad status codes
        result = response.json()
        
        # Update Redis with the response data
        if result.get("code") == 1 and "data" in result:
            redis_client = redis.Redis(db=7)
            update_redis_with_pump_data(redis_client, screen_name, user_id, result["data"])
            redis_client.close()
            
        return result
    except requests.exceptions.RequestException as e:
        print(f"Error occurred: {e}")
        return None

if __name__ == "__main__":
    start_time = time.time()
    result = send_pump_pill_request("elonmusk", "44196397", "16b8c2b20a636d6aede09d6e509f7b46ea03cd7d0d37abd73bff9ad7ddc8a235")
    print("result:", result)
    end_time = time.time()
    print(f"Time taken: {end_time - start_time} seconds")
    # if result:
        # print("Response:", json.dumps(result, indent=2))
