from locust import HttpUser, task, between, events
import websocket
import requests
import logging
import re
import threading
import time
import ssl
from queue import Empty, Queue
from enum import Enum
import uuid
import random
from websocket import WebSocketApp
from locust.runners import WorkerRunner
from urllib.parse import quote
from threading import Lock
import base64
import sys


# -------------------------- 新增：WebSocket关闭状态码解析 --------------------------
def get_close_reason(status_code):
    """根据WebSocket标准关闭状态码返回原因说明"""
    reason_map = {
        1000: "正常关闭（双方完成通信）",
        1001: "端点离开（如浏览器导航离开）",
        1002: "协议错误（端点收到不符合协议的消息）",
        1003: "不支持的数据类型（端点收到不支持的消息类型）",
        1004: "预留状态码（无具体含义）",
        1005: "无状态码（表示未收到关闭帧）",
        1006: "连接异常断开（可能是网络问题或服务器崩溃）",
        1007: "消息格式错误（如文本消息包含非UTF-8数据）",
        1008: "消息内容违规（违反服务器政策）",
        1009: "消息过大（服务器无法处理）",
        1010: "客户端需要扩展（服务器不支持所需扩展）",
        1011: "服务器内部错误（处理消息时发生错误）",
        1012: "服务重启（服务器因重启关闭连接）",
        1013: "暂时不可用（服务器因过载关闭连接）",
        1014: "错误的网关响应（从上游服务器收到无效响应）",
        1015: "TLS握手失败（加密连接建立失败）"
    }
    return reason_map.get(status_code, f"未知状态码（{status_code}）")


# -------------------------- 日志优先配置 --------------------------
from default_config import LOG_LEVEL as DEFAULT_LOG_LEVEL
from default_config import LOG_FORMAT, LOG_ENCODING

logging.basicConfig(
    level=getattr(logging, DEFAULT_LOG_LEVEL),
    format=LOG_FORMAT,
    encoding=LOG_ENCODING
)
logger = logging.getLogger(__name__)

# -------------------------- 配置加载 --------------------------
try:
    from config import *

    logging.getLogger().setLevel(getattr(logging, LOG_LEVEL))
    logger.info("成功加载用户配置文件 config.py")
except ImportError:
    logger.warning("未找到配置文件 config.py，使用默认配置")
    from default_config import *


# -------------------------- 全局状态管理 --------------------------
class Config:
    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                cls._instance = super().__new__(cls)
                cls._instance.target_users = 10
                cls._instance.ws_connect_interval = DEFAULT_WS_CONNECT_INTERVAL
        return cls._instance


config = Config()

# 账号池及线程安全控制
account_pool = Queue()
account_pool_lock = threading.Lock()
login_counter = 0
login_counter_lock = threading.Lock()
all_accounts_ready = threading.Event()

# 用户计数相关
user_number_counter = 0
user_number_lock = threading.Lock()
active_users = 0
active_users_lock = threading.Lock()
spawned_user_count = 0
spawned_user_lock = threading.Lock()
initial_users_spawned = False
initial_users_spawned_lock = threading.Lock()

# 连接速度控制
last_connect_time = 0.0
connect_lock = Lock()


# -------------------------- WebSocket连接统计（增强版） --------------------------
class WebSocketStats:
    """增强版统计器，包含断开原因分类计数"""

    def __init__(self):
        self.active_connections = 0
        self.total_connections = 0
        self.target_connections = 0
        self.failed_connections = 0
        self.reconnect_count = 0

        # 新增：断开原因分类统计
        self.normal_closures = 0  # 正常关闭（1000）
        self.protocol_errors = 0  # 协议错误（1002等）
        self.server_initiated = 0  # 服务器主动关闭（1011-1014等）
        self.network_errors = 0  # 网络问题（1006等）
        self.unknown_closures = 0  # 未知原因

        self.lock = Lock()

    def set_target(self, target):
        with self.lock:
            self.target_connections = target
            logger.info(f"目标连接数已设置为：{target}")

    def increment(self):
        with self.lock:
            self.active_connections += 1
            self.total_connections += 1
            return self.active_connections, self.total_connections

    def decrement(self):
        with self.lock:
            if self.active_connections > 0:
                self.active_connections -= 1
            return self.active_connections

    def increment_failed(self):
        with self.lock:
            self.failed_connections += 1
            return self.failed_connections

    def increment_reconnect(self):
        with self.lock:
            self.reconnect_count += 1
            return self.reconnect_count

    # 新增：根据状态码更新断开原因统计
    def update_close_reason(self, status_code):
        with self.lock:
            if status_code == 1000:
                self.normal_closures += 1
            elif status_code in (1002, 1003, 1007, 1008, 1009, 1010):
                self.protocol_errors += 1
            elif status_code in (1011, 1012, 1013, 1014):
                self.server_initiated += 1
            elif status_code in (1006, 1015):
                self.network_errors += 1
            else:
                self.unknown_closures += 1

    def get_stats(self):
        with self.lock:
            return {
                "target": self.target_connections,
                "active": self.active_connections,
                "total": self.total_connections,
                "failed": self.failed_connections,
                "reconnects": self.reconnect_count,
                # 新增：断开原因统计
                "closures": {
                    "normal": self.normal_closures,
                    "protocol_errors": self.protocol_errors,
                    "server_initiated": self.server_initiated,
                    "network_errors": self.network_errors,
                    "unknown": self.unknown_closures
                }
            }


