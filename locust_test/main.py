"""
主程序入口模块
负责程序初始化、事件注册和配置加载
"""
import logging
from locust import events
from locust.runners import WorkerRunner

from config import config, DEFAULT_LOGIN_INTERVAL, DEFAULT_ACCOUNT_MULTIPLIER, DEFAULT_WS_CONNECT_INTERVAL, DEFAULT_STATS_INTERVAL, DEFAULT_MESSAGE_SEND_INTERVAL_MIN, DEFAULT_MESSAGE_SEND_INTERVAL_MAX, SYNC_BATCH_SIZE, WEBSOCKET_BATCH_SIZE, BATCH_START_INTERVAL
from user_behavior import UserBehavior

# 设置日志
logger = config.setup_logging()


@events.init_command_line_parser.add_listener
def _(parser):
    """添加自定义命令行参数"""
    parser.add_argument(
        "--login-interval",
        type=float,
        default=DEFAULT_LOGIN_INTERVAL,
        help="登录间隔（秒/个）"
    )
    parser.add_argument(
        "--log-level",
        type=str,
        default=config.logging.level,
        help="日志级别 (DEBUG, INFO, WARNING, ERROR, CRITICAL)"
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
    parser.add_argument(
        "--message-send-interval-min",
        type=float,
        default=DEFAULT_MESSAGE_SEND_INTERVAL_MIN,
        help="消息发送最小间隔（秒）"
    )
    parser.add_argument(
        "--message-send-interval-max",
        type=float,
        default=DEFAULT_MESSAGE_SEND_INTERVAL_MAX,
        help="消息发送最大间隔（秒）"
    )
    parser.add_argument(
        "--sync-batch-size",
        type=int,
        default=SYNC_BATCH_SIZE,
        help="同步批次大小（每批同时启动的用户数）"
    )
    parser.add_argument(
        "--websocket-batch-size",
        type=int,
        default=WEBSOCKET_BATCH_SIZE,
        help="WebSocket连接批次大小（每批同时建立的连接数）"
    )
    parser.add_argument(
        "--batch-start-interval",
        type=float,
        default=BATCH_START_INTERVAL,
        help="批次启动间隔（秒）"
    )


@events.init.add_listener
def _(environment, **kwargs):
    """初始化事件监听器，打印测试配置信息"""
    if not isinstance(environment.runner, WorkerRunner):
        login_interval = environment.parsed_options.login_interval
        account_multiplier = environment.parsed_options.account_multiplier
        ws_connect_interval = environment.parsed_options.ws_connect_interval
        stats_interval = environment.parsed_options.stats_interval
        sync_batch_size = environment.parsed_options.sync_batch_size
        websocket_batch_size = environment.parsed_options.websocket_batch_size
        batch_start_interval = environment.parsed_options.batch_start_interval
        
        # 日志级别参数
        log_level = getattr(environment.parsed_options, 'log_level', config.logging.level)
        
        # 设置日志级别
        import logging
        logging.getLogger().setLevel(getattr(logging, log_level))
        
        total_users = environment.runner.target_user_count if hasattr(environment.runner, 'target_user_count') else 10
        target_accounts = max(1, int(total_users * account_multiplier))
        
        logger.info(
            f"测试配置：用户数={total_users}，目标账号数={target_accounts}，"
            f"登录间隔={login_interval}秒，连接间隔={ws_connect_interval}秒，"
            f"统计打印间隔={stats_interval}秒，日志级别={log_level}"
        )
        logger.info(
            f"批次控制：同步批次大小={sync_batch_size}，WebSocket批次大小={websocket_batch_size}，"
            f"批次启动间隔={batch_start_interval}秒"
        )


# 导出用户类
user_classes = [UserBehavior]


if __name__ == "__main__":
    # 如果直接运行此文件，可以在这里添加一些初始化逻辑
    logger.info("AI Talk WebSocket 压力测试工具已启动")
    logger.info("使用 'locust -f main.py' 命令启动测试")
