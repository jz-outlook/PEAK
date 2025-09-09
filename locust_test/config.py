"""
AI Talk WebSocket 压力测试配置管理模块
使用 Pydantic 进行配置验证和类型检查
"""
from typing import List, Dict, Any
from pydantic import BaseModel, Field, validator
import logging
import os


class APIConfig(BaseModel):
    """API 相关配置"""
    base_url: str = Field(default="https://api-test-ws.myaitalk.vip", description="基础API地址")
    test_account_api: str = Field(default="https://watchdog.myaitalk.vip/register_test_account", description="获取测试账号API")
    auth_token: str = Field(default="NjExveResQZUKqFXCurPed5kXeSHkZsW", description="认证令牌")
    
    # 接口端点
    login_endpoint: str = Field(default="/register/userLogin.php", description="登录接口")
    user_profile_endpoint: str = Field(default="/profile/getUserProfile.php", description="获取用户信息接口")
    start_lesson_endpoint: str = Field(default="/lesson/startLessonv2.php", description="获取课程信息接口")


class WebSocketConfig(BaseModel):
    """WebSocket 相关配置"""
    base_url: str = Field(default="wss://wss-test-ws.myaitalk.vip", description="WebSocket基础地址")
    heartbeat_interval: int = Field(default=10, ge=1, le=300, description="心跳间隔（秒）")
    max_reconnect_attempts: int = Field(default=5, ge=1, le=20, description="最大重连次数")
    
    # WebSocket 参数
    params: Dict[str, str] = Field(default={
        "tts": "elevenlabs",
        "sid": "49",
        "bid": "108", 
        "gid": "413",
        "mid": "16132",
        "alert": "1",
        "version": "2",
        "llm_model": "gpt-4",
        "age": "100",
        "protobuf": "11",
        "pv": "2",
        "game": "no",
        "audio": "wav",
        "ver": "3001000",
        "device": "Redmi-2311DRK48C",
        "bilingual": "en"
    }, description="WebSocket连接参数")


class ConnectionConfig(BaseModel):
    """连接控制配置"""
    max_login_attempts: int = Field(default=3, ge=1, le=10, description="最大登录尝试次数")
    max_account_attempts: int = Field(default=10, ge=1, le=50, description="最大获取账号尝试次数")
    user_check_interval: int = Field(default=10, ge=1, le=60, description="用户检查间隔（秒）")
    account_pool_timeout_multiplier: float = Field(default=3.0, ge=1.0, le=10.0, description="账号池超时倍数")
    
    # 新增：并发控制配置
    sync_batch_size: int = Field(default=5, ge=1, le=100, description="同步批次大小（每批同时启动的用户数同时启动登录）")
    websocket_batch_size: int = Field(default=5, ge=1, le=50, description="WebSocket连接批次大小（每批同时建立的连接数）")
    batch_start_interval: float = Field(default=2.0, ge=0.1, le=10.0, description="批次启动间隔（秒）")


class TimingConfig(BaseModel):
    """时间控制配置"""
    login_interval: float = Field(default=2.0, ge=0.1, le=60.0, description="登录间隔（秒/个）")
    account_multiplier: float = Field(default=1.5, ge=1.0, le=10.0, description="账号数量乘数")
    ws_connect_interval: float = Field(default=5.0, ge=0.1, le=60.0, description="WebSocket连接间隔（秒/个）")
    stats_interval: float = Field(default=60.0, ge=5.0, le=300.0, description="统计打印间隔（秒）")
    message_send_interval_min: float = Field(default=5.0, ge=0.1, le=60.0, description="消息发送最小间隔（秒）")
    message_send_interval_max: float = Field(default=10.0, ge=0.1, le=60.0, description="消息发送最大间隔（秒）")
    lesson_restart_delay: float = Field(default=5.0, ge=0.1, le=30.0, description="课程结束后重新开始延迟（秒）")


