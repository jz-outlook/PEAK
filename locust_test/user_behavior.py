"""
用户行为逻辑模块
负责用户的生命周期管理、课程选择和测试任务执行
"""
import time
import random
import logging
import threading
import uuid
from typing import Dict, Optional, Any
from locust import HttpUser, task, between
from locust.runners import WorkerRunner

from config import (
    BASE_API_URL, START_LESSON_ENDPOINT, LESSON_PARAMS_LIST, TEST_MESSAGES,
    DEFAULT_LOGIN_INTERVAL, DEFAULT_ACCOUNT_MULTIPLIER, DEFAULT_WS_CONNECT_INTERVAL,
    DEFAULT_STATS_INTERVAL, USER_CHECK_INTERVAL, DEFAULT_MESSAGE_SEND_INTERVAL_MIN,
    DEFAULT_MESSAGE_SEND_INTERVAL_MAX, DEFAULT_LESSON_RESTART_DELAY
)
from stats_manager import ws_stats, ConnectionState
from account_manager import account_manager
from websocket_manager import WebSocketManager
from batch_manager import batch_manager

logger = logging.getLogger(__name__)


class UserBehavior(HttpUser):
    """用户行为测试类"""
    
    # 默认等待时间，会在初始化时动态更新
    def wait_time(self):
        if hasattr(self, '_wait_time_func'):
            return self._wait_time_func(self)
        else:
            return between(DEFAULT_MESSAGE_SEND_INTERVAL_MIN, DEFAULT_MESSAGE_SEND_INTERVAL_MAX)(self)
    host = BASE_API_URL

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        
        # 用户标识和状态
        self.user_number = self._get_user_number()
        self.global_user_id = str(uuid.uuid4())
        self.state = ConnectionState.INIT
        
        # 用户数据
        self.account_data: Optional[Dict[str, str]] = None
        self.user_info: Dict[str, Any] = {}
        self.lesson_info: Dict[str, Any] = {}
        self.current_lesson_params: Optional[Dict[str, str]] = None
        
        # 重连控制
        self.last_reconnect_time = 0
        self.reconnect_interval = 5.0  # 重连间隔5秒
        
        # 配置参数
        self._load_command_line_options()
        
        # 初始化WebSocket管理器
        self.ws_manager = WebSocketManager(self.user_number)
        self._setup_websocket_callbacks()
        
        # 启动统计和检查线程（仅第一个用户）
        if self.user_number == "user_1":
            self._start_stats_printer()
            self._start_user_check_thread()
            
        # 更新全局配置
        self._update_global_config()
        
        logger.info(f"{self.user_number} 初始化完成")

    def _get_user_number(self) -> str:
        """获取用户编号"""
        global user_number_counter, user_number_lock
        with user_number_lock:
            user_number_counter += 1
            return f"user_{user_number_counter}"

    def _load_command_line_options(self):
        """加载命令行参数"""
        self.login_interval = self.environment.parsed_options.login_interval
        self.account_multiplier = self.environment.parsed_options.account_multiplier
        self.stats_interval = self.environment.parsed_options.stats_interval
        self.message_send_interval_min = self.environment.parsed_options.message_send_interval_min
        self.message_send_interval_max = self.environment.parsed_options.message_send_interval_max
        self.target_accounts = max(1, int(self.environment.runner.target_user_count * self.account_multiplier))
        
        # 批次控制参数
        self.sync_batch_size = self.environment.parsed_options.sync_batch_size
        self.websocket_batch_size = self.environment.parsed_options.websocket_batch_size
        self.batch_start_interval = self.environment.parsed_options.batch_start_interval
        
        # 动态更新等待时间（使用实例属性方式）
        self._wait_time_func = between(self.message_send_interval_min, self.message_send_interval_max)
        
        # 更新批次管理器配置
        batch_manager.sync_batch_size = self.sync_batch_size
        batch_manager.websocket_batch_size = self.websocket_batch_size
        batch_manager.batch_start_interval = self.batch_start_interval

    def _setup_websocket_callbacks(self):
        """设置WebSocket回调函数"""
        self.ws_manager.set_callbacks(
            on_message=self._on_websocket_message,
            on_lesson_complete=self._on_lesson_complete,
            on_teacher_stop=self._on_teacher_stop
        )

    def _update_global_config(self):
        """更新全局配置"""
        global config, initial_users_spawned, initial_users_spawned_lock, spawned_user_count, spawned_user_lock
        
        # 更新目标用户数
        with initial_users_spawned_lock:
            config.target_users = self.environment.runner.target_user_count
            
        # 检查初始用户是否全部生成
        with spawned_user_lock:
            spawned_user_count += 1
            current_spawned = spawned_user_count
            
        with initial_users_spawned_lock:
            if current_spawned >= config.target_users and not initial_users_spawned:
                initial_users_spawned = True
                ws_stats.set_target(config.target_users)
                logger.info(f"初始 {config.target_users} 个用户已全部启动")

    def _start_stats_printer(self):
        """启动统计打印线程"""
        def print_stats_loop():
            while True:
                # 检查测试是否已经停止
                if hasattr(self.environment.runner, 'state') and self.environment.runner.state in ['stopping', 'stopped']:
                    logger.info("测试已停止，统计打印线程退出")
                    break
                    
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

    def _start_user_check_thread(self):
        """启动用户数量检查线程"""
        def check_user_loop():
            while True:
                # 检查测试是否已经停止
                if hasattr(self.environment.runner, 'state') and self.environment.runner.state in ['stopping', 'stopped']:
                    logger.info("测试已停止，用户数量检查线程退出")
                    break
                    
                with initial_users_spawned_lock:
                    if initial_users_spawned:
                        self._check_and_spawn_users()
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
                    logger.info(f"用户数量检查：当前活跃 {current_active}/{config.target_users}，需要补充 {deficit} 个用户")
                    self._spawn_new_users(deficit)
                else:
                    logger.debug(f"用户数量检查：当前活跃 {current_active}/{config.target_users}，无需补充")
        except Exception as e:
            logger.error(f"用户数量检查失败：{str(e)}")

    def _spawn_new_users(self, count: int):
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
        self._increment_active_users()
        
        # 执行登录和账号准备
        if self._login_and_cache_account():
            self._wait_for_accounts_ready()
            
            # 等待同步批次就绪
            sync_batch_id = batch_manager.get_sync_batch_id(self.user_number)
            if not batch_manager.wait_for_sync_batch(self.user_number, sync_batch_id):
                logger.error(f"{self.user_number} 同步批次等待失败")
                return
            
            self._execute_post_login_steps()
            
            # 等待WebSocket连接批次就绪
            ws_batch_id = batch_manager.get_websocket_batch_id(self.user_number)
            if not batch_manager.wait_for_websocket_batch(self.user_number, ws_batch_id):
                logger.error(f"{self.user_number} WebSocket批次等待失败")
                return
            
            # 如果状态正常，启动WebSocket连接
            if self.state == ConnectionState.PROCESSING:
                self._start_websocket_connection()

    def on_stop(self):
        """用户停止时清理资源"""
        self._decrement_active_users()
        if hasattr(self, 'ws_manager'):
            self.ws_manager.close_connection()
        logger.info(f"{self.user_number} 实例停止，执行清理")

    def _increment_active_users(self):
        """增加活跃用户计数"""
        global active_users, active_users_lock
        with active_users_lock:
            active_users += 1
            logger.info(f"活跃用户数增加：{active_users}/{config.target_users}")

    def _decrement_active_users(self):
        """减少活跃用户计数"""
        global active_users, active_users_lock
        with active_users_lock:
            active_users -= 1
            logger.info(f"活跃用户数减少：{active_users}/{config.target_users}")

    def _login_and_cache_account(self) -> bool:
        """登录并缓存账号信息"""
        login_index = account_manager.get_login_index(self.target_accounts)
        if login_index is None:
            self.state = ConnectionState.READY
            return True

        self.state = ConnectionState.LOGGING_IN
        logger.info(f"{self.user_number} 负责第 {login_index + 1}/{self.target_accounts} 个账号登录")

        # 登录时间控制
        login_time = time.time() + login_index * self.login_interval
        sleep_time = login_time - time.time()
        if sleep_time > 0:
            logger.info(f"{self.user_number} 等待 {sleep_time:.1f} 秒后登录")
            time.sleep(sleep_time)

        account = account_manager.get_and_login_single_account(self.user_number, self.client)
        if account:
            # 修改：将账号保存到当前用户实例，而不是放入共享池
            self.my_account = account  # 保存当前用户的账号

            # 传递实际需要的用户数，而不是目标账号数
            required_users = self.environment.runner.target_user_count
            account_manager.add_account_to_pool(account, self.user_number, self.target_accounts, required_users)
            self.state = ConnectionState.READY
            return True
        else:
            logger.error(f"{self.user_number} 登录失败")
            return False

    def _wait_for_accounts_ready(self):
        """等待账号池就绪"""
        # 等待账号池达到用户数量即可，不需要等待所有目标账号
        required_accounts = self.environment.runner.target_user_count
        if not account_manager.wait_for_accounts_ready(self.user_number, required_accounts, self.login_interval):
            raise Exception("账号池数量未达标，终止测试")

    def _execute_post_login_steps(self):
        """执行登录后的核心流程"""
        self.state = ConnectionState.PROCESSING

        # 修改：不要从账号池获取账号，而是使用当前用户已经登录的账号
        # 如果当前用户没有账号，说明没有参与登录，需要从账号池获取
        if not hasattr(self, 'my_account') or not self.my_account:
            self.account_data = account_manager.get_valid_account(self.user_number, self.target_accounts)
            if not self.account_data:
                self.state = ConnectionState.DONE
                return
        else:
            # 使用当前用户已经登录的账号
            self.account_data = self.my_account

        # 验证账号有效性并获取用户信息
        if not self._get_user_profile():
            logger.warning(f"{self.user_number} 账号token无效，丢弃该账号")
            self.account_data = None
            self.state = ConnectionState.DONE
            return

        # 随机选择课程并开始
        if not self._start_lesson_with_random_params():
            self.state = ConnectionState.DONE
            return

        logger.info(f"{self.user_number} 前置步骤执行完成，进入测试阶段")

    def _get_user_profile(self) -> bool:
        """获取用户个人信息（同时验证token有效性）"""
        from config import USER_PROFILE_ENDPOINT
        
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
            logger.info(f"{self.user_number} 获取用户信息成功：{self.user_info}")
            return True
        except Exception as e:
            logger.error(f"{self.user_number} 获取用户信息失败（token可能过期）：{str(e)}")
            return False

    def _start_lesson_with_random_params(self) -> bool:
        """随机选择课程参数并开始课程"""
        raw_lesson_params = random.choice(LESSON_PARAMS_LIST)
        logger.info(f"{self.user_number} 随机选择课程参数: {raw_lesson_params['material_id']}")

        headers = {"Authorization": f"Bearer {self.account_data['access_token']}"}
        try:
            response = self.client.post(
                START_LESSON_ENDPOINT,
                json=raw_lesson_params,
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
            
            # 解析material_id为WebSocket所需的参数
            self.current_lesson_params = self._parse_material_id(raw_lesson_params['material_id'])
            
            logger.info(f"{self.user_number} 开始课程成功：{self.lesson_info}")
            return True
        except Exception as e:
            logger.error(f"{self.user_number} 开始课程失败：{str(e)}")
            return False

    def _parse_material_id(self, material_id: str) -> Dict[str, str]:
        """解析material_id为WebSocket所需的参数"""
        try:
            # material_id格式: "topic_talk-b2-1/materials/topic_talk-b2-1-lesson_1-c_121102.json?sid=49&bid=51&gid=148&mid=8600"
            # 需要解析出: topic, materials, sid, bid, gid, mid
            
            # 分割路径和查询参数
            if '?' in material_id:
                path_part, query_part = material_id.split('?', 1)
            else:
                path_part = material_id
                query_part = ""
            
            # 解析路径部分
            path_parts = path_part.split('/')
            topic = path_parts[0]  # "topic_talk-b2-1"
            materials = '/'.join(path_parts[1:])  # "materials/topic_talk-b2-1-lesson_1-c_121102.json"
            
            # 解析查询参数
            params = {}
            if query_part:
                for param in query_part.split('&'):
                    if '=' in param:
                        key, value = param.split('=', 1)
                        params[key] = value
            
            return {
                'topic': topic,
                'materials': materials,
                'sid': params.get('sid', ''),
                'bid': params.get('bid', ''),
                'gid': params.get('gid', ''),
                'mid': params.get('mid', '')
            }
            
        except Exception as e:
            logger.error(f"{self.user_number} 解析material_id失败：{str(e)}")
            return {
                'topic': 'topic_talk-b2-1',
                'materials': 'materials/default.json',
                'sid': '49',
                'bid': '51',
                'gid': '148',
                'mid': '8600'
            }

    def _start_websocket_connection(self):
        """启动WebSocket连接"""
        if (self.account_data and self.user_info and
                self.lesson_info and self.current_lesson_params):

            success = self.ws_manager.start_connection(
                self.account_data,
                self.user_info,
                self.lesson_info,
                self.current_lesson_params
            )
            if not success:
                logger.error(f"{self.user_number} WebSocket连接启动失败")
                # 不要立即设置为DONE状态，给重连机会
                if not hasattr(self, 'connection_fail_count'):
                    self.connection_fail_count = 0
                self.connection_fail_count += 1

                if self.connection_fail_count >= 3:  # 连续失败3次才设置为DONE
                    logger.error(
                        f"{self.user_number} WebSocket连接连续失败{self.connection_fail_count}次，设置为DONE状态")
                    self.state = ConnectionState.DONE
                else:
                    logger.warning(f"{self.user_number} WebSocket连接失败，将在下次检查时重试")

    def _on_websocket_message(self, message: str):
        """WebSocket消息回调"""
        # 可以在这里添加额外的消息处理逻辑
        pass

    def _on_lesson_complete(self):
        """课程完成回调"""
        logger.info(f"{self.user_number} 课程正常完成，准备下一节课")
        self._reset_lesson_state()
        # 课程完成后，等待一段时间再重新开始，避免过于频繁
        import threading
        def delayed_restart():
            time.sleep(DEFAULT_LESSON_RESTART_DELAY)  # 使用配置的延迟时间
            self._restart_lesson_with_existing_account()
        
        restart_thread = threading.Thread(target=delayed_restart, daemon=True)
        restart_thread.start()

    def _on_teacher_stop(self):
        """老师停止回调"""
        logger.info(f"{self.user_number} 老师停止，重新开始课程")
        self._reset_lesson_state()
        # 老师停止后，延迟一段时间再重新选择课程，避免频繁重连
        import threading
        def delayed_restart():
            # 随机延迟1-3秒，避免所有用户同时重连
            delay = random.uniform(1.0, 3.0)
            time.sleep(delay)
            self._restart_lesson_with_existing_account()
        
        restart_thread = threading.Thread(target=delayed_restart, daemon=True)
        restart_thread.start()

    def _restart_lesson_with_existing_account(self):
        """使用现有账号重新开始课程"""
        if not self.account_data:
            logger.error(f"{self.user_number} 没有可用账号，无法重新开始课程")
            return
            
        # 先关闭现有的WebSocket连接
        if self.ws_manager:
            logger.info(f"{self.user_number} 关闭现有WebSocket连接，准备重新开始课程")
            self.ws_manager.close_connection()
            
        # 检查用户信息是否已存在，避免重复获取
        if not self.user_info or not self.user_info.get('english_name'):
            logger.info(f"{self.user_number} 用户信息缺失，重新获取用户信息")
            if not self._get_user_profile():
                logger.warning(f"{self.user_number} 账号token已失效，需要重新获取账号")
                # 如果账号失效，则重新执行完整的登录流程
                self._execute_post_login_steps()
                return
        else:
            logger.info(f"{self.user_number} 使用现有用户信息：{self.user_info['english_name']}")
            
        # 使用现有账号重新选择课程
        if self._start_lesson_with_random_params():
            self.state = ConnectionState.PROCESSING
            self._start_websocket_connection()
            logger.info(f"{self.user_number} 使用现有账号重新开始课程成功")
        else:
            logger.error(f"{self.user_number} 重新开始课程失败")

    def _reset_lesson_state(self):
        """重置课程状态（保留账号和用户信息）"""
        self.lesson_info = {}
        self.current_lesson_params = None
        self.ws_manager.reset_state()
        self.state = ConnectionState.READY
        # 注意：不重置 account_data 和 user_info，因为用户还在使用这个账号

    @task(5)
    def send_chat_message(self):
        """发送聊天消息任务"""
        # 添加调试日志
        logger.debug(
            f"{self.user_number} 尝试发送消息 - 状态: {self.state.name}, 连接状态: {self.ws_manager.is_connected()}")

        if (self.state == ConnectionState.PROCESSING and
                self.ws_manager.is_connected()):
            message = random.choice(TEST_MESSAGES)
            logger.info(f"{self.user_number} 发送消息: {message}")
            self.ws_manager.send_message(message)
        else:
            # 添加详细的状态信息
            if self.state != ConnectionState.PROCESSING:
                logger.warning(f"{self.user_number} 状态不是PROCESSING，当前状态: {self.state.name}")
            if not self.ws_manager.is_connected():
                logger.warning(f"{self.user_number} WebSocket未连接")

    @task(1)
    def check_connection(self):
        """检查WebSocket连接状态"""
        # 添加调试日志
        logger.debug(
            f"{self.user_number} 检查连接 - 状态: {self.state.name}, 连接状态: {self.ws_manager.is_connected()}")

        if (self.state == ConnectionState.PROCESSING and
                not self.ws_manager.is_connected()):

            # 检查重连间隔
            current_time = time.time()
            if current_time - self.last_reconnect_time < self.reconnect_interval:
                return  # 未到重连间隔，跳过本次检查

            # 检查重连次数限制
            if not hasattr(self, 'reconnect_attempts'):
                self.reconnect_attempts = 0

            # 修改：增加重连次数限制，但不要设置为DONE
            if self.reconnect_attempts >= 5:  # 增加到5次
                logger.warning(f"{self.user_number} 重连次数过多，延长重连间隔")
                self.reconnect_interval = 30.0  # 延长重连间隔到30秒
                self.reconnect_attempts = 0  # 重置计数，继续尝试

            # 如果课程信息丢失，尝试重新开始课程
            if not (self.account_data and self.user_info and
                    self.lesson_info and self.current_lesson_params):
                logger.warning(f"{self.user_number} 课程信息丢失，尝试重新开始课程")
                if self.account_data and self.user_info:
                    # 有账号和用户信息，重新开始课程
                    if self._start_lesson_with_random_params():
                        logger.info(f"{self.user_number} 重新开始课程成功")
                        self._start_websocket_connection()
                    else:
                        logger.error(f"{self.user_number} 重新开始课程失败")
                else:
                    logger.error(f"{self.user_number} 账号信息丢失，无法重连")
                return

            # 增加重连延迟，避免过于频繁
            time.sleep(min(self.reconnect_attempts * 2, 10))  # 延迟时间递增，最多10秒

            self.reconnect_attempts += 1
            logger.warning(f"{self.user_number} WebSocket连接断开，尝试重新连接 (第{self.reconnect_attempts}次)")
            self.last_reconnect_time = current_time
            self._start_websocket_connection()


# 全局变量（用于向后兼容）
user_number_counter = 0
user_number_lock = threading.Lock()
active_users = 0
active_users_lock = threading.Lock()
spawned_user_count = 0
spawned_user_lock = threading.Lock()
initial_users_spawned = False
initial_users_spawned_lock = threading.Lock()

# 全局配置实例
class Config:
    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                cls._instance = super().__new__(cls)
                cls._instance.target_users = 10
        return cls._instance

config = Config()
