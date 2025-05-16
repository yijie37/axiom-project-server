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
        print(f"Error: {response.status_code}")
        print(response.text)
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
        print(f"Error: {response.status_code}")
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
        print(f"Error: {response.status_code}")
        print(response.text)
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
        return response.json()
    else:
        print(f"Error: {response.status_code}")
        print(response.text)
        return None
