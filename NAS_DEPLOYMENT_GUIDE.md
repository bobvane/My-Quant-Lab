创建 NAS 部署测试指南

## My Quant Lab NAS 部署与测试指南

本指南将指导您在 NAS 上部署和测试 My Quant Lab V1。系统遵循全新的架构设计，所有设计文档已更新到 GitHub 仓库。

### 📋 项目版本

- 版本: v0.0.1 (初始版本)
- 架构: 6 服务架构 (quantlab-web, quantlab-api, quantlab-worker, quantlab-scheduler, quantlab-postgres, quantlab-redis)
- 网络: 两层网络 (frontend, backend)
- 数据库: 17 张核心表，策略版本不可变

### 🚀 快速开始

#### 1. 克隆仓库

```bash
git clone https://github.com/bobvane/My-Quant-Lab.git
```

#### 2. 设置环境变量

```bash
cd My-Quant-Lab
source .env.example
# 编辑 .env 文件，设置必要的配置
# 例如: DB_PASSWORD, REDIS_PASSWORD 等
```

#### 3. 安装 Docker 和 Docker Compose

确保您的 NAS 上已安装 Docker 和 Docker Compose。如果没有，请安装:

```bash
# Ubuntu/Debian
 sudo apt update
sudo apt install docker.io docker-compose
# macOS
brew install docker
# Windows
# 下载 Docker Desktop 并安装
```

#### 4. 构建和运行

```bash
# 构建镜像
cd My-Quant-Lab
docker compose build

# 启动服务
docker compose up -d
```

#### 5. 访问服务

```bash
# Web 界面 (通过 NAS IP:80访问)
# API 健康检查 (通过 NAS IP:8080/api/v1/health)
```

### 📁 文件结构

```
My-Quant-Lab/
├── scripts/
│   └── version.sh               # 版本管理脚本
├── MY_QUANT_LAB_Docs/            # 设计文档汇总
│   └── summary.md               # 架构决策总结
├── My_Quant_Lab_Development_Docs/
│   ├── 00_README.md            # 项目总纲
│   ├── 01_PRODUCT_SPEC.md      # 产品需求
│   ├── 02_ARCHITECTURE.md      # 系统架构
│   ├── 03_MODULES.md          # 功能模块
│   ├── 04_STRATEGY_DSL.md      # 策略 DSL
│   ├── ...                     # 其他设计文档
│   └── README.md               # 开发文档包
├── .env.example                 # 环境配置示例
├── docker-compose.yml          # Docker Compose 配置
└── requirements.txt            # Python 依赖
```

### 🖥️ Docker Compose 配置

```yaml
version: '3.8'

services:
  quantlab-api:
    build: .
    ports:
      - "8080:8080"
    environment:
      - DATABASE_URL=postgresql://quant_user:${DB_PASSWORD}@quantlab-postgres:5432/quant_lab
      - REDIS_URL=redis://quantlab-redis:6379
    depends_on:
      - quantlab-postgres
      - quantlab-redis
    restart: unless-stopped

  quantlab-web:
    image: nginx:alpine
    ports:
      - "80:80"
    depends_on:
      - quantlab-api
    restart: unless-stopped

  quantlab-postgres:
    image: postgres:15-alpine
    environment:
      POSTGRES_DB: quant_lab
      POSTGRES_USER: quant_user
      POSTGRES_PASSWORD: ${DB_PASSWORD}
    volumes:
      - postgres_data:/var/lib/postgresql/data
    restart: unless-stopped

  quantlab-redis:
    image: redis:7-alpine
    command: redis-server --appendonly yes
    volumes:
      - redis_data:/data
    restart: unless-stopped

volumes:
  postgres_data:
  redis_data:
```

### 🛠️ 核心模块架构

#### 应用内部模块 (六层设计)

```
domain/
├── 领域模型和业务规则
├── 值对象和领域规则
└── 策略DSL和参数管理

data/
├── 数据库访问层
├── 数据迁移和备份
└── 数据质量验证

features/
├── 指标计算引擎
├── 价格行为特征提取
└── 特征缓存和管理

strategies/
├── 策略执行引擎
├── 规则验证和优化
└── 策略生命周期管理

research/
├── 回测引擎核心
├── OOS和Walk-Forward分析
└── 绩效评估和报告

simulation/
├── 模拟交易引擎
├── 持仓管理和风险控制
└── 交易记录和结算

ai/
├── AI提供者适配器
├── 解释和分析引擎
└── 提示模板管理和缓存

infrastructure/
└── 通用工具和支持服务
```

### 📊 数据库设计

#### 17 张核心表

```sql
-- assets: 资产信息
-- market_data: 市场数据
-- market_data_sources: 数据源配置
-- strategies: 策略主表
-- strategy_versions: 策略版本（不可变）
-- strategy_parameters: 策略参数
-- features: 特征定义
-- feature_snapshots: 特征快照
-- backtest_runs: 回测任务
-- backtest_results: 回测结果
-- backtest_metrics: 回测指标
-- paper_accounts: 模拟账户
-- paper_positions: 模拟持仓
-- paper_orders: 模拟订单
-- paper_trades: 模拟交易
-- ai_providers: AI提供商
-- ai_models: AI模型
-- ai_tasks: AI任务
-- ai_usage: AI使用统计
-- ai_prompts: AI提示模板
-- jobs: 作业任务
-- job_logs: 作业日志
-- audit_logs: 审计日志
-- system_settings: 系统配置
```

