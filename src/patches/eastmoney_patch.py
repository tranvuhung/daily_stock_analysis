import hashlib
import random
import secrets
import threading
import time
import requests
import json
import uuid
import logging
from fake_useragent import UserAgent

logger = logging.getLogger(__name__)

original_request = requests.Session.request

ua = UserAgent()


class AuthCache:
    def __init__(self):
        self.data = None
        self.expire_at = 0
        self.lock = threading.Lock()
        self.ttl = 20


_cache = AuthCache()


class PatchSign:
    def __init__(self):
        self.patched = False

    def set_patch(self, patched):
        self.patched = patched

    def is_patched(self):
        return self.patched


_patch_sign = PatchSign()


def _get_nid(user_agent):
    """
    Get the Eastmoney NID authorization token

    Args:
        user_agent (str): User-agent string used to simulate different browsers

    Returns:
        str: The obtained NID authorization token, or None if retrieval fails

    Description:
        Sends a request to Eastmoney's authorization endpoint to obtain the NID token,
        which authorizes subsequent data access. A cache avoids frequent requests.
    """
    now = time.time()
    # Check whether the cache is valid to avoid repeated requests
    if _cache.data and now < _cache.expire_at:
        return _cache.data
    # Use a thread lock for concurrency safety
    with _cache.lock:
        try:
            def generate_uuid_md5():
                """
                Generate a UUID and MD5-hash it
                :return: MD5 hash (32-character hex string)
                """
                # Generate a UUID
                unique_id = str(uuid.uuid4())
                # MD5-hash the UUID
                md5_hash = hashlib.md5(unique_id.encode('utf-8')).hexdigest()
                return md5_hash

            def generate_st_nvi():
                """
                Generate the st_nvi value
                :return: The generated st_nvi value
                """
                HASH_LENGTH = 4  # Number of leading hash characters to keep

                def generate_random_string(length=21):
                    """
                    Generate a random string of the given length
                    :param length: String length, defaults to 21
                    :return: Random string
                    """
                    charset = "useandom-26T198340PX75pxJACKVERYMINDBUSHWOLF_GQZbfghjklqvwyzrict"
                    return ''.join(secrets.choice(charset) for _ in range(length))

                def sha256(input_str):
                    """
                    Compute a SHA-256 hash
                    :param input_str: Input string
                    :return: Hash value (hex)
                    """
                    return hashlib.sha256(input_str.encode('utf-8')).hexdigest()

                random_str = generate_random_string()
                hash_prefix = sha256(random_str)[:HASH_LENGTH]
                return random_str + hash_prefix

            url = "https://anonflow2.eastmoney.com/backend/api/webreport"
            # Randomly pick a screen resolution to make the request look more realistic
            screen_resolution = random.choice(['1920X1080', '2560X1440', '3840X2160'])
            payload = json.dumps({
                "osPlatform": "Windows",
                "sourceType": "WEB",
                "osversion": "Windows 10.0",
                "language": "zh-CN",
                "timezone": "Asia/Shanghai",
                "webDeviceInfo": {
                    "screenResolution": screen_resolution,
                    "userAgent": user_agent,
                    "canvasKey": generate_uuid_md5(),
                    "webglKey": generate_uuid_md5(),
                    "fontKey": generate_uuid_md5(),
                    "audioKey": generate_uuid_md5()
                }
            })
            headers = {
                'Cookie': f'st_nvi={generate_st_nvi()}',
                'Content-Type': 'application/json'
            }
            # Add a timeout to avoid waiting indefinitely
            response = requests.request("POST", url, headers=headers, data=payload, timeout=30)
            response.raise_for_status()  # Raise HTTPError on 4xx/5xx responses

            data = response.json()
            nid = data['data']['nid']

            _cache.data = nid
            _cache.expire_at = now + _cache.ttl
            return nid
        except requests.exceptions.RequestException as e:
            logger.warning(f"请求东方财富授权接口失败: {e}")
            _cache.data = None
            # If this endpoint fails, the approach may be broken and will likely keep failing; since retrieval failed it would be retried next time, so set a long expiry to avoid frequent requests
            _cache.expire_at = now + 5 * 60
            return None
        except (KeyError, json.JSONDecodeError) as e:
            logger.warning(f"解析东方财富授权接口响应失败: {e}")
            _cache.data = None
            # If this endpoint fails, the approach may be broken and will likely keep failing; since retrieval failed it would be retried next time, so set a long expiry to avoid frequent requests
            _cache.expire_at = now + 5 * 60
            return None


def eastmoney_patch():
    if _patch_sign.is_patched():
        return

    def patched_request(self, method, url, **kwargs):
        # Skip non-target domains
        is_target = any(
            d in (url or "")
            for d in [
                "fund.eastmoney.com",
                "push2.eastmoney.com",
                "push2his.eastmoney.com",
            ]
        )
        if not is_target:
            return original_request(self, method, url, **kwargs)
        # Get a random User-Agent
        user_agent = ua.random
        # Handle headers: make sure not to break headers passed in by business code
        headers = kwargs.get("headers", {})
        headers["User-Agent"] = user_agent
        nid = _get_nid(user_agent)
        if nid:
            headers["Cookie"] = f"nid18={nid}"
        kwargs["headers"] = headers
        # Random sleep to reduce the risk of being blocked
        sleep_time = random.uniform(1, 4)
        time.sleep(sleep_time)
        return original_request(self, method, url, **kwargs)

    # Globally replace the Session request entry point
    requests.Session.request = patched_request
    _patch_sign.set_patch(True)
