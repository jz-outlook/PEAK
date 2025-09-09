import websocket
import logging
import threading
import time
import ssl
from typing import Dict, Optional, Callable
from websocket import WebSocketApp
from stats_manager import ws_stats, get_close_reason
from config import WS_BASE_URL, WS_PARAMS
import json

logger = logging.getLogger(__name__)


class WebSocketManager:
    """WebSocket连接管理器"""

    def __init__(self, user_number: str):
        self.user_number = user_number
        self.ws = None
        self.ws_thread = None
        self.ws_connected = False
        self.ws_retry_count = 0
        self.heartbeat_thread = None
        self.heartbeat_stop_event = threading.Event()
        self.last_connection_time = 0
        self.connection_interval = 5.0  # 连接间隔控制

        # 消息缓冲机制
        self.message_buffer = []  # 消息缓冲区
        self.buffer_size = 15  # 缓冲区大小（增加以容纳更多消息）
        self.buffer_timeout = 3.0  # 缓冲区超时时间（秒）
        self.last_message_time = 0

        # 新增：连接状态监控
        self.last_pong_time = 0  # 最后收到pong的时间
        self.connection_quality = "good"  # 连接质量：good, poor, bad
        self.heartbeat_failures = 0  # 心跳失败次数

        # 新增：课程信息存储（用于错误日志）
        self.current_lesson_id = None  # 当前课程ID
        self.current_lesson_token = None  # 当前课程token
        self.current_phone_number = None  # 当前用户手机号

        # 回调函数
        self.on_message_callback = None
        self.on_lesson_complete_callback = None
        self.on_teacher_stop_callback = None

    def set_callbacks(self, on_message: Callable = None, on_lesson_complete: Callable = None,
                      on_teacher_stop: Callable = None):
        """设置回调函数"""
        self.on_message_callback = on_message
        self.on_lesson_complete_callback = on_lesson_complete
        self.on_teacher_stop_callback = on_teacher_stop

    def start_connection(self,
                         account_data: Dict[str, str],
                         user_info: Dict[str, str],
                         lesson_info: Dict[str, str],
                         lesson_params: Dict[str, str],
                         is_reconnect: bool = False) -> bool:
        """
        启动WebSocket连接
        :param account_data: 账号数据
        :param user_info: 用户信息
        :param lesson_info: 课程信息
        :param lesson_params: 课程参数
        :param is_reconnect: 是否为重连
        :return: 是否成功启动连接
        """
        # 防重复连接检查
        if self._check_existing_connection():
            return False

        # 连接速度控制（仅首次连接）
        if not is_reconnect:
            self._control_connection_speed()

        # 参数预校验
        if not self._validate_connection_params(account_data, user_info, lesson_info, lesson_params):
            return False

        # 存储当前课程信息（用于错误日志）
        self.current_lesson_id = lesson_info.get('lesson_id')
        self.current_lesson_token = lesson_info.get('token')
        self.current_phone_number = account_data.get('username')  # 存储用户手机号

        # 构建WebSocket URL
        websocket_url = self._build_websocket_url(account_data, user_info, lesson_info, lesson_params)
        if not websocket_url:
            return False

        # 创建并启动WebSocket连接
        return self._create_and_start_websocket(websocket_url, is_reconnect)

    def _check_existing_connection(self) -> bool:
        """检查是否已有连接在运行"""
        # 最简单的检查：只检查是否有活跃的连接线程
        if hasattr(self, 'ws_thread') and self.ws_thread and self.ws_thread.is_alive():
            logger.warning(f"{self.user_number} 已有WebSocket连接线程在运行，跳过本次连接请求")
            return True

        return False

    def _force_cleanup(self) -> None:
        """强制清理僵尸连接和线程"""
        try:
            # 先重置连接状态，防止回调函数执行
            self.ws_connected = False

            # 停止心跳线程
            if self.heartbeat_thread and self.heartbeat_thread.is_alive():
                self.heartbeat_stop_event.set()
                try:
                    if self.heartbeat_thread != threading.current_thread():
                        self.heartbeat_thread.join(timeout=1.0)
                except RuntimeError as e:
                    if "cannot join current thread" in str(e):
                        logger.debug(f"{self.user_number} 跳过心跳线程join操作（当前线程）")
                    else:
                        logger.error(f"{self.user_number} 心跳线程join操作失败：{str(e)}")

            # 关闭WebSocket连接
            if hasattr(self, 'ws') and self.ws:
                old_ws = self.ws
                self.ws = None  # 先设置为None，防止回调函数访问
                try:
                    old_ws.close()
                except Exception as e:
                    logger.debug(f"{self.user_number} 关闭WebSocket时发生异常: {str(e)}")

            # 清理线程引用
            if hasattr(self, 'ws_thread'):
                self.ws_thread = None

            logger.info(f"{self.user_number} 强制清理完成，准备重新连接")

        except Exception as e:
            logger.error(f"{self.user_number} 强制清理过程中发生错误：{str(e)}")

    def _control_connection_speed(self) -> None:
        """控制连接速度，避免过快建立连接"""
        current_time = time.time()
        if self.last_connection_time > 0:
            elapsed = current_time - self.last_connection_time
            if elapsed < self.connection_interval:
                wait_time = self.connection_interval - elapsed
                logger.info(
                    f"{self.user_number} 控制连接速度，等待 {wait_time:.2f} 秒（上一个连接在 {elapsed:.2f} 秒前建立）")
                time.sleep(wait_time)

        self.last_connection_time = time.time()

    def _validate_connection_params(self, account_data: Dict[str, str], user_info: Dict[str, str],
                                    lesson_info: Dict[str, str], lesson_params: Dict[str, str]) -> bool:
        """验证连接参数"""
        required_params = {
            'account_data': ['username', 'password'],
            'user_info': ['english_name', 'member_id'],
            'lesson_info': ['lesson_id', 'token'],
            'lesson_params': ['topic', 'materials']
        }

        for param_name, required_keys in required_params.items():
            param_dict = locals()[param_name]
            if not param_dict:
                logger.error(f"{self.user_number} WebSocket连接失败：{param_name}为空")
                return False

            for key in required_keys:
                if key not in param_dict or not param_dict[key]:
                    logger.error(f"{self.user_number} WebSocket连接失败：{param_name}中缺少{key}")
                    return False

        return True

    def _build_websocket_url(self, account_data: Dict[str, str], user_info: Dict[str, str],
                             lesson_info: Dict[str, str], lesson_params: Dict[str, str]) -> Optional[str]:
        """构建WebSocket连接URL"""
        try:
            # 使用账号数据中的用户名（手机号）
            phone_number = account_data['username']
            password = account_data['password']

            # 编码所有参数（解决特殊字符问题）
            from urllib.parse import quote
            encoded_params = {
                "english_name": quote(str(user_info['english_name']), safe=''),
                "lesson_id": quote(str(lesson_info['lesson_id']), safe=''),
                "token": quote(str(lesson_info['token']), safe=''),
                "phone_number": quote(str(phone_number), safe=''),
                "member_id": quote(str(user_info['member_id']), safe=''),
                "material_id": quote(str(lesson_params['topic'] + '/' + lesson_params['materials']), safe='')
            }

            # 合并基础参数与动态参数
            base_params = WS_PARAMS.copy()
            base_params.update({
                "student_name": encoded_params['english_name'],
                "material": encoded_params['material_id'],
                "character_id": quote(lesson_params.get('character_id', 'lily_white'), safe=''),
                "lesson_id": encoded_params['lesson_id'],
                "token": encoded_params['token'],
                "mobile": encoded_params['phone_number'],
                "member_id": encoded_params['member_id']
            })

            # 构建最终WebSocket URL
            query_params = "&".join([f"{k}={v}" for k, v in base_params.items()])
            websocket_url = f"{WS_BASE_URL}/?{query_params}"

            logger.info(f"{self.user_number} WebSocket URL构建成功（长度: {len(websocket_url)}）")
            logger.debug(f"{self.user_number} WebSocket URL: {websocket_url[:200]}...")
            return websocket_url

        except Exception as e:
            logger.error(f"{self.user_number} WebSocket URL构建失败：{str(e)}")
            return None

    # 在 websocket_manager.py 的 _create_and_start_websocket 方法中修改
    def _create_and_start_websocket(self, websocket_url: str, is_reconnect: bool) -> bool:
        """创建并启动WebSocket连接"""
        try:
            self.ws = WebSocketApp(
                websocket_url,
                on_open=self._on_open,
                on_message=self._on_message,
                on_error=self._on_error,
                on_close=self._on_close
            )

            # 启动连接线程 - 根据URL协议决定是否使用SSL
            if websocket_url.startswith('wss://'):
                # SSL连接，禁用证书验证，启用内置心跳
                self.ws_thread = threading.Thread(
                    target=lambda: self.ws.run_forever(
                        sslopt={
                            "cert_reqs": ssl.CERT_NONE,
                            "check_hostname": False,
                            "ssl_version": ssl.PROTOCOL_TLS,
                            "ciphers": "DEFAULT@SECLEVEL=0"
                        },
                        suppress_origin=True,
                        ping_interval=20,  # 改为20秒发送ping（更频繁）
                        ping_timeout=15,  # 改为15秒超时（更宽松）
                        ping_payload="heartbeat"  # 添加ping载荷
                    ),
                    daemon=True
                )
            else:
                # 非SSL连接，启用内置心跳
                self.ws_thread = threading.Thread(
                    target=lambda: self.ws.run_forever(
                        suppress_origin=True,
                        ping_interval=20,  # 改为20秒发送ping（更频繁）
                        ping_timeout=15,  # 改为15秒超时（更宽松）
                        ping_payload="heartbeat"  # 添加ping载荷
                    ),
                    daemon=True
                )
            self.ws_thread.start()
            self.ws_retry_count += 1

            logger.info(f"{self.user_number} WebSocket第 {self.ws_retry_count} 次连接线程已启动")
            return True

        except Exception as e:
            logger.error(f"{self.user_number} WebSocket连接线程启动失败：{str(e)}")
            ws_stats.increment_failed()
            return False

    def _on_open(self, ws):
        """WebSocket连接成功回调"""
        self.ws_connected = True
        active, total = ws_stats.increment()
        logger.info(f"{self.user_number} WebSocket连接成功，当前活跃: {active} | 累计连接: {total}")

        # 强制清空消息缓冲区，确保没有残留消息
        self.message_buffer.clear()
        self.last_message_time = 0

        # 添加延迟，确保缓冲区完全清空
        import time
        time.sleep(0.1)

        logger.debug(f"{self.user_number} 连接建立，消息缓冲区已清空")

        self._start_heartbeat()

    def _on_message(self, ws, message):
        """WebSocket消息接收回调"""
        # 防止在清理过程中处理消息
        if self.ws is None or ws is None:
            return

        try:
            # 处理二进制消息（如语音数据）
            if isinstance(message, bytes):
                logger.debug(f"{self.user_number} 【收到二进制消息】长度: {len(message)} 字节")
                return

            # 确保消息是字符串类型
            if not isinstance(message, str):
                message = str(message)

            # 添加到消息缓冲区
            self._add_to_buffer(message)

            # 检查课程结束消息（从缓冲区检测）
            if self._check_lesson_complete_from_buffer():
                logger.info(f"{self.user_number} 从消息缓冲区检测到课程结束")
                if self.on_lesson_complete_callback:
                    self.on_lesson_complete_callback()
                return

            # 检查老师停止消息
            if self._is_teacher_stop_message(message):
                logger.info(f"{self.user_number} 收到老师停止消息: {message[:100]}...")
                if self.on_teacher_stop_callback:
                    self.on_teacher_stop_callback()
                return

            # 调用消息回调
            if self.on_message_callback:
                self.on_message_callback(message)

        except Exception as e:
            logger.error(f"{self.user_number} 处理WebSocket消息时发生错误：{str(e)}")
            # 记录消息类型和长度以便调试
            logger.debug(
                f"{self.user_number} 消息类型: {type(message)}, 长度: {len(message) if hasattr(message, '__len__') else 'N/A'}")

    def _on_error(self, ws, error):
        """WebSocket错误回调"""
        # 防止在清理过程中处理错误
        if self.ws is None:
            return

        error_str = str(error)
        logger.error(f"{self.user_number} WebSocket错误：{error_str}")

        # 根据错误类型调整连接质量
        if "ping/pong timed out" in error_str:
            self.connection_quality = "poor"
            self.heartbeat_failures += 1
        elif "Connection to remote host was lost" in error_str:
            self.connection_quality = "bad"
        elif "'NoneType' object has no attribute 'sock'" in error_str:
            # 处理WebSocket对象为None的情况
            logger.error(f"{self.user_number} WebSocket对象为None，强制清理连接")
            self._force_cleanup()

    def _on_close(self, ws, close_status_code, close_msg):
        """WebSocket关闭回调"""
        # 防止在清理过程中处理关闭事件
        if self.ws is None or ws is None:
            logger.warning(f"{self.user_number} WebSocket关闭回调被调用，但WebSocket对象已为空，忽略此关闭事件")
            return

        # 更新连接状态
        self.ws_connected = False

        # 停止心跳
        self._stop_heartbeat()

        # 更新统计信息
        active = ws_stats.decrement()
        ws_stats.update_close_reason(close_status_code)

        # 记录关闭原因
        reason = get_close_reason(close_status_code)
        phone_info = f"['phone_number': '{self.current_phone_number}']" if self.current_phone_number else "['phone_number': 'None']"
        lesson_info = f"[lesson_id:{self.current_lesson_id}]" if self.current_lesson_id else "[lesson_id:None]"
        logger.info(
            f"{self.user_number} {phone_info}{lesson_info}WebSocket关闭 | 状态码: {close_status_code} | 原因: {reason} | 关闭消息: {close_msg} | 当前活跃连接: {active} | 累计重连次数: {self.ws_retry_count}")

    def _is_lesson_complete_message(self, message: str) -> bool:
        """检查是否是课程结束消息（单消息检测，作为备用）"""
        message_lower = message.lower()

        # 检查单个关键词
        complete_indicators = [
            "goodbye", "bye", "see you next time"
        ]

        for indicator in complete_indicators:
            if indicator in message_lower:
                logger.info(f"{self.user_number} 在单消息中检测到课程结束关键词: '{indicator}'")
                return True

        return False

    def _is_teacher_stop_message(self, message: str) -> bool:
        """检查是否是老师停止消息"""
        # 更严格的老师停止消息检测，避免误判
        message_lower = message.lower()

        # 排除一些常见的系统消息
        if any(system_msg in message_lower for system_msg in ["[end]", "websocket", "connection", "ping", "pong"]):
            return False

        # 检查明确的老师停止消息模式
        teacher_stop_patterns = [
            "goodbye",
            "See you",
            "See you next time!"
        ]

        return any(pattern in message_lower for pattern in teacher_stop_patterns)

    def _start_heartbeat(self):
        """启动心跳线程"""
        if self.heartbeat_thread and self.heartbeat_thread.is_alive():
            return

        self.heartbeat_stop_event.clear()
        self.heartbeat_thread = threading.Thread(target=self._heartbeat_loop, daemon=True)
        self.heartbeat_thread.start()

    def _heartbeat_loop(self):
        """改进的心跳循环"""
        heartbeat_interval = 20  # 与内置心跳保持一致
        consecutive_failures = 0
        max_failures = 3

        while not self.heartbeat_stop_event.is_set() and self.ws_connected:
            try:
                if self.ws and self.ws.sock and self.ws.sock.connected:
                    # 检查连接是否真的活跃
                    if self._is_connection_healthy():
                        # 发送自定义心跳消息（可选）
                        self._send_custom_heartbeat()
                        consecutive_failures = 0  # 重置失败计数
                        logger.debug(f"{self.user_number} 心跳检查通过")
                    else:
                        consecutive_failures += 1
                        logger.warning(f"{self.user_number} 连接健康检查失败 ({consecutive_failures}/{max_failures})")

                        if consecutive_failures >= max_failures:
                            logger.error(f"{self.user_number} 连接健康检查连续失败，主动断开连接")
                            self._force_disconnect()
                            break
                else:
                    logger.warning(f"{self.user_number} 心跳检测到连接已断开")
                    break

                # 动态调整心跳间隔
                current_interval = heartbeat_interval
                if consecutive_failures > 0:
                    current_interval = heartbeat_interval // 2  # 失败时更频繁检查

                time.sleep(current_interval)

            except Exception as e:
                consecutive_failures += 1
                logger.error(f"{self.user_number} 心跳发送失败：{str(e)} (失败次数: {consecutive_failures})")

                if consecutive_failures >= max_failures:
                    logger.error(f"{self.user_number} 心跳连续失败{consecutive_failures}次，停止心跳")
                    break

                # 失败时等待更长时间再重试
                time.sleep(heartbeat_interval)

    def _is_connection_healthy(self) -> bool:
        """检查连接是否健康"""
        try:
            if not self.ws or not self.ws.sock:
                return False

            # 检查socket状态
            if not self.ws.sock.connected:
                return False

            # 可以添加更多健康检查逻辑
            # 比如检查最后接收消息的时间等

            return True
        except Exception as e:
            logger.debug(f"{self.user_number} 连接健康检查异常: {str(e)}")
            return False

    def _send_custom_heartbeat(self):
        """发送自定义心跳消息（可选）"""
        try:
            # 发送一个轻量级的心跳消息
            heartbeat_msg = {"type": "ping", "timestamp": time.time()}
            self.ws.send(json.dumps(heartbeat_msg))
            logger.debug(f"{self.user_number} 发送自定义心跳消息")
        except Exception as e:
            logger.debug(f"{self.user_number} 发送自定义心跳失败: {str(e)}")

    def _force_disconnect(self):
        """强制断开连接"""
        try:
            if self.ws and self.ws.sock and self.ws.sock.connected:
                self.ws.close()
                logger.info(f"{self.user_number} 主动断开连接")
        except Exception as e:
            logger.error(f"{self.user_number} 强制断开连接失败: {str(e)}")

    def _stop_heartbeat(self):
        """停止心跳线程"""
        if self.heartbeat_thread and self.heartbeat_thread.is_alive():
            self.heartbeat_stop_event.set()
            self.heartbeat_thread.join(timeout=1.0)

    def is_connected(self) -> bool:
        """检查连接状态"""
        return (hasattr(self, 'ws') and self.ws and
                hasattr(self.ws, 'sock') and self.ws.sock and
                self.ws.sock.connected)

    def close_connection(self):
        """关闭连接"""
        try:
            self.ws_connected = False
            self._stop_heartbeat()

            if hasattr(self, 'ws') and self.ws:
                self.ws.close()

            # 安全地等待WebSocket线程结束
            if hasattr(self, 'ws_thread') and self.ws_thread:
                try:
                    # 检查线程是否还活着，并且不是当前线程
                    if self.ws_thread.is_alive() and self.ws_thread != threading.current_thread():
                        self.ws_thread.join(timeout=2.0)
                        if self.ws_thread.is_alive():
                            logger.warning(f"{self.user_number} WebSocket线程在超时后仍在运行")
                except RuntimeError as e:
                    if "cannot join current thread" in str(e):
                        logger.debug(f"{self.user_number} 跳过线程join操作（当前线程）")
                    else:
                        logger.error(f"{self.user_number} 线程join操作失败：{str(e)}")
                except Exception as e:
                    logger.error(f"{self.user_number} 线程join操作异常：{str(e)}")

        except Exception as e:
            logger.error(f"{self.user_number} 关闭WebSocket连接时发生错误：{str(e)}")

    def send_message(self, message: str) -> bool:
        """发送消息"""
        try:
            if self.ws and self.ws_connected:
                self.ws.send(message)
                logger.debug(f"{self.user_number} 发送消息: {message}")
                return True
            else:
                logger.warning(f"{self.user_number} WebSocket未连接，无法发送消息")
                return False
        except Exception as e:
            logger.error(f"{self.user_number} 发送消息失败: {str(e)}")
            return False

    def reset_state(self):
        """重置状态"""
        # 如果当前有活跃连接，需要减少活跃连接计数
        if self.ws_connected:
            ws_stats.decrement()
            logger.info(f"{self.user_number} 重置状态时减少活跃连接计数")

        self.close_connection()
        self.ws = None
        self.ws_thread = None
        self.ws_connected = False
        self.ws_retry_count = 0
        self.heartbeat_thread = None
        self.heartbeat_stop_event = threading.Event()
        self.last_connection_time = 0

        # 清空消息缓冲区
        self.message_buffer.clear()
        self.last_message_time = 0

        # 清空课程信息
        self.current_lesson_id = None
        self.current_lesson_token = None
        self.current_phone_number = None

    def _add_to_buffer(self, message: str):
        """添加消息到缓冲区"""
        import time
        current_time = time.time()

        # 如果距离上次消息时间过长，清空缓冲区
        if current_time - self.last_message_time > self.buffer_timeout:
            if self.message_buffer:
                logger.debug(f"{self.user_number} 消息缓冲区超时，清空缓冲区（包含 {len(self.message_buffer)} 条消息）")
            self.message_buffer.clear()

        # 添加新消息
        self.message_buffer.append(message)
        self.last_message_time = current_time

        # 保持缓冲区大小
        if len(self.message_buffer) > self.buffer_size:
            removed_message = self.message_buffer.pop(0)
            logger.debug(f"{self.user_number} 缓冲区已满，移除最旧消息: {removed_message[:50]}...")

        logger.debug(f"{self.user_number} 消息缓冲区当前大小: {len(self.message_buffer)}/{self.buffer_size}")

    def _check_lesson_complete_from_buffer(self) -> bool:
        """从消息缓冲区检查课程结束消息"""
        if not self.message_buffer:
            return False

        # 将缓冲区中的消息组合成一个字符串
        combined_message = " ".join(self.message_buffer).lower()

        # 更严格的结束关键词检测
        complete_indicators = [
            "see you next time",  # 完整短语
            "goodbye"
        ]

        # 检查完整短语，而不是单个词
        for indicator in complete_indicators:
            if indicator in combined_message:
                logger.info(f"{self.user_number} 在消息缓冲区中检测到课程结束关键词: '{indicator}'")
                logger.debug(f"{self.user_number} 缓冲区内容: {combined_message[:200]}...")
                return True

        return False
