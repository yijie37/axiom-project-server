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

def preprocess_creator_data(creator_data):
    """
    Preprocess creator data to match the expected format in update_redis_user_info
    """
    return {
        'userName': creator_data.get('screen_name', ''),
        'id': creator_data.get('id', ''),
        'name': creator_data.get('name', ''),
        'description': creator_data.get('description', ''),
        'following': creator_data.get('following_count', 0),
        'followers': creator_data.get('followers_count', 0),
        'coverPicture': creator_data.get('profile_banner_url', '')
    }

def get_twitter_community_info(community_id):
    url = "https://api.twitterapi.io/twitter/community/info"
    
    querystring = {"community_id": community_id}
    
    # Get API key from environment variable
    api_key = "2bf06adbe2a746deb2f4229dc60a935d"
    # api_key = os.getenv('TWITTER_API_KEY')
    if not api_key:
        raise ValueError("TWITTER_API_KEY not found in environment variables")
    
    headers = {"X-API-Key": api_key}
    
    try:
        response = requests.request("GET", url, headers=headers, params=querystring)
        response.raise_for_status()
        result = response.json()
        
        if result.get('status') == 'success' and 'community_info' in result:
            community_info = result['community_info']
            
            # Process creator information and update Redis
            if 'creator' in community_info:
                redis_client = redis.Redis(db=7)
                # Preprocess creator data before updating Redis
                processed_creator_data = preprocess_creator_data(community_info['creator'])
                update_redis_user_info(redis_client, processed_creator_data)
                redis_client.close()
            
            # Extract and format community and creator information
            info = {
                'comm_name': community_info['name'],
                'comm_desc': community_info['description'],
                'comm_banner_url': community_info['banner_url'],
                'member_count': community_info['member_count'],
                'mod_count': community_info['moderator_count'],
                'creator_name': community_info['creator']['name'],
                'creator_description': community_info['creator']['description'],
                'creator_followers_count': community_info['creator']['followers_count'],
                'creator_statuses_count': community_info['creator']['statuses_count'],
                'creator_avtar': community_info['creator']['profile_image_url_https']
            }
            
            return {
                'status': 'success',
                'community_info': info,
                'raw_data': result
            }
        
        return result
    except requests.exceptions.RequestException as e:
        print(f"Error occurred: {e}")
        return None

if __name__ == "__main__":
    # Test community info
    start_time = time.time()
    result = get_twitter_community_info("1931138282429054982")
    end_time = time.time()
    print(f"Time taken: {end_time - start_time} seconds")
    if result:
        print(json.dumps(result['community_info'], indent=2))