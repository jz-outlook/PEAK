"""
WebSocket连接统计信息管理模块
负责收集、统计和报告WebSocket连接的各种指标
"""
import logging
from threading import Lock
from typing import Dict, Any
from enum import Enum

logger = logging.getLogger(__name__)


class ConnectionState(Enum):
    """连接状态枚举"""
    INIT = 0
    LOGGING_IN = 1
    READY = 2
    PROCESSING = 3
    DONE = 4


class WebSocketStats:
    """增强版统计器，包含断开原因分类计数"""

    def __init__(self):
        self.active_connections = 0
        self.total_connections = 0
        self.target_connections = 0
        self.failed_connections = 0
        self.reconnect_count = 0

        # 断开原因分类统计
        self.normal_closures = 0  # 正常关闭（1000）
        self.protocol_errors = 0  # 协议错误（1002等）
        self.server_initiated = 0  # 服务器主动关闭（1011-1014等）
        self.network_errors = 0  # 网络问题（1006等）
        self.unknown_closures = 0  # 未知原因

        self.lock = Lock()

    def set_target(self, target: int) -> None:
        """设置目标连接数"""
        with self.lock:
            self.target_connections = target
            logger.info(f"目标连接数已设置为：{target}")

    def increment(self) -> tuple[int, int]:
        """增加活跃连接数"""
        with self.lock:
            self.active_connections += 1
            self.total_connections += 1
            return self.active_connections, self.total_connections

    def decrement(self) -> int:
        """减少活跃连接数"""
        with self.lock:
            if self.active_connections > 0:
                self.active_connections -= 1
            return self.active_connections

    def increment_failed(self) -> int:
        """增加失败连接数"""
        with self.lock:
            self.failed_connections += 1
            return self.failed_connections

    def increment_reconnect(self) -> int:
        """增加重连次数"""
        with self.lock:
            self.reconnect_count += 1
            return self.reconnect_count

    def update_close_reason(self, status_code: int) -> None:
        """根据状态码更新断开原因统计"""
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

    def get_stats(self) -> Dict[str, Any]:
        """获取统计信息"""
        with self.lock:
            return {
                "target": self.target_connections,
                "active": self.active_connections,
                "total": self.total_connections,
                "failed": self.failed_connections,
                "reconnects": self.reconnect_count,
                "closures": {
                    "normal": self.normal_closures,
                    "protocol_errors": self.protocol_errors,
                    "server_initiated": self.server_initiated,
                    "network_errors": self.network_errors,
                    "unknown": self.unknown_closures
                }
            }

    def get_connection_rate(self) -> float:
        """获取连接成功率"""
        with self.lock:
            if self.total_connections == 0:
                return 0.0
            return (self.total_connections - self.failed_connections) / self.total_connections * 100

    def get_reconnect_rate(self) -> float:
        """获取重连率"""
        with self.lock:
            if self.total_connections == 0:
                return 0.0
            return self.reconnect_count / self.total_connections * 100

    def reset(self) -> None:
        """重置所有统计信息"""
        with self.lock:
            self.active_connections = 0
            self.total_connections = 0
            self.target_connections = 0
            self.failed_connections = 0
            self.reconnect_count = 0
            self.normal_closures = 0
            self.protocol_errors = 0
            self.server_initiated = 0
            self.network_errors = 0
            self.unknown_closures = 0
            logger.info("统计信息已重置")


def get_close_reason(status_code: int) -> str:
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


# 全局统计实例
ws_stats = WebSocketStats()