class LoggingConfig(BaseModel):
    """日志配置"""
    level: str = Field(default_factory=lambda: os.getenv('LOG_LEVEL', 'INFO'), description="日志级别")
    format: str = Field(default="%(asctime)s - %(name)s - %(levelname)s - %(message)s", description="日志格式")
    encoding: str = Field(default="utf-8", description="日志编码")
    
    @validator('level')
    def validate_log_level(cls, v):
        valid_levels = ['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL']
        if v.upper() not in valid_levels:
            raise ValueError(f'日志级别必须是以下之一: {valid_levels}')
        return v.upper()


class TestDataConfig(BaseModel):
    """测试数据配置"""
    lesson_params: List[Dict[str, str]] = Field(default=[
        {
            "material_id": "topic_talk-b2-1/materials/topic_talk-b2-1-lesson_1-b_121001.json?sid=49&bid=51&gid=148&mid=8600",
            "character_id": "lily_white"
        }
    ], description="课程参数列表")
    
    test_messages: List[str] = Field(default=[
        "Hello! How are you today?",
        "What's the weather like?",
        "Can you tell me a story?",
        "I like playing games.",
        "Thank you for your help!"
    ], description="测试消息列表")


class TestConfig(BaseModel):
    """主配置类，包含所有子配置"""
    api: APIConfig = Field(default_factory=APIConfig)
    websocket: WebSocketConfig = Field(default_factory=WebSocketConfig)
    connection: ConnectionConfig = Field(default_factory=ConnectionConfig)
    timing: TimingConfig = Field(default_factory=TimingConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    test_data: TestDataConfig = Field(default_factory=TestDataConfig)
    
    def setup_logging(self):
        """设置日志配置"""
        logging.basicConfig(
            level=getattr(logging, self.logging.level),
            format=self.logging.format,
            encoding=self.logging.encoding
        )
        return logging.getLogger(__name__)


# 全局配置实例
config = TestConfig()

# 为了向后兼容，保留原有的变量名
BASE_API_URL = config.api.base_url
TEST_ACCOUNT_API = config.api.test_account_api
AUTH_TOKEN = config.api.auth_token
LOGIN_ENDPOINT = config.api.login_endpoint
USER_PROFILE_ENDPOINT = config.api.user_profile_endpoint
START_LESSON_ENDPOINT = config.api.start_lesson_endpoint

MAX_LOGIN_ATTEMPTS = config.connection.max_login_attempts
MAX_ACCOUNT_ATTEMPTS = config.connection.max_account_attempts
WEBSOCKET_HEARTBEAT_INTERVAL = config.websocket.heartbeat_interval
USER_CHECK_INTERVAL = config.connection.user_check_interval
ACCOUNT_POOL_TIMEOUT_MULTIPLIER = config.connection.account_pool_timeout_multiplier

# 新增：并发控制配置
SYNC_BATCH_SIZE = config.connection.sync_batch_size
WEBSOCKET_BATCH_SIZE = config.connection.websocket_batch_size
BATCH_START_INTERVAL = config.connection.batch_start_interval

DEFAULT_LOGIN_INTERVAL = config.timing.login_interval
DEFAULT_ACCOUNT_MULTIPLIER = config.timing.account_multiplier
DEFAULT_WS_CONNECT_INTERVAL = config.timing.ws_connect_interval
DEFAULT_STATS_INTERVAL = config.timing.stats_interval
DEFAULT_MESSAGE_SEND_INTERVAL_MIN = config.timing.message_send_interval_min
DEFAULT_MESSAGE_SEND_INTERVAL_MAX = config.timing.message_send_interval_max
DEFAULT_LESSON_RESTART_DELAY = config.timing.lesson_restart_delay

LESSON_PARAMS_LIST = config.test_data.lesson_params
TEST_MESSAGES = config.test_data.test_messages

LOG_LEVEL = config.logging.level
LOG_FORMAT = config.logging.format
LOG_ENCODING = config.logging.encoding

WS_BASE_URL = config.websocket.base_url
WS_PARAMS = config.websocket.params 