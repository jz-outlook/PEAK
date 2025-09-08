# AI Talk WebSocket 压力测试系统

一个基于 Locust 的 WebSocket 压力测试系统，专门用于测试 AI Talk 平台的并发性能和稳定性。

## 📋 目录

- [项目概述](#项目概述)
- [功能特性](#功能特性)
- [系统架构](#系统架构)
- [安装配置](#安装配置)
- [使用方法](#使用方法)
- [配置说明](#配置说明)
- [命令行参数](#命令行参数)
- [日志系统](#日志系统)
- [性能监控](#性能监控)
- [故障排除](#故障排除)
- [项目结构](#项目结构)

## 🎯 项目概述

本项目是一个专业的 WebSocket 压力测试工具，模拟真实用户行为，包括：
- 用户登录和认证
- 课程选择和开始
- WebSocket 连接建立
- 实时消息交互
- 课程结束和重连

支持多用户并发测试，提供详细的性能统计和监控功能。

## ✨ 功能特性

### 🔐 用户管理
- **自动账号获取**：从测试API自动获取测试账号
- **账号池管理**：线程安全的账号池，支持多用户共享
- **登录验证**：完整的用户登录和token验证流程

### 🌐 WebSocket 连接
- **智能重连**：自动检测连接状态，支持断线重连
- **心跳机制**：定期发送心跳包保持连接活跃
- **消息处理**：完整的消息收发和响应处理

### 📊 性能监控
- **实时统计**：活跃连接数、累计连接数、失败次数等
- **详细分析**：连接断开原因分类统计
- **性能指标**：响应时间、吞吐量等关键指标

### 🚀 批次控制
- **同步启动**：支持分批启动用户，避免系统过载
- **连接控制**：控制WebSocket连接建立的速度
- **资源管理**：智能的资源分配和释放

## 🏗️ 系统架构

```
┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐
│   Locust UI     │    │   Main Entry    │    │  User Behavior  │
│   (Web界面)      │◄──►│   (main.py)     │◄──►│ (user_behavior) │
└─────────────────┘    └─────────────────┘    └─────────────────┘
                                │                        │
                                ▼                        ▼
                       ┌─────────────────┐    ┌─────────────────┐
                       │   Config Mgmt   │    │ Account Manager │
                       │   (config.py)   │    │(account_manager)│
                       └─────────────────┘    └─────────────────┘
                                │                        │
                                ▼                        ▼
                       ┌─────────────────┐    ┌─────────────────┐
                       │  Batch Manager  │    │WebSocket Manager│
                       │(batch_manager)  │    │(websocket_mgr)  │
                       └─────────────────┘    └─────────────────┘
                                │                        │
                                ▼                        ▼
                       ┌─────────────────┐    ┌─────────────────┐
                       │  Stats Manager  │    │   Test APIs     │
                       │ (stats_manager) │    │  (External)     │
                       └─────────────────┘    └─────────────────┘
```

## 🛠️ 安装配置

### 环境要求
- Python 3.9+
- Locust 2.34.0+
- 网络连接到测试环境

### 安装步骤

1. **克隆项目**
```bash
git clone <repository-url>
cd locust_test
```

2. **创建虚拟环境**
```bash
python -m venv venv
source venv/bin/activate  # Linux/Mac
# 或
venv\Scripts\activate     # Windows
```

3. **安装依赖**
```bash
pip install -r requirements.txt
```

4. **验证安装**
```bash
locust --version
```

## 🚀 使用方法

### 基本使用

#### 1. Web界面模式（推荐）
```bash
# 启动Web界面
locust -f main.py

# 在浏览器中访问 http://localhost:8089
# 设置用户数和启动速率，点击"Start swarming"
```

#### 2. 命令行模式
```bash
# 基本测试
locust -f main.py --users 10 --spawn-rate 2 --run-time 60s --headless

# 详细参数测试
locust -f main.py --users 50 --spawn-rate 5 --run-time 300s --headless \
  --login-interval 1.0 --log-level DEBUG
```

#### 3. 使用启动脚本
```bash
# DEBUG级别日志
./start_debug.sh

# INFO级别日志  
./start_info.sh
```

### 高级使用

#### 多用户压力测试
```bash
# 100个用户，每秒启动5个，运行5分钟
locust -f main.py --users 100 --spawn-rate 5 --run-time 300s --headless
```

#### 长时间稳定性测试
```bash
# 50个用户，运行1小时
locust -f main.py --users 50 --spawn-rate 2 --run-time 3600s --headless
```

## ⚙️ 配置说明

### 主要配置文件：`config.py`

#### API配置
```python
class APIConfig(BaseModel):
    base_url: str = "https://api-test-ws.myaitalk.vip"
    test_account_api: str = "https://watchdog.myaitalk.vip/register_test_account"
    auth_token: str = "NjExveResQZUKqFXCurPed5kXeSHkZsW"
```

#### WebSocket配置
```python
class WebSocketConfig(BaseModel):
    base_url: str = "wss://wss-test-ws.myaitalk.vip"
    heartbeat_interval: int = 10  # 心跳间隔（秒）
    max_reconnect_attempts: int = 5  # 最大重连次数
```

#### 连接配置
```python
class ConnectionConfig(BaseModel):
    login_interval: float = 2.0  # 登录间隔（秒）
    ws_connect_interval: float = 5.0  # WebSocket连接间隔（秒）
    sync_batch_size: int = 5  # 同步批次大小
    websocket_batch_size: int = 5  # WebSocket批次大小
    batch_start_interval: float = 2.0  # 批次启动间隔（秒）
```

#### 测试数据配置
```python
class TestDataConfig(BaseModel):
    target_users: int = 10  # 目标用户数
    account_multiplier: float = 1.0  # 账号倍数
    lesson_restart_delay: float = 2.0  # 课程重启延迟（秒）
```

## 📝 命令行参数

### Locust 标准参数
- `--users`: 目标用户数
- `--spawn-rate`: 用户启动速率（每秒）
- `--run-time`: 运行时间
- `--headless`: 无头模式（命令行运行）

### 自定义参数
- `--login-interval`: 登录间隔（秒/个）
- `--log-level`: 日志级别 (DEBUG, INFO, WARNING, ERROR, CRITICAL)

### 示例
```bash
# 完整参数示例
locust -f main.py \
  --users 20 \
  --spawn-rate 3 \
  --run-time 120s \
  --headless \
  --login-interval 1.5 \
  --log-level INFO
```

## 📊 日志系统

### 日志级别
- **DEBUG**: 详细的调试信息，包括所有消息内容
- **INFO**: 一般信息，包括连接状态、用户行为等
- **WARNING**: 警告信息，如重连、超时等
- **ERROR**: 错误信息，如连接失败、API错误等
- **CRITICAL**: 严重错误，如系统崩溃等

### 日志配置
```python
# 通过环境变量设置
export LOG_LEVEL=DEBUG
locust -f main.py

# 通过命令行参数设置
locust -f main.py --log-level DEBUG

# 通过配置文件设置
# 在 config.py 中修改 LoggingConfig.level
```

### 日志示例
```
[2025-09-06 16:37:17,300] INFO/websocket_manager: user_1 WebSocket连接成功，当前活跃: 1 | 累计连接: 1
[2025-09-06 16:37:23,157] DEBUG/websocket_manager: user_1 发送消息：Hello! How are you today?
[2025-09-06 16:37:24,061] DEBUG/websocket_manager: user_1 【收到含respId的消息】[respId:resp68bbf2c3673a4458459012]You did great!
```

## 📈 性能监控

### 实时统计信息
```
=== WebSocket连接统计 === 
目标: 10 | 活跃: 8 | 累计: 12
失败: 1 | 重连: 2
断开原因统计:
  正常关闭: 5
  协议错误: 0
  服务器主动关闭: 1
  网络问题: 3
  未知原因: 0
=======================
```

### 关键指标
- **目标连接数**: 配置的目标用户数
- **活跃连接数**: 当前活跃的WebSocket连接数
- **累计连接数**: 总共建立的连接数（包括重连）
- **失败连接数**: 连接失败的次数
- **重连次数**: 自动重连的次数

### Locust统计报告
- **请求统计**: API请求的成功率和响应时间
- **用户统计**: 用户启动、停止、错误等统计
- **性能图表**: 实时性能曲线图

## 🔧 故障排除

### 常见问题

#### 1. 连接失败
```
WebSocket连接失败：关键参数无效/缺失 {'phone_number': None}
```
**解决方案**: 检查账号登录是否成功，确保获取到有效的access_token

#### 2. 登录失败
```
登录失败: 缺少手机号
```
**解决方案**: 检查测试账号API是否正常，验证API响应格式

#### 3. 用户数量不足
```
账号池数量未达标，终止测试
```
**解决方案**: 增加目标用户数或检查网络连接

#### 4. 端口占用
```
OSError: [Errno 48] Address already in use: ('', 8089)
```
**解决方案**: 
```bash
# 停止之前的Locust进程
pkill -f locust
# 或使用无头模式
locust -f main.py --headless
```

### 调试技巧

#### 1. 启用DEBUG日志
```bash
LOG_LEVEL=DEBUG locust -f main.py --users 1 --headless --run-time 30s
```

#### 2. 单用户测试
```bash
# 先进行单用户测试，确保基本功能正常
locust -f main.py --users 1 --headless --run-time 60s
```

#### 3. 检查网络连接
```bash
# 测试API连接
curl -X POST https://watchdog.myaitalk.vip/register_test_account
```

## 📁 项目结构

```
locust_test/
├── __init__.py                 # Python包标识
├── main.py                     # 主程序入口
├── config.py                   # 配置管理
├── user_behavior.py            # 用户行为类
├── websocket_manager.py        # WebSocket连接管理
├── account_manager.py          # 账号池管理
├── stats_manager.py            # 统计管理
├── batch_manager.py            # 批次管理
├── start_debug.sh              # DEBUG启动脚本
├── start_info.sh               # INFO启动脚本
├── README.md                   # 项目文档
├── BATCH_OPTIMIZATION.md       # 批次优化文档
└── backup/                     # 备份目录
    ├── back.py                 # 原始文件
    ├── default_config.py       # 原始配置
    └── README.md               # 备份说明
```

### 核心模块说明

#### `main.py`
- 程序入口点
- 命令行参数解析
- 事件监听器注册
- 配置初始化

#### `user_behavior.py`
- 用户行为模拟
- 登录流程控制
- 课程选择和管理
- 任务调度

#### `websocket_manager.py`
- WebSocket连接管理
- 消息收发处理
- 重连机制
- 心跳维护

#### `account_manager.py`
- 测试账号获取
- 账号池管理
- 登录验证
- 线程安全控制

#### `stats_manager.py`
- 连接统计
- 性能指标收集
- 数据格式化
- 实时监控

#### `batch_manager.py`
- 批次同步控制
- 用户启动管理
- 连接速度控制
- 资源分配

## 🤝 贡献指南

1. Fork 项目
2. 创建功能分支 (`git checkout -b feature/AmazingFeature`)
3. 提交更改 (`git commit -m 'Add some AmazingFeature'`)
4. 推送到分支 (`git push origin feature/AmazingFeature`)
5. 打开 Pull Request

## 📄 许可证

本项目采用 MIT 许可证 - 查看 [LICENSE](LICENSE) 文件了解详情

## 📞 支持

如果您遇到问题或有任何建议，请：
1. 查看 [故障排除](#故障排除) 部分
2. 提交 Issue
3. 联系开发团队

---

**注意**: 这是一个测试工具，请仅在测试环境中使用，不要在生产环境中运行。