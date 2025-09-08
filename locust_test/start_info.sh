#!/bin/bash
# AI Talk WebSocket 压力测试启动脚本 - INFO模式

echo "启动AI Talk WebSocket压力测试工具 (INFO模式)"
echo "Web界面地址: http://0.0.0.0:8089"
echo "日志级别: INFO"
echo ""

# 设置环境变量
export LOG_LEVEL=INFO

# 启动Locust
locust -f main.py --host=https://api-test-ws.myaitalk.vip