### 🔧 测试步骤

#### 1. 环境检查

```bash
# 检查 Docker 和 Docker Compose 是否安装
docker --version
docker compose version

# 检查系统资源
free -h

# 检查磁盘空间
 df -h /
```

#### 2. 配置检查

```bash
# 检查 .env 文件
cat .env | grep -E "(DB_PASSWORD|REDIS_PASSWORD|AI_API_KEY)"

# 检查配置文件
cat docker-compose.yml
```

#### 3. 构建和运行

```bash
# 构建镜像
docker compose build --no-cache

# 启动所有服务
docker compose up -d

# 检查日志
docker compose logs -f
```

#### 4. API 测试

```bash
# 健康检查
curl -f http://localhost:8080/api/v1/health

# 健康检查 (Web 代理)
curl -f http://localhost/api/v1/health
```

#### 5. 功能测试

1. **策略管理**
   - 创建新策略
   - 导入策略
   - 版本控制

2. **市场数据**
   - OHLCV 数据同步
   - 数据验证

3. **策略执行**
   - DSL 验证
   - 策略执行

4. **回测引擎**
   - 历史回测
   - OOS 验证

5. **模拟交易**
   - 虚拟账户管理
   - 交易执行

6. **AI 集成**
   - AI 提供商配置
   - 解释和分析

### 🔒 安全配置

#### 环境变量

```bash
# 数据库
DB_PASSWORD=your_secure_password

# Redis
REDIS_PASSWORD=your_secure_password

# AI 提供商
AI_API_KEY=your_ai_api_key
AI_PROVIDER=OpenAI

# Ghostfolio
GHOSTFOLIO_API_KEY=your_ghostfolio_api_key
GHOSTFOLIO_BASE_URL=https://your-ghostfolio-instance.com

# 通知
NOTIFICATION_WEBHOOK=your_webhook_url
```

#### 文件权限

```bash
# 设置正确的权限
chmod 600 .env
chmod 700 data/ logs/
```

### 📈 监控与日志

#### 日志

```bash
# API 日志
docker compose logs quantlab-api

# 数据库日志
docker compose logs quantlab-postgres

# Redis 日志
docker compose logs quantlab-redis
```

#### 健康检查

```bash
# Web 界面健康检查
curl -f http://localhost/health

# API 健康检查
curl -f http://localhost:8080/api/v1/health
```

### 🚀 常见问题与解决方法

#### 1. 端口冲突

```bash
# 检查端口占用情况
netstat -tuln | grep :80
netstat -tuln | grep :8080
```

#### 2. 数据库连接失败

```bash
# 检查 PostgreSQL 服务
docker compose logs quantlab-postgres

# 手动连接到数据库
docker compose exec quantlab-postgres psql -U quant_user -d quant_lab
```

#### 3. 镜像构建失败

```bash
# 清除缓存并重新构建
docker compose build --no-cache

# 检查构建日志
docker compose logs --no-color quantlab-api | tail -50
```

### 📚 后续学习

#### 基本功能

1. **策略管理**
   - 创建自定义策略
   - 导入 GitHub 策略
   - 策略版本控制

2. **回测引擎**
   - 历史回测
   - 性能指标计算
   - OOS 验证

3. **模拟交易**
   - 虚拟账户管理
   - 交易执行
   - 绩效分析

#### 高级功能

1. **AI 集成**
   - 配置 OpenAI 适配器
   - AI 解释功能
   - 预算管理和任务路由

2. **GitHub 策略导入**
   - 仓库导入
   - 策略提取
   - 版本控制

3. **Web UI**
   - 仪表盘
   - 策略管理
   - 回测分析

### 🎯 项目目标

My Quant Lab V1 专注于：

- **策略验证和模拟交易**
- **AI 辅助策略发现**
- **实时信号生成**
- **完整审计跟踪**

**不**提供：
- 自动真实交易
- 券商 API 执行
- AI 决策自动执行

### 🏷️ 版本控制

```bash
# 当前版本检查
cd scripts && ./version.sh show

# 创建新版本
cd scripts && ./version.sh create

# 生成发布包
cd scripts && ./version.sh release
```

### 🛠️ 常见命令

```bash
# 克隆和初始化
cd ~
git clone https://github.com/bobvane/My-Quant-Lab.git
cd My-Quant-Lab
source .env.example
nano .env  # 配置环境变量

# 构建和运行
docker compose build
docker compose up -d

# 查看日志
docker compose logs -f

# 重启服务
docker compose down
docker compose up -d

# 停止服务
docker compose down

# 清理缓存
docker compose down --rmi local
```

### 📞 技术支持

如果遇到任何问题，请检查以下内容：

1. **环境配置**
   - Docker 和 Docker Compose 是否安装
   - 所有环境变量是否配置
   - 端口是否有冲突

2. **日志**
   - 检查服务日志
   - 查看 docker compose logs
   - 验证健康检查端点

3. **网络**
   - 检查 Docker 网络配置
   - 验证服务连接

如果问题仍然存在，请参考完整的文档或 GitHub 仓库Issues。