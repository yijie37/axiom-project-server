import requests
import os
import json
import redis
import time
from dotenv import load_dotenv
from datetime import datetime

# Load environment variables from .env.local
load_dotenv('.env.local')

def update_redis_user_info(redis_client, user_data):
    # Extract user data
    handle = user_data.get('userName', '')
    if not handle:
        return
    
    # Get existing data if any
    existing_data = redis_client.get(handle)
    base_data = {
        "handle": handle,
        "id": user_data.get('id', ''),
        "name": user_data.get('name', ''),
        "bio": user_data.get('description', ''),
        "followingCount": user_data.get('following', 0),
        "followerCount": user_data.get('followers', 0),
        "followedByFollowingCount": 0,  # This field is not available in the API response
        "bannerImageUrl": user_data.get('coverPicture', ''),
        "update_time": int(datetime.now().timestamp())
    }
    
    if existing_data:
        existing_json = json.loads(existing_data)
        # Preserve followedByFollowingCount if it exists
        base_data['followedByFollowingCount'] = existing_json.get('followedByFollowingCount', 0)
        # Preserve title if it exists
        if 'title' in existing_json:
            base_data['title'] = existing_json['title']
        # Preserve counts if they exist
        if 'counts' in existing_json:
            base_data['counts'] = existing_json['counts']
    
    # Update Redis
    redis_client.set(handle, json.dumps(base_data))

def get_twitter_user_info(username):
    url = "https://api.twitterapi.io/twitter/user/info"
    
    querystring = {"userName": username}
    
    # Get API key from environment variable
    api_key = os.getenv('TWITTER_API_KEY')
    if not api_key:
        raise ValueError("TWITTER_API_KEY not found in environment variables")
    
    headers = {"X-API-Key": api_key}
    
    try:
        response = requests.request("GET", url, headers=headers, params=querystring)
        response.raise_for_status()
        result = response.json()
        
        # Update Redis if the request was successful
        if result.get('status') == 'success' and 'data' in result:
            redis_client = redis.Redis(db=7)
            update_redis_user_info(redis_client, result['data'])
            redis_client.close()
        
        return result
    except requests.exceptions.RequestException as e:
        print(f"Error occurred: {e}")
        return None

if __name__ == "__main__":
    start_time = time.time()
    result = get_twitter_user_info("elonmusk")
    end_time = time.time()
    print(f"Time taken: {end_time - start_time} seconds")
    if result:
        print(json.dumps(result, indent=2))