ws_stats = WebSocketStats()


# -------------------------- 连接状态枚举 --------------------------
class ConnectionState(Enum):
    INIT = 0
    LOGGING_IN = 1
    READY = 2
    PROCESSING = 3
    DONE = 4


# -------------------------- 自定义命令行参数 --------------------------
@events.init_command_line_parser.add_listener
def _(parser):
    parser.add_argument(
        "--login-interval",
        type=float,
        default=DEFAULT_LOGIN_INTERVAL,
        help="登录间隔（秒/个）"
    )
    parser.add_argument(
        "--account-multiplier",
        type=float,
        default=DEFAULT_ACCOUNT_MULTIPLIER,
        help="账号数量乘数（总账号数 = 用户数 × 乘数）"
    )
    parser.add_argument(
        "--ws-connect-interval",
        type=float,
        default=DEFAULT_WS_CONNECT_INTERVAL,
        help="WebSocket连接间隔（秒/个）"
    )
    parser.add_argument(
        "--stats-interval",
        type=float,
        default=DEFAULT_STATS_INTERVAL,
        help="WebSocket连接统计打印间隔（秒）"
    )


# -------------------------- 主测试类 --------------------------
class UserBehavior(HttpUser):
    wait_time = between(5, 10)
    host = BASE_API_URL

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # 分配用户编号
        global user_number_counter
        with user_number_lock:
            user_number_counter += 1
            self.user_number = f"user_{user_number_counter}"

        self.global_user_id = str(uuid.uuid4())
        self.state = ConnectionState.INIT
        self.account_data = None
        self.ws_connected = False
        self.ws_retry_count = 0
        self.user_info = {}
        self.lesson_info = {}
        self.current_lesson_params = None
        self.lesson_completed = False  # 新增：标记课程是否正常结束

        # 读取命令行参数
        self.login_interval = self.environment.parsed_options.login_interval
        self.account_multiplier = self.environment.parsed_options.account_multiplier
        self.stats_interval = self.environment.parsed_options.stats_interval
        config.ws_connect_interval = self.environment.parsed_options.ws_connect_interval

        # 启动统计打印线程（仅由第一个用户启动）
        if self.user_number == "user_1":
            self._start_stats_printer()

        # 更新目标用户数
        with initial_users_spawned_lock:
            config.target_users = self.environment.runner.target_user_count if hasattr(
                self.environment.runner, 'target_user_count') else 10

        self.target_accounts = max(1, int(config.target_users * self.account_multiplier))
        self._increment_active_users()

        logger.info(
            f"{self.user_number} 初始化：用户数={config.target_users}，账号数={self.target_accounts}，"
            f"连接间隔={config.ws_connect_interval}秒")

        # 检查初始用户是否全部生成（基于计数器而非编号顺序）
        global spawned_user_count, initial_users_spawned
        with spawned_user_lock:
            spawned_user_count += 1
            current_spawned = spawned_user_count
        with initial_users_spawned_lock:
            if current_spawned >= config.target_users and not initial_users_spawned:
                initial_users_spawned = True
                ws_stats.set_target(config.target_users)
                logger.info(f"初始 {config.target_users} 个用户已全部启动（实际初始化 {current_spawned} 个）")

        # 启动用户检查线程（仅第一个用户）
        if self.user_number == "user_1":
            self._start_user_check_thread()

    def _start_stats_printer(self):
        """启动定时打印WebSocket连接统计的线程（包含断开原因）"""

        def print_stats_loop():
            while True:
                stats = ws_stats.get_stats()
                closures = stats["closures"]
                logger.info(
                    f"=== WebSocket连接统计 === \n"
                    f"目标: {stats['target']} | 活跃: {stats['active']} | 累计: {stats['total']}\n"
                    f"失败: {stats['failed']} | 重连: {stats['reconnects']}\n"
                    f"断开原因统计:\n"
                    f"  正常关闭: {closures['normal']}\n"
                    f"  协议错误: {closures['protocol_errors']}\n"
                    f"  服务器主动关闭: {closures['server_initiated']}\n"
                    f"  网络问题: {closures['network_errors']}\n"
                    f"  未知原因: {closures['unknown']}\n"
                    f"======================="
                )
                time.sleep(self.stats_interval)

        self.stats_thread = threading.Thread(target=print_stats_loop, daemon=True)
        self.stats_thread.start()
        logger.info(f"统计打印线程启动，间隔：{self.stats_interval}秒")

    def _increment_active_users(self):
        """增加活跃用户计数"""
        global active_users
        with active_users_lock:
            active_users += 1
            logger.info(f"活跃用户数增加：{active_users}/{config.target_users}")

    def _decrement_active_users(self):
        """减少活跃用户计数"""
        global active_users
        with active_users_lock:
            active_users -= 1
            current_active = active_users
            logger.info(f"活跃用户数减少：{current_active}/{config.target_users}")

    def _start_user_check_thread(self):
        """启动用户数量检查线程"""

        def check_user_loop():
            while True:
                with initial_users_spawned_lock:
                    if initial_users_spawned:
                        self._check_and_spawn_users()
                    else:
                        logger.debug("初始用户未全部启动，跳过用户检查")
                time.sleep(USER_CHECK_INTERVAL)

        self.user_check_thread = threading.Thread(target=check_user_loop, daemon=True)
        self.user_check_thread.start()
        logger.info(f"用户数量检查线程已启动，检查间隔：{USER_CHECK_INTERVAL}秒")

    def _check_and_spawn_users(self):
        """检查活跃用户数，必要时生成新用户"""
        try:
            with active_users_lock:
                current_active = active_users
                deficit = config.target_users - current_active
                if deficit > 0:
                    logger.info(
                        f"用户数量检查：当前活跃 {current_active}/{config.target_users}，需要补充 {deficit} 个用户")
                    self._spawn_new_users(deficit)
                else:
                    logger.debug(f"用户数量检查：当前活跃 {current_active}/{config.target_users}，无需补充")
        except Exception as e:
            logger.error(f"用户数量检查失败：{str(e)}")

    def _spawn_new_users(self, count):
        """生成指定数量的新用户"""
        try:
            if not isinstance(self.environment.runner, WorkerRunner):
                logger.info(f"=== 开始补充用户流程：需要补充 {count} 个 ===")
                current_target = self.environment.runner.target_user_count
                new_target = current_target + count
                logger.info(f"将官方目标用户数从 {current_target} 调整为 {new_target}")
                self.environment.runner.target_user_count = new_target

                # 更新全局目标用户数和连接数
                with initial_users_spawned_lock:
                    config.target_users = new_target
                ws_stats.set_target(config.target_users)

                start_time = time.time()
                timeout = 30
                logger.info(f"等待新用户生成（超时 {timeout} 秒）")

                while True:
                    if hasattr(self.environment.runner, 'user_count'):
                        official_user_count = self.environment.runner.user_count
                    else:
                        official_user_count = sum(len(users) for users in self.environment.runner.user_classes)

                    with active_users_lock:
                        current_active = active_users
                    logger.info(
                        f"等待中：官方统计 {official_user_count}，本地活跃 {current_active}/{new_target}（已等 {time.time() - start_time:.1f} 秒）")

                    if official_user_count >= new_target:
                        logger.info(f"Locust已生成新用户：{official_user_count}/{new_target}")
                        break
                    if time.time() - start_time > timeout:
                        logger.warning(f"超时未生成新用户：{official_user_count}/{new_target}")
                        break
                    time.sleep(2)

                logger.info(f"=== 补充用户流程结束 ===")

        except Exception as e:
            logger.error(f"补充用户失败：{str(e)}", exc_info=True)

    def on_start(self):
        """用户启动时执行的初始化流程"""
        logger.info(f"{self.user_number} 启动，进入初始化状态")
        self.login_and_cache_account()
        self.wait_for_accounts_ready()
        self.execute_post_login_steps()
        if self.state == ConnectionState.PROCESSING:
            self._start_websocket_thread()

    def on_stop(self):
        """Locust用户实例停止时调用，确保活跃用户数准确减少"""
        self._decrement_active_users()
        logger.info(f"{self.user_number} 实例停止，执行on_stop清理")

    def login_and_cache_account(self):
        """登录并缓存账号信息到账号池"""
        global login_counter
        with login_counter_lock:
            if login_counter >= self.target_accounts:
                self.state = ConnectionState.READY
                return
            current_login_index = login_counter
            login_counter += 1

        self.state = ConnectionState.LOGGING_IN
        logger.info(f"{self.user_number} 负责第 {current_login_index + 1}/{self.target_accounts} 个账号登录")

        # 登录时间控制
        login_time = time.time() + current_login_index * self.login_interval
        sleep_time = login_time - time.time()
        if sleep_time > 0:
            logger.info(f"{self.user_number} 等待 {sleep_time:.1f} 秒后登录")
            time.sleep(sleep_time)

        account = self.get_and_login_single_account()
        if account:
            with account_pool_lock:
                account_pool.put(account)
                logger.info(
                    f"{self.user_number} 登录成功，账号池当前数量: {account_pool.qsize()}/{self.target_accounts}")
                if account_pool.qsize() >= config.target_users:
                    all_accounts_ready.set()
        else:
            logger.error(f"{self.user_number} 登录失败，账号池数量不足")
        self.state = ConnectionState.READY

    def wait_for_accounts_ready(self):
        """等待账号池达到足够数量的账号"""
        if all_accounts_ready.is_set():
            logger.info(f"{self.user_number} 账号池已就绪，开始后续步骤")
            return

        logger.info(f"{self.user_number} 等待账号池达标（当前: {account_pool.qsize()}/{self.target_accounts}）")
        timeout = self.target_accounts * self.login_interval * ACCOUNT_POOL_TIMEOUT_MULTIPLIER
        all_accounts_ready.wait(timeout=timeout)

        if not all_accounts_ready.is_set():
            logger.error(f"{self.user_number} 等待超时，账号池未达标")
            raise Exception("账号池数量未达标，终止测试")

    def execute_post_login_steps(self):
        """执行登录后的核心流程：获取账号→验证有效性→随机选课程→开始课程"""
        self.state = ConnectionState.PROCESSING

        # 从账号池获取有效账号（带有效性校验）
        for attempt in range(MAX_ACCOUNT_ATTEMPTS):
            try:
                self.account_data = account_pool.get(block=False)
                logger.info(f"{self.user_number} 获取账号: {self.account_data['phone_number']}")

                # 验证账号token有效性
                if not self.get_user_profile_once():
                    logger.warning(f"{self.user_number} 账号token无效，丢弃该账号")
                    self.account_data = None
                    continue
                break  # 账号有效，退出循环
            except Empty:
                logger.warning(f"{self.user_number} 等待账号池有可用账号（尝试 {attempt + 1}/{MAX_ACCOUNT_ATTEMPTS}）")
                time.sleep(1)
        else:
            logger.error(f"{self.user_number} 尝试多次后仍无法获取有效账号")
            self.state = ConnectionState.DONE
            return

        try:
            # 随机选择课程并开始
            if not self.start_lesson_with_random_params():
                raise Exception("开始课程失败")

            logger.info(f"{self.user_number} 前置步骤执行完成，进入测试阶段")
        except Exception as e:
            logger.error(f"{self.user_number} 前置步骤失败: {str(e)}")
            self.state = ConnectionState.DONE
        finally:
            if self.account_data:
                account_pool.put(self.account_data)

    def get_and_login_single_account(self):
        """获取并登录单个测试账号"""
        for attempt in range(MAX_LOGIN_ATTEMPTS):
            try:
                headers = {"Authorization": f"Bearer {AUTH_TOKEN}"}
                response = requests.post(
                    TEST_ACCOUNT_API,
                    headers=headers,
                    json={},
                    timeout=10
                )
                response.raise_for_status()
                content = response.text.strip()

                phone_match = re.search(r'手机号：(\d{11})', content)
                code_match = re.search(r'验证码：(\d{6})', content)
                if not phone_match or not code_match:
                    logger.warning(f"{self.user_number} 账号格式错误（第{attempt + 1}次）：{content}")
                    continue

                login_response = self.client.post(
                    LOGIN_ENDPOINT,
                    json={
                        "phone_number": phone_match.group(1),
                        "verification_code": code_match.group(1)
                    },
                    name="用户登录"
                )
                login_response.raise_for_status()
                login_data = login_response.json()
                access_token = login_data.get("data", {}).get("access_token")
                if not access_token:
                    logger.error(f"{self.user_number} 未获取到access_token（第{attempt + 1}次）")
                    continue

                return {
                    "phone_number": phone_match.group(1),
                    "verification_code": code_match.group(1),
                    "access_token": access_token
                }
            except Exception as e:
                logger.error(f"{self.user_number} 登录失败（第{attempt + 1}次）：{str(e)}")
                time.sleep(1)
        return None

    def get_user_profile_once(self):
        """获取用户个人信息（同时验证token有效性）"""
        headers = {"Authorization": f"Bearer {self.account_data['access_token']}"}
        try:
            response = self.client.get(
                USER_PROFILE_ENDPOINT,
                headers=headers,
                name="获取用户信息"
            )
            response.raise_for_status()
            data = response.json()
            user_data = data.get("data", {})
            if isinstance(user_data, list) and len(user_data) > 0:
                user_data = user_data[0]

            if not isinstance(user_data, dict):
                logger.error(f"{self.user_number} 用户信息格式错误：{user_data}")
                return False

            self.user_info = {
                "english_name": user_data.get("english_name"),
                "member_id": user_data.get("member_id")
            }
            logger.info(f"{self.user_number} 获取信息成功：{self.user_info}")
            return True
        except Exception as e:
            logger.error(f"{self.user_number} 获取信息失败（token可能过期）：{str(e)}")
            return False

    def start_lesson_with_random_params(self):
        """随机选择课程参数并开始课程"""
        self.current_lesson_params = random.choice(LESSON_PARAMS_LIST)
        logger.info(f"{self.user_number} 随机选择课程参数: {self.current_lesson_params['material_id']}")

        headers = {"Authorization": f"Bearer {self.account_data['access_token']}"}
        try:
            response = self.client.post(
                START_LESSON_ENDPOINT,
                json=self.current_lesson_params,
                headers=headers,
                name="开始课程"
            )
            response.raise_for_status()
            data = response.json()
            lesson_data = data.get("data", {})

            self.lesson_info = {
                "lesson_id": lesson_data.get("lesson_id"),
                "token": lesson_data.get("token")
            }
            logger.info(f"{self.user_number} 开始课程成功：{self.lesson_info}")
            return True
        except Exception as e:
            logger.error(f"{self.user_number} 开始课程失败：{str(e)}")
            return False

    def _start_websocket_thread(self, is_reconnect=False):
        """
        启动WebSocket连接线程（核心优化：防重复连接、重连会话重置、预校验）
        :param is_reconnect: 是否为重连场景（True=重连，False=首次连接）
        """
        global last_connect_time

        # -------------------------- 关键优化1：防重复连接（避免同一用户多线程并发连接） --------------------------
        # 检查是否已有连接线程在运行（避免线程堆积）
        if hasattr(self, 'ws_thread') and self.ws_thread.is_alive():
            logger.warning(f"{self.user_number} 已有WebSocket连接线程在运行，跳过本次连接请求")
            return
        # 检查是否已有活跃连接（避免重复建立）
        if hasattr(self, 'ws') and self.ws.sock and self.ws.sock.connected:
            logger.warning(f"{self.user_number} 已存在活跃WebSocket连接，无需重复建立")
            return

        # -------------------------- 关键优化2：重连场景下重置会话（避免复用失效信息） --------------------------
        if is_reconnect:
            logger.info(f"{self.user_number} 进入重连流程，开始重置会话信息")
            # 1. 清空旧会话（lesson_id/token已可能失效）
            self.lesson_info = {}
            self.current_lesson_params = None
            # 2. 重新获取新课程信息（确保用新lesson_id/token）
            if not self.start_lesson_with_random_params():
                logger.error(f"{self.user_number} 重连时获取新课程失败，需重新初始化账号")
                # 重新执行登录→获取账号→选课程流程
                self.execute_post_login_steps()
                # 若重新初始化仍失败，终止本次连接
                if not self.lesson_info.get("lesson_id"):
                    logger.error(f"{self.user_number} 账号重新初始化失败，放弃本次重连")
                    return
            logger.info(f"{self.user_number} 重连会话重置完成，新lesson_id: {self.lesson_info.get('lesson_id')}")

        # -------------------------- 关键优化3：连接速度控制（仅首次连接生效，重连跳过） --------------------------
        if not is_reconnect:
            with connect_lock:
                current_time = time.time()
                elapsed = current_time - last_connect_time
                if elapsed < config.ws_connect_interval:
                    wait_time = config.ws_connect_interval - elapsed
                    logger.info(
                        f"{self.user_number} 控制连接速度，等待 {wait_time:.2f} 秒（上一个连接在 {elapsed:.2f} 秒前建立）"
                    )
                    time.sleep(wait_time)
                last_connect_time = time.time()

        logger.info(
            f"{self.user_number} 开始建立WebSocket连接（重连: {is_reconnect}，第 {self.ws_retry_count + 1} 次尝试）")
        try:
            # -------------------------- 关键优化4：会话信息预校验（避免无效参数） --------------------------
            required_params = {
                "english_name": self.user_info.get('english_name'),
                "lesson_id": self.lesson_info.get('lesson_id'),
                "token": self.lesson_info.get('token'),
                "phone_number": self.account_data.get('phone_number'),
                "member_id": self.user_info.get('member_id')
            }
            # 校验参数：排除None/空字符串
            invalid_params = {}
            for k, v in required_params.items():
                if v is None or str(v).strip() == "":
                    invalid_params[k] = v
            if invalid_params:
                logger.error(f"{self.user_number} WebSocket连接失败：关键参数无效/缺失 {invalid_params}")
                ws_stats.increment_failed()
                return

            # -------------------------- 原有逻辑：参数编码与URL构建 --------------------------
            # 编码所有参数（解决特殊字符问题）
            encoded_params = {
                "english_name": quote(str(required_params["english_name"]), safe=''),
                "lesson_id": quote(str(required_params["lesson_id"]), safe=''),
                "token": quote(str(required_params["token"]), safe=''),
                "phone_number": quote(str(required_params["phone_number"]), safe=''),
                "member_id": quote(str(required_params["member_id"]), safe=''),
                "material_id": quote(str(self.current_lesson_params['material_id']), safe='')
            }

            # 合并基础参数与动态参数
            base_params = WS_PARAMS.copy()
            base_params.update({
                "student_name": encoded_params['english_name'],
                "material": encoded_params['material_id'],
                "character_id": quote(self.current_lesson_params['character_id'], safe=''),
                "lesson_id": encoded_params['lesson_id'],
                "token": encoded_params['token'],
                "mobile": encoded_params['phone_number'],
                "member_id": encoded_params['member_id']
            })

            # 构建最终WebSocket URL
            query_params = "&".join([f"{k}={v}" for k, v in base_params.items()])
            websocket_url = f"{WS_BASE_URL}/?{query_params}"
            logger.info(f"{self.user_number} WebSocket URL：{websocket_url[:200]}...（完整长度: {len(websocket_url)}）")
            # logger.info(f"{self.user_number} WebSocket URL：{websocket_url}...（完整长度: {len(websocket_url)}）")

            # -------------------------- 原有逻辑：创建WebSocket连接与启动线程 --------------------------
            self.ws = WebSocketApp(
                websocket_url,
                on_open=self.on_ws_open,
                on_message=self.on_ws_message,
                on_error=self.on_ws_error,
                on_close=self.on_ws_close
            )

            # 启动连接线程（守护线程，避免主进程退出残留）
            self.ws_thread = threading.Thread(
                target=lambda: self.ws.run_forever(sslopt={"cert_reqs": ssl.CERT_NONE}),  # 忽略SSL证书校验（测试环境）
                daemon=True
            )
            self.ws_thread.start()
            self.ws_retry_count += 1
            logger.info(
                f"{self.user_number} WebSocket第 {self.ws_retry_count} 次连接线程已启动（线程ID: {self.ws_thread.ident}）")

        except Exception as e:
            logger.error(f"{self.user_number} WebSocket连接线程启动失败：{str(e)}", exc_info=True)
            ws_stats.increment_failed()

    def on_ws_open(self, ws):
        """WebSocket连接成功回调"""
        self.ws_connected = True
        active, total = ws_stats.increment()
        logger.info(
            f"{self.user_number} WebSocket连接成功，开始发送消息 | "
            f"当前活跃: {active} | 累计连接: {total}"
        )
        self._start_heartbeat()

    def on_ws_message(self, ws, message):
        """仅打印包含respId的消息，其他内容不输出"""
        # 二进制消息：完全不打印
        if isinstance(message, bytes):
            return

        # 文本消息：仅处理含"respId"的内容
        text_msg = str(message).strip()

        # 只打印包含"respId"的消息
        if "respId" in text_msg:
            logger.info(
                f"\n{self.user_number} 【收到含respId的消息】{text_msg}"
            )

        # 核心业务逻辑保留（不影响功能，仅不打印无关日志）
        if text_msg == "ping":
            ws.send("pong")
        elif any(keyword in text_msg.lower() for keyword in ["goodbye", "bye"]):
            self.lesson_completed = True
            ws.close(status=1000, reason="收到结束消息")
        elif "teacher_stopped" in text_msg:
            ws.close()
            self.ws_connected = False
            self._reset_lesson_state()
            self.execute_post_login_steps()
            self._start_websocket_thread()

    def _reset_lesson_state(self):
        """重置课程状态，包括新增的结束标记"""
        self.lesson_info = {}
        self.current_lesson_params = None
        self.ws_retry_count = 0
        self.state = ConnectionState.READY
        self.lesson_completed = False  # 重置结束标记

    def on_ws_error(self, ws, error):
        """WebSocket错误回调（详细记录错误原因）"""
        self.ws_connected = False
        # 区分不同错误类型（超时、连接被拒绝、SSL错误等）
        error_type = type(error).__name__
        error_msg = str(error)

        # 常见错误分类
        if "timed out" in error_msg.lower():
            error_category = "连接超时"
        elif "connection refused" in error_msg.lower():
            error_category = "连接被拒绝（服务器未监听端口）"
        elif "ssl" in error_type.lower() or "tls" in error_type.lower():
            error_category = "SSL/TLS握手失败"
        elif "reset by peer" in error_msg.lower():
            error_category = "连接被对方重置（可能服务器崩溃）"
        else:
            error_category = "未知错误"

        logger.error(
            f"{self.user_number} WebSocket错误（{error_category}）：\n"
            f"  类型: {error_type}\n"
            f"  消息: {error_msg}"
        )
        ws_stats.increment_failed()
        # 错误导致的断开计入网络问题
        ws_stats.update_close_reason(1006)  # 1006表示异常断开

    def on_ws_close(self, ws, close_status_code, close_msg):
        """
        WebSocket关闭回调（核心优化：断开原因统计、重连次数限制、正常结束不重连）
        :param ws: WebSocketApp实例
        :param close_status_code: 关闭状态码（None=异常断开）
        :param close_msg: 关闭消息（二进制格式，需转字符串）
        """
        # 标记连接状态为断开
        self.ws_connected = False
        # 减少活跃连接统计
        active = ws_stats.decrement()

        # -------------------------- 关键优化1：解析关闭原因与状态码 --------------------------
        # 处理close_msg（二进制转字符串，避免日志乱码）
        close_msg_str = close_msg.decode('utf-8', errors='replace') if isinstance(close_msg, bytes) else str(close_msg)
        # 解析状态码（1006=异常断开，1000=正常关闭）
        if close_status_code is None:
            status_code = 1006
            reason = "未收到关闭帧（可能是网络中断、服务器强制关闭或连接超时）"
        else:
            status_code = close_status_code
            reason = get_close_reason(status_code)  # 调用之前定义的状态码解析函数

        # -------------------------- 关键优化2：更新断开原因统计（便于问题定位） --------------------------
        ws_stats.update_close_reason(status_code)

        # -------------------------- 原有逻辑：打印关闭日志（含详细上下文） --------------------------
        logger.info(
            f"{self.user_number} WebSocket关闭 | "
            f"状态码: {status_code} | 原因: {reason} | "
            f"关闭消息: {close_msg_str[:100]} | 当前活跃连接: {active} | "
            f"累计重连次数: {self.ws_retry_count}"
        )

        # -------------------------- 关键优化3：重连逻辑（限制次数+正常结束不重连） --------------------------
        # 仅在“处理中状态”且“未达最大重连次数”时尝试重连
        if self.state != ConnectionState.PROCESSING:
            logger.info(f"{self.user_number} 当前状态非处理中（{self.state.name}），不触发重连")
            return
        if self.ws_retry_count >= 5:  # 最大重连次数=5（可在config.py中配置）
            logger.error(f"{self.user_number} 重连次数已达上限（{self.ws_retry_count}/5），停止重连并重新初始化账号")
            # 重置状态+重新执行登录→选课程流程
            self._reset_lesson_state()
            self.execute_post_login_steps()
            return
        # 课程正常结束（收到goodbye/bye），不重连
        if self.lesson_completed:
            logger.info(f"{self.user_number} 课程正常结束（已收到结束消息），不触发重连，准备下一轮流程")
            self._reset_lesson_state()
            self.execute_post_login_steps()
            return

        # -------------------------- 原有逻辑：指数退避重连（避免频繁重试压垮服务器） --------------------------
        # 记录重连统计
        ws_stats.increment_reconnect()
        # 指数退避等待（2^重连次数，最大30秒）
        backoff_time = min(30, 2 ** self.ws_retry_count)
        logger.warning(
            f"{self.user_number} 准备重连（第 {self.ws_retry_count + 1}/5 次） | "
            f"退避等待: {backoff_time:.2f} 秒 | 上次断开原因: {reason[:50]}"
        )

        # 等待后触发重连（传入is_reconnect=True，触发会话重置）
        try:
            time.sleep(backoff_time)
            self._start_websocket_thread(is_reconnect=True)
        except Exception as e:
            logger.error(f"{self.user_number} 重连触发失败：{str(e)}", exc_info=True)

    def _start_heartbeat(self):
        """发送标准ping控制帧（WebSocket协议规定的心跳）"""

        def heartbeat_loop():
            while True:
                if hasattr(self, 'ws') and self.ws.sock and self.ws.sock.connected:
                    self.ws.send("", opcode=websocket.ABNF.OPCODE_PING)
                    logger.debug(f"{self.user_number} 发送标准ping心跳")
                    time.sleep(WEBSOCKET_HEARTBEAT_INTERVAL)
                else:
                    break

        self.heartbeat_thread = threading.Thread(target=heartbeat_loop, daemon=True)
        self.heartbeat_thread.start()

    @task(5)
    def send_chat_message(self):
        """发送聊天消息任务"""
        if (self.state == ConnectionState.PROCESSING
                and self.ws_connected
                and hasattr(self, 'ws')
                and self.ws.sock
                and self.ws.sock.connected):
            message = random.choice(TEST_MESSAGES)
            self.ws.send(message)
            logger.info(f"{self.user_number} 发送消息：{message}")

    @task(1)
    def check_connection(self):
        """检查WebSocket连接状态，必要时重连"""
        if (self.state == ConnectionState.PROCESSING
                and hasattr(self, 'ws')
                and not (self.ws.sock and self.ws.sock.connected)):
            logger.warning(f"{self.user_number} WebSocket连接断开，尝试重新连接")
            self._start_websocket_thread(is_reconnect=True)


user_classes = [UserBehavior]


@events.init.add_listener
def _(environment, **kwargs):
    """初始化事件监听器，打印测试配置信息"""
    if not isinstance(environment.runner, WorkerRunner):
        login_interval = environment.parsed_options.login_interval
        account_multiplier = environment.parsed_options.account_multiplier
        ws_connect_interval = environment.parsed_options.ws_connect_interval
        stats_interval = environment.parsed_options.stats_interval
        total_users = environment.runner.target_user_count if hasattr(environment.runner, 'target_user_count') else 10
        target_accounts = max(1, int(total_users * account_multiplier))
        logger.info(
            f"测试配置：用户数={total_users}，目标账号数={target_accounts}，"
            f"登录间隔={login_interval}秒，连接间隔={ws_connect_interval}秒，"
            f"统计打印间隔={stats_interval}秒"
        )
