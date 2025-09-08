"""
批次管理器模块
负责控制用户分批启动和WebSocket连接分批建立
"""
import threading
import time
import logging
from typing import List, Callable, Optional
from config import SYNC_BATCH_SIZE, WEBSOCKET_BATCH_SIZE, BATCH_START_INTERVAL

logger = logging.getLogger(__name__)


class BatchManager:
    """批次管理器，控制并发数量"""
    
    def __init__(self):
        self.sync_batch_size = SYNC_BATCH_SIZE
        self.websocket_batch_size = WEBSOCKET_BATCH_SIZE
        self.batch_start_interval = BATCH_START_INTERVAL
        
        # 同步控制
        self.sync_lock = threading.Lock()
        self.sync_batch_count = 0
        self.sync_events = {}  # 存储每批的同步事件
        
        # WebSocket连接控制
        self.ws_lock = threading.Lock()
        self.ws_batch_count = 0
        self.ws_events = {}  # 存储每批的WebSocket连接事件
        
        logger.info(f"批次管理器初始化完成 - 同步批次大小: {self.sync_batch_size}, WebSocket批次大小: {self.websocket_batch_size}")
    
    def get_sync_batch_id(self, user_number: str) -> int:
        """获取用户的同步批次ID"""
        with self.sync_lock:
            # 根据用户编号计算批次ID
            try:
                user_id = int(user_number.split('_')[1])  # 从 user_1 中提取 1
            except (IndexError, ValueError):
                # 如果解析失败，使用哈希值作为用户ID
                user_id = hash(user_number) % 1000 + 1
            batch_id = (user_id - 1) // self.sync_batch_size
            
            # 初始化批次事件
            if batch_id not in self.sync_events:
                self.sync_events[batch_id] = {
                    'event': threading.Event(),
                    'count': 0,
                    'users': []
                }
            
            self.sync_events[batch_id]['count'] += 1
            self.sync_events[batch_id]['users'].append(user_number)
            
            logger.info(f"{user_number} 分配到同步批次 {batch_id}，当前批次用户数: {self.sync_events[batch_id]['count']}")
            return batch_id
    
    def wait_for_sync_batch(self, user_number: str, batch_id: int) -> bool:
        """等待同步批次就绪"""
        if batch_id not in self.sync_events:
            logger.error(f"{user_number} 批次 {batch_id} 不存在")
            return False
        
        batch_info = self.sync_events[batch_id]
        
        # 检查是否已经达到批次大小
        if batch_info['count'] >= self.sync_batch_size:
            batch_info['event'].set()
            logger.info(f"{user_number} 同步批次 {batch_id} 已就绪，用户数: {batch_info['count']}")
            return True
        
        # 如果当前批次用户数较少，等待一段时间后直接启动
        if batch_info['count'] < self.sync_batch_size:
            # 等待一小段时间，看是否有其他用户加入
            time.sleep(2.0)
            # 重新检查用户数，如果仍然少于批次大小，直接启动
            if batch_info['count'] < self.sync_batch_size:
                batch_info['event'].set()
                logger.info(f"{user_number} 同步批次 {batch_id} 用户数不足，直接启动，用户数: {batch_info['count']}")
                return True
        
        # 等待批次就绪
        logger.info(f"{user_number} 等待同步批次 {batch_id} 就绪（当前: {batch_info['count']}/{self.sync_batch_size}）")
        
        # 计算超时时间（基于批次大小和启动间隔）
        timeout = self.sync_batch_size * self.batch_start_interval * 2  # 给2倍缓冲时间
        batch_info['event'].wait(timeout=timeout)
        
        if batch_info['event'].is_set():
            logger.info(f"{user_number} 同步批次 {batch_id} 已就绪")
            return True
        else:
            logger.warning(f"{user_number} 同步批次 {batch_id} 等待超时")
            return False
    
    def get_websocket_batch_id(self, user_number: str) -> int:
        """获取用户的WebSocket连接批次ID"""
        with self.ws_lock:
            # 根据用户编号计算批次ID
            try:
                user_id = int(user_number.split('_')[1])  # 从 user_1 中提取 1
            except (IndexError, ValueError):
                # 如果解析失败，使用哈希值作为用户ID
                user_id = hash(user_number) % 1000 + 1
            batch_id = (user_id - 1) // self.websocket_batch_size
            
            # 初始化批次事件
            if batch_id not in self.ws_events:
                self.ws_events[batch_id] = {
                    'event': threading.Event(),
                    'count': 0,
                    'users': []
                }
            
            self.ws_events[batch_id]['count'] += 1
            self.ws_events[batch_id]['users'].append(user_number)
            
            logger.info(f"{user_number} 分配到WebSocket批次 {batch_id}，当前批次用户数: {self.ws_events[batch_id]['count']}")
            return batch_id

    def wait_for_websocket_batch(self, user_number: str, batch_id: int) -> bool:
        """等待WebSocket连接批次就绪"""
        if batch_id not in self.ws_events:
            logger.error(f"{user_number} WebSocket批次 {batch_id} 不存在")
            return False

        batch_info = self.ws_events[batch_id]

        # 检查是否已经达到批次大小
        if batch_info['count'] >= self.websocket_batch_size:
            batch_info['event'].set()
            logger.info(f"{user_number} WebSocket批次 {batch_id} 已就绪，用户数: {batch_info['count']}")
            return True

        # 如果当前批次只有一个用户，直接触发（避免单用户等待）
        if batch_info['count'] == 1:
            # 等待一小段时间，看是否有其他用户加入
            time.sleep(1.0)
            if batch_info['count'] == 1:
                batch_info['event'].set()
                logger.info(f"{user_number} WebSocket批次 {batch_id} 单用户直接启动，用户数: {batch_info['count']}")
                return True

        # 等待批次就绪
        logger.info(
            f"{user_number} 等待WebSocket批次 {batch_id} 就绪（当前: {batch_info['count']}/{self.websocket_batch_size}）")

        # 计算超时时间
        timeout = self.websocket_batch_size * self.batch_start_interval * 2
        batch_info['event'].wait(timeout=timeout)

        if batch_info['event'].is_set():
            logger.info(f"{user_number} WebSocket批次 {batch_id} 已就绪")
            return True
        else:
            # 修改：超时后也允许启动，避免用户无法建立连接
            logger.warning(f"{user_number} WebSocket批次 {batch_id} 等待超时，但允许启动")
            batch_info['event'].set()  # 手动触发事件，让其他等待的用户也能启动
            return True
    
    def get_batch_stats(self) -> dict:
        """获取批次统计信息"""
        with self.sync_lock:
            sync_stats = {
                'total_batches': len(self.sync_events),
                'batches': {}
            }
            for batch_id, batch_info in self.sync_events.items():
                sync_stats['batches'][batch_id] = {
                    'user_count': batch_info['count'],
                    'users': batch_info['users'],
                    'ready': batch_info['event'].is_set()
                }
        
        with self.ws_lock:
            ws_stats = {
                'total_batches': len(self.ws_events),
                'batches': {}
            }
            for batch_id, batch_info in self.ws_events.items():
                ws_stats['batches'][batch_id] = {
                    'user_count': batch_info['count'],
                    'users': batch_info['users'],
                    'ready': batch_info['event'].is_set()
                }
        
        return {
            'sync_batches': sync_stats,
            'websocket_batches': ws_stats
        }


# 全局批次管理器实例
batch_manager = BatchManager()
