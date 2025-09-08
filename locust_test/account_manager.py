"""
账号管理器模块
负责管理测试账号池、登录控制和账号分配
"""
import time
import logging
import threading
import requests
from typing import Dict, Optional, Any
from queue import Queue
from config import (
    TEST_ACCOUNT_API, AUTH_TOKEN, LOGIN_ENDPOINT, 
    MAX_LOGIN_ATTEMPTS, MAX_ACCOUNT_ATTEMPTS, ACCOUNT_POOL_TIMEOUT_MULTIPLIER
)

logger = logging.getLogger(__name__)


class AccountManager:
    """账号管理器，负责账号池管理和登录控制"""
    
    def __init__(self):
        self.account_pool = Queue()
        self.account_pool_lock = threading.Lock()
        self.login_counter = 0
        self.login_counter_lock = threading.Lock()
        self.all_accounts_ready = threading.Event()
        
        logger.info("账号管理器初始化完成")

    
    def get_login_index(self, target_accounts: int) -> Optional[int]:
        """获取登录索引，控制登录顺序"""
        with self.login_counter_lock:
            if self.login_counter >= target_accounts:
                return None
            current_index = self.login_counter
            self.login_counter += 1
            return current_index
    
    def get_and_login_single_account(self, user_number: str, client) -> Optional[Dict[str, str]]:
        """获取并登录单个测试账号"""
        for attempt in range(MAX_ACCOUNT_ATTEMPTS):
            try:
                # 获取测试账号
                account_response = requests.post(
                    TEST_ACCOUNT_API,
                    headers={"Authorization": f"Bearer {AUTH_TOKEN}"},
                    timeout=10
                )
                account_response.raise_for_status()
                
                # 检查响应内容
                response_text = account_response.text
                logger.debug(f"{user_number} API响应内容: {response_text[:200]}...")
                
                if not response_text.strip():
                    logger.warning(f"{user_number} API返回空响应")
                    continue
                    
                # 解析纯文本响应格式
                # 格式: 手机号：14044330949\n验证码：478908
                try:
                    account_data = account_response.json()
                    # 如果是JSON格式，按原逻辑处理
                    if not account_data.get("success"):
                        logger.warning(f"{user_number} 获取测试账号失败: {account_data.get('message', '未知错误')}")
                        continue
                    test_account = account_data.get("data", {})
                    username = test_account.get("username")
                    password = test_account.get("password")
                except ValueError:
                    # 解析纯文本格式
                    logger.info(f"{user_number} 解析纯文本格式的测试账号响应")
                    lines = response_text.strip().split('\n')
                    username = None
                    password = None
                    
                    for line in lines:
                        if '手机号：' in line:
                            username = line.split('手机号：')[1].strip()
                        elif '验证码：' in line:
                            password = line.split('验证码：')[1].strip()
                    
                    if not username or not password:
                        logger.warning(f"{user_number} 无法从响应中解析账号信息")
                        logger.warning(f"{user_number} 响应内容: {response_text}")
                        continue
                
                if not username or not password:
                    logger.warning(f"{user_number} 测试账号数据不完整")
                    continue
                
                # 登录账号
                for login_attempt in range(MAX_LOGIN_ATTEMPTS):
                    try:
                        login_data = {
                            "phone_number": username,  # 手机号
                            "verification_code": password  # 验证码
                        }
                        
                        logger.info(f"{user_number} 登录请求数据: {login_data}")
                        
                        response = client.post(
                            LOGIN_ENDPOINT,
                            json=login_data,
                            name="用户登录"
                        )
                        response.raise_for_status()
                        login_result = response.json()
                        
                        # 记录详细的登录响应信息
                        logger.debug(f"{user_number} 登录响应: {login_result}")
                        
                        if login_result.get("status") and login_result.get("code") == 200:
                            access_token = login_result.get("data", {}).get("access_token")
                            if access_token:
                                logger.info(f"{user_number} 登录成功: {username}")
                                return {
                                    "username": username,
                                    "password": password,
                                    "access_token": access_token
                                }
                            else:
                                logger.warning(f"{user_number} 登录响应中缺少access_token")
                        else:
                            logger.warning(f"{user_number} 登录失败: {login_result.get('message', '未知错误')}")
                            
                    except Exception as e:
                        logger.warning(f"{user_number} 登录尝试 {login_attempt + 1} 失败: {str(e)}")
                        if login_attempt < MAX_LOGIN_ATTEMPTS - 1:
                            time.sleep(1)
                
            except Exception as e:
                logger.warning(f"{user_number} 获取账号尝试 {attempt + 1} 失败: {str(e)}")
                if attempt < MAX_ACCOUNT_ATTEMPTS - 1:
                    time.sleep(1)
        
        logger.error(f"{user_number} 获取并登录账号失败，已尝试 {MAX_ACCOUNT_ATTEMPTS} 次")
        return None
    
    def add_account_to_pool(self, account_data: Dict[str, str], user_number: str, target_accounts: int, required_users: int = None) -> None:
        """将账号添加到账号池"""
        with self.account_pool_lock:
            self.account_pool.put(account_data)
            current_size = self.account_pool.qsize()
            logger.info(f"{user_number} 账号已添加到池中，当前数量: {current_size}/{target_accounts}")
            
            # 检查是否达到目标用户数
            if required_users is None:
                required_users = min(target_accounts, 10)  # 默认最多等待10个用户
            
            if current_size >= required_users:
                self.all_accounts_ready.set()
                logger.info(f"账号池已就绪，当前有 {current_size} 个账号，满足 {required_users} 个用户需求")

    def wait_for_accounts_ready(self, user_number: str, required_users: int, login_interval: float) -> bool:
        """等待账号池达到足够数量的账号"""
        if self.all_accounts_ready.is_set():
            logger.info(f"{user_number} 账号池已就绪，开始后续步骤")
            return True

        logger.info(f"{user_number} 等待账号池达标（当前: {self.account_pool.qsize()}/{required_users}）")
        timeout = required_users * login_interval * ACCOUNT_POOL_TIMEOUT_MULTIPLIER
        self.all_accounts_ready.wait(timeout=timeout)

        if not self.all_accounts_ready.is_set():
            logger.error(f"{user_number} 等待超时，账号池未达标（需要 {required_users} 个账号）")
            return False
        
        return True


    def get_valid_account(self, user_number: str, target_accounts: int) -> Optional[Dict[str, str]]:
        """从账号池获取有效账号"""
        with self.account_pool_lock:
            if self.account_pool.empty():
                logger.warning(f"{user_number} 账号池为空，无法获取账号")
                return None
            
            account = self.account_pool.get()
            logger.info(f"{user_number} 获取账号: {account['username']}")
            return account


# 全局账号管理器实例
account_manager = AccountManager()
