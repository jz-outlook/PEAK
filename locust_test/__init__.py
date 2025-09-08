"""
AI Talk WebSocket 压力测试工具包
"""

__version__ = "2.0.0"
__author__ = "AI Talk Team"
__description__ = "WebSocket压力测试工具，支持多用户并发测试和智能重连"

# 导出主要类和函数
from .config import config, TestConfig
from .stats_manager import ws_stats, WebSocketStats, ConnectionState
from .account_manager import account_manager, AccountManager
from .websocket_manager import WebSocketManager
from .user_behavior import UserBehavior
from .main import user_classes

__all__ = [
    'config',
    'TestConfig', 
    'ws_stats',
    'WebSocketStats',
    'ConnectionState',
    'account_manager',
    'AccountManager',
    'WebSocketManager',
    'UserBehavior',
    'user_classes'
]

