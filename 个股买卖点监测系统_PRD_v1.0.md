# 个股买卖点监测系统 PRD

| 字段 | 内容 |
|------|------|
| 文档版本 | v2.2 |
| 创建日期 | 2026-06-01；v2.2 生产级采集修复 |
| 修订记录 | v1.0~v1.7 见历史版本；v2.0 重大架构变更 — 数据源收敛为 baostock 单一数据源，去掉三交易所爬虫和 akshare，cron 调度对齐 baostock 时刻表(17:35)；v2.1 与代码实现对齐修正 — baostock API 批量拉取（非逐日下载交易所文件），开发环境 SQLite/生产 PostgreSQL，前端使用静态 HTML（非 Vue 3），无 Alembic 迁移（使用 init_db 裸 SQL），复权因子尚未实现（close_hfq=close 占位），部分功能为待实现状态；v2.2 生产级采集修复 — 每日增量更新重写为生产级（A股过滤/重试/重登录/批量INSERT/失败记录），catch_up 补采实现，stock_name 修复，数据完整性验证通过（6346只/656万行，覆盖沪深全量A股） |
| 产品名称 | 个股买卖点监测系统 |
| 技术基座 | FastAPI + DeepSeek API（自然语言理解） |
| 部署环境 | 华为云 / 自备 CentOS 服务器 |
| 使用渠道 | 飞书（单用户，自然语言交互）+ Web 界面（只读看板） |

---

## 1 产品概述

### 1.1 背景与目标

用户需要一个基于日行情数据的个股买卖点监测系统，能够自动采集沪深全市场历史及每日行情数据，基于技术分析策略生成买卖信号，并通过飞书向单人推送持仓管理提醒与全市场买点扫描结果。同时，提供简单的 Web 界面用于直观查看持仓、买点清单和个股详情。

核心目标：

- **数据自主可控**：通过 baostock（免费开源证券数据库）获取全市场数据，数据权威可靠、可追溯
- **策略可组合**：支持多策略勾选叠加，策略逻辑透明可解释
- **轻量实用**：仅使用日行情数据，不需要实时行情，降低系统复杂度和运维成本
- **一人专用**：飞书通道 + Web 界面（只读），安全可控

### 1.2 产品范围

| 范围 | 说明 |
|------|------|
| 包含 | 日行情数据采集与存储、技术策略计算引擎、持仓管理、买卖点提醒、飞书推送、简单 Web 看板（持仓/买点/个股详情/数据状态） |
| 不包含 | 实时行情接入、自动化交易执行、回测框架、多用户支持、复杂前端配置 |

### 1.3 术语定义

| 术语 | 定义 |
|------|------|
| 日行情 | 交易所每个交易日收盘后发布的个股 OHLCV 数据（开盘价、最高价、最低价、收盘价、成交量），通过 baostock 获取 |
| baostock | 免费开源证券数据库（www.baostock.com），覆盖沪深京全市场 A 股日K线、复权因子、基本面、行业分类等数据，日K 17:30 入库就绪 |
| 左右侧偏好 | 左侧交易（提前布局）与右侧交易（趋势确认后入场）的信号倾向配置 |
| 量价背离 | 价格创新高/新低但成交量未能同步放大/缩小的技术形态 |
| 布林线 | 基于 N 日均线 ± K 倍标准差构建的通道指标 |
| 仓位 | 用户持有某只个股的数量和成本价 |

---

## 2 系统架构

### 2.1 整体架构

![系统架构图](./diagrams/system_architecture.svg)

```
┌─────────────────────────────────────────────────────────────────┐
│                     ECS / CentOS 服务器                          │
│                                                                 │
│  ┌──────────────────────┐   ┌─────────────┐  ┌───────────────┐ │
│  │ Data Crawler         │   │ Strategy    │  │ Portfolio     │ │
│  │  baostock API 批量拉取│→ │  Engine     │→ │  Manager      │ │
│  │  (cron 触发, 17:35)  │   │  策略引擎    │  │  持仓管理     │ │
│  └──────────┬───────────┘   └──────┬──────┘  └───────┬───────┘ │
│             │                      │                  │         │
│  ┌──────────▼──────────────────────▼──────────────────▼───────┐ │
│  │              PostgreSQL / SQLite 数据库                     │ │
│  │    (dev=SQLite, prod=PostgreSQL)                           │ │
│  │    (行情库 / 策略库 / 持仓库 / 日历库)                     │ │
│  └───────────────────┬─────────────────────────────────────────┘ │
│                      │                                           │
│  ┌───────────────────┼────────────────────────────┐              │
│  │                   │                            │              │
│  ┌──▼──────────┐  ┌──▼──────┐  ┌─────────────┐   │              │
│  │  FastAPI     │  │ Redis 7 │  │  Nginx      │   │              │
│  │  :8000       │  │(缓存/历 │  │  静态前端   │   │              │
│  │  ├飞书Webhook│  │ 史/限频)│  │  (HTML/JS)  │   │              │
│  │  ├REST API   │  └─────────┘  └──────┬──────┘   │              │
│  │  ├动态上下文 │                      │           │              │
│  │  └DeepSeek↗  │                      │           │              │
│  └──────┬───────┘                      │           │              │
│         │                              │           │              │
└─────────┼──────────────────────────────┼───────────┼──────────────┘
          │HTTPS                         │HTTPS      │
    ┌─────▼─────┐                   ┌────▼──────────▼──┐
    │ 飞书 Bot  │                   │  用户浏览器      │
    │(自然语言) │                   │  (Web 看板)     │
    └─────┬─────┘                   └─────────────────┘
          │
    ┌─────▼─────────┐
    │ DeepSeek API  │  ← 云端 AI，自然语言理解 + Function Calling
    │ (api.deepseek │     每次请求携带动态系统上下文
    │  .com)        │
    └───────────────┘
```

> **数据源说明**：数据不直接从三大交易所官网下载，而是通过 baostock SDK 批量拉取。baostock 每天 17:30 完成日K线入库，系统 cron 在 17:35 触发采集。这种方式的优势：① 无需分别对接三大交易所的不同文件格式（CSV/XLSX）；② baostock 原生提供后复权价格（adjustflag='2'），减少复权因子计算工作量；③ 全量股票代码列表一次查询即可获得，无需逐个交易所合并。

### 2.2 技术选型

| 组件 | 选型 | 说明 |
|------|------|------|
| Web 框架 | FastAPI (Python 3.11) | 飞书 Webhook + REST API + 动态上下文组装 + DeepSeek 调用，单进程覆盖全部后端逻辑 |
| AI 服务 | DeepSeek API (deepseek-chat) | 自然语言理解 + Function Calling，国内直连延迟低、成本极低 |
| 云服务器 | 华为云 ECS 4C8G / 自备 CentOS 7+ | 数据计算+Web 服务共用 |
| 数据库 | **PostgreSQL 15（生产）/ SQLite（开发）** | 开发环境用 SQLite 零配置启动；生产环境使用 PG 支持并发写入和高级 SQL 功能。通过 `DATABASE_URL` 环境变量切换 |
| Web 前端 | **静态 HTML + JavaScript（非 Vue 3）** | 纯静态页面，通过 Fetch API 调用后端 REST 接口。所有逻辑内嵌在单页面中，无需构建工具链 |
| 前端托管 | Nginx | 静态文件托管 + 反向代理 + SSL 终结 |
| 域名 | 用户自有域名 | 配置 DNS 解析至服务器公网 IP |
| SSL 证书 | Let's Encrypt（自动续期） | 强制 HTTPS |
| 定时调度 | 系统 cron | 数据采集 (17:35, 对齐 baostock 17:30 日K就绪)、健康检查 (每小时)、数据库备份 (每周日) |
| 消息通道 | 飞书 Bot API（FastAPI 直接回复） | 当前通过 Webhook 同步回复，**未实现主动推送**（后续通过飞书 API 扩展） |
| 数据源 | **baostock**（免费开源证券数据库，单一数据源） | 覆盖沪深 A 股（不含北交所），提供日K线 + 复权因子 + 基本面 + 行业分类，日K 17:30 就绪。通过 Python SDK 批量拉取，无需逐个交易所下载文件 |
| 数据缓存 | Redis 7 | 对话历史（7天 TTL，30轮消息+自动摘要）、限频计数 |
| 数据分析 | pandas / TA-Lib | 行情数据处理与技术指标计算（TA-Lib C 库安装失败时自动降级为纯 numpy/pandas 实现） |
| 安全层 | VPC + 安全组 + SSL + JWT 认证 | 网络隔离与身份认证 |

### 2.3 调度分工

系统存在两层触发机制：

| 调度器 | 职责 | 触发方式 |
|--------|------|---------|
| 系统 cron | 数据采集（17:30）、健康检查（每小时）、数据库备份（每周日） | 定时触发 |
| FastAPI (飞书 Webhook) | 接收飞书消息 → 组装动态上下文 → DeepSeek API 理解意图 → Function Calling 执行 → 飞书回复 | 事件驱动（用户发消息即触发） |
| 数据采集脚本回调 | 采集成功后触发策略计算 | 事件驱动（采集脚本末尾调用） |

**AI 交互链路**：飞书消息到达 → FastAPI 组装动态系统上下文（持仓/信号/状态）→ DeepSeek API → Function Calling → Python 执行 → 结果返回飞书。整个链路无 Agent 框架依赖，上下文由数据库状态实时组装。

cron 配置示例：

```cron
# 每交易日 17:30 数据采集（采集成功后自动触发策略计算）
35 17 * * 1-5 /opt/stock-monitor/scripts/daily_crawl.sh >> /var/log/stock-monitor/crawl.log 2>&1
# 每小时健康检查
0 * * * * /opt/stock-monitor/scripts/health_check.sh >> /var/log/stock-monitor/health.log 2>&1
# 每周日 02:00 数据库全量备份
0 2 * * 0 /opt/stock-monitor/scripts/db_backup.sh >> /var/log/stock-monitor/backup.log 2>&1
```

`daily_crawl.sh` 脚本（含交易日判断、内部重试、采集成功后触发策略计算）：

```bash
#!/bin/bash
# daily_crawl.sh — 每日数据采集
# 由 cron 在每个交易日 17:30 触发，脚本内部处理重试和策略计算触发

MAX_RETRIES=3
RETRY_DELAYS=(1800 1800 3600)  # 30分钟、30分钟、1小时

# 1. 检查当日是否为交易日（静默退出，非错误）
python3 -c "from calendar_utils import is_trade_day; exit(0 if is_trade_day() else 1)"
if [ $? -ne 0 ]; then
    echo "$(date '+%Y-%m-%d %H:%M:%S'): 非交易日，跳过采集" >> /var/log/stock-monitor/crawl.log
    exit 0
fi

# 2. 自动检测并补采缺失的交易日（最多补采10天，超出则跳过并飞书通知）
python3 /opt/stock-monitor/crawler/catch_up.py --max-days 10 >> /var/log/stock-monitor/crawl.log 2>&1

# 3. 采集当日数据（含内部重试）
for i in $(seq 0 $MAX_RETRIES); do
    python3 /opt/stock-monitor/crawler/daily_crawl.py
    if [ $? -eq 0 ]; then
        echo "$(date '+%Y-%m-%d %H:%M:%S'): 采集成功（第$((i+1))次尝试），触发策略计算" >> /var/log/stock-monitor/crawl.log
        python3 /opt/stock-monitor/strategy/daily_strategy.py >> /var/log/stock-monitor/strategy.log 2>&1
        exit 0
    fi

    if [ $i -lt $MAX_RETRIES ]; then
        DELAY=${RETRY_DELAYS[$i]}
        echo "$(date '+%Y-%m-%d %H:%M:%S'): 采集失败，${DELAY}秒后重试（$((i+1))/$MAX_RETRIES）" >> /var/log/stock-monitor/crawl.log
        sleep $DELAY
    fi
done

# 4. 全部重试失败 → 飞书告警，按补采流程留给下次 cron 处理
echo "$(date '+%Y-%m-%d %H:%M:%S'): [ALERT] 采集彻底失败，已重试${MAX_RETRIES}次" >> /var/log/stock-monitor/crawl.log
python3 /opt/stock-monitor/alert/send_alert.py "数据采集失败" "当日数据经${MAX_RETRIES}次重试仍失败，将在下次 cron 自动补采。"
exit 1
```

---

## 3 部署与安全设计

### 3.1 华为云部署方案

#### 3.1.1 计算资源

| 资源 | 规格 | 用途 |
|------|------|------|
| ECS 实例 | 4C8G（通用型 s6）| FastAPI + 数据采集 + 策略计算 + Nginx |
| 系统盘 | 40GB SSD | 操作系统 + 应用 |
| 数据盘 | 100GB SSD | PostgreSQL 数据目录 + 行情数据文件 |
| 公网带宽 | 2Mbps（按需） | 飞书 API + Web 界面访问 + 数据源下载 |
| OBS 桶 | 50GB | 数据库备份 + 原始文件备份 |

#### 3.1.2 网络架构与安全组

```
华为云 VPC (10.0.0.0/16)
  ├── 子网 A (10.0.1.0/24) - 应用层
  │     └── ECS 实例
  └── 安全组规则：
        ├── 入站：允许 飞书回调 IP 段 → 443 端口
        ├── 入站：允许 用户 IP（家庭/办公）→ 443 端口（Web 界面）
        ├── 入站：允许 用户 SSH IP → 22 端口
        └── 出站：允许全部
```

飞书回调 IP 段：以飞书官方文档为准（常见 134.0.0.0/8, 103.0.0.0/8, 47.0.0.0/8）。

### 3.2 安全措施

| 层面 | 措施 | 说明 |
|------|------|------|
| 网络隔离 | VPC + 安全组白名单 | Web 界面可限制用户 IP，飞书回调单独开放 |
| 传输安全 | 全程 HTTPS | 飞书 API、Web 界面均走 TLS |
| 身份认证 | 飞书 Bot 单会话 + JWT Token（Web API） | 仅用户本人可访问 |
| 数据安全 | PostgreSQL 加密存储（生产）/ SQLite 本地文件（开发） | 生产环境连接密码强密码，开发环境 SQLite 文件权限控制 |
| 凭证管理 | 环境变量 / .env 文件 | 密钥不硬编码 |
| 访问审计 | 系统日志 + Web 访问日志 | 记录操作和 API 调用 |
| 备份 | 每日快照 + pg_dump → OBS + KMS 加密 | 数据盘每日自动快照，数据库每周全量备份 |

### 3.3 飞书集成

| 项目 | 说明 |
|------|------|
| Bot 类型 | 企业自建应用（飞书 Bot） |
| 交互模式 | **自然语言对话**：用户发消息 → FastAPI 组装动态上下文 → DeepSeek 理解意图 → Function Calling 执行 → 飞书回复 |
| 消息模式 | 主动推送（持仓提醒/买点扫描/告警）+ 交互卡片（写入确认按钮） |
| 授权范围 | 仅用户一人可见可用 |
| 回调地址 | `https://<domain>/webhook/feishu`（FastAPI 接收飞书事件） |
| 飞书 SDK | `lark-oapi`（Python），处理消息收发、卡片交互、事件订阅 |
| 推送频率 | 盘后数据更新完成后推送，晚间 21:00 后降级至次日 09:00 |

---

## 4 功能需求

### 4.1 数据管理模块

#### 4.1.1 数据源说明

系统使用 **baostock** 作为唯一数据源，不再分别对接三大交易所的文件下载接口。

| 数据类别 | 来源 | 获取方式 | 更新频率 |
|----------|------|---------|----------|
| 个股日K线（OHLCV + 换手率） | baostock `query_history_k_data_plus` | 按股票代码批量拉取 | 每交易日 17:30 后可用 |
| 后复权价格 | baostock `adjustflag='2'` 参数 | 与日K线同请求获取 | 每交易日 18:00 复权因子入库 |
| 前复权价格 | baostock `adjustflag='1'` 参数 | 与日K线同请求获取 | 同上 |
| 股票基础信息（代码/名称/上市日） | baostock `query_stock_basic` | 全量一次查询 | 每日更新（增量） |
| 基本面（PE/PB/ROE/增长率） | baostock `query_history_k_data_plus`（PE/PB）+ `query_profit_data`（ROE）+ `query_growth_data`（增长率） | 按股票代码逐一查询 | 每日更新 |
| 行业分类 | baostock `query_stock_industry` | 全量一次查询 | 按需更新 |
| 交易日历 | baostock `query_trade_dates` | 按年度查询 | 每年底更新次年 |

> **baostock 数据覆盖范围**：沪深 A 股全量（含主板、创业板、科创板），**不含北交所**（baostock 不支持北交所股票，bj./sh./sz. 前缀均返回 0 行）。不含港股/美股/期货/期权。
> 
> **baostock vs 直接下载交易所文件**：baostock 相当于对交易所数据的统一封装层，数据源仍是交易所。使用 baostock 的优势是无需对接不同的文件格式（SSE=CSV/GBK, SZSE=XLSX）和下载链接，且自带复权价格、行业分类等衍生数据。劣势是不含北交所数据，如需覆盖北交所需引入 akshare 等补充数据源。

#### 4.1.2 数据采集功能

**F-DC-001 历史数据初始化（通过 baostock 批量拉取）**

| 属性 | 说明 |
|------|------|
| 优先级 | P0 |
| 描述 | 首次部署时，通过 baostock API 批量下载近 5 年（约 2021-01-01 至今）的全市场个股日K线数据，并入库 |
| 触发 | 手动触发（部署后执行 `python crawler/history_download.py`） |
| 前置条件 | baostock SDK 可连通 |
| 输入 | 起始日期、结束日期 |
| 处理逻辑 | 1. 调用 `bs.query_stock_basic()` 获取全量股票代码列表<br>2. 对每只股票调用 `bs.query_history_k_data_plus()` 拉取全部历史日K线（支持断点续传）<br>3. 每 100 只提交一次数据库事务，同时记录进度文件 `data/download_progress.json`<br>4. 完成后更新 stock_master 表（股票代码/名称/上市日）<br>5. 可选下载基本面数据（PE/PB/ROE/增长率） |
| 输出 | 日志：采集进度（每 100 只上报）、成功/跳过/失败数量 |
| 异常处理 | 单只股票失败仅记录日志，不阻塞后续股票；断点续传：中断后重新运行自动跳过已完成的股票 |

> **注意**：全量历史下载约需 2-4 小时（5500+ 只 A 股，5 年数据）。建议首次部署时使用 `scripts/resume_kline.py` 脚本，支持断点续传、自动跳过已完成股票。当前数据库已包含 6,346 只 / 656 万行数据。

**F-DC-002 每日增量更新（✅ 生产就绪）**

| 属性 | 说明 |
|------|------|
| 优先级 | P0 |
| 实现状态 | ✅ **生产就绪**。`download_daily_update()` 已重写为生产级：A股过滤（仅 type=1，排除指数/可转债/ETF）、随机请求间隔（0.3~0.7秒）、指数退避重试（最多4次）、会话失效自动重登录、批量 INSERT（200行/批）、自动填充 stock_name、失败记录写入 failed_downloads 表、跳过当日已有数据及未上市股票。 |
| 触发 | cron 定时，每个交易日 17:35 执行 |
| 前置条件 | baostock SDK 可连通，确认当日为交易日 |
| 处理逻辑 | 1. 获取 A 股列表（`get_a_stock_codes()`，仅 type=1）<br>2. 查询已有当日数据的股票，跳过<br>3. 跳过当日尚未上市的股票（ipo_date > today）<br>4. 对剩余股票逐一下载当日K线（`_fetch_kline` 带重试）<br>5. 批量 INSERT OR IGNORE（200行/批）<br>6. 记录失败到 failed_downloads 表<br>7. 返回详细状态（rows/stocks/errors/skipped/failed_codes/elapsed_seconds） |
| 异常处理 | 单只股票：4 次指数退避重试 → 记录 failed_downloads；会话失效：自动重登录并重试；脚本级失败：daily_crawl.sh 重试 3 次（间隔 30min/30min/1h）；补采：次日 cron 自动检测缺失交易日并补采 |

**F-DC-003 交易日历管理**

| 属性 | 说明 |
|------|------|
| 优先级 | P0 |
| 实现状态 | ✅ 已实现。通过 baostock 获取交易日历，或在开发环境使用 `generate_default_calendar()` 生成简单周末排除日历。 |
| 描述 | 维护交易日历表，用于判断是否执行数据采集和策略计算 |
| 触发 | 初始化时从 CSV 导入 + 代码自动生成 |
| 处理逻辑 | 1. 从 CSV 文件导入（`trade_calendar.csv`）或代码自动生成<br>2. 按交易所（SSE/SZSE/BSE）分别标记交易日 |
| 输出 | `trade_calendar` 表 |

**F-DC-004 行业分类数据采集**

| 属性 | 说明 |
|------|------|
| 优先级 | P1 |
| 实现状态 | ✅ 已实现。通过 baostock `query_stock_industry` 全量一次性获取行业分类，写入 `stock_industry` 表。 |
| 描述 | 采集个股行业归属映射表，用于基本面分析和筛选 |
| 触发 | 随基本面数据同步采集 |

**F-DC-005 股票基础信息管理**

| 属性 | 说明 |
|------|------|
| 优先级 | P1 |
| 实现状态 | ✅ 已实现。通过 baostock `query_stock_basic` 获取全量股票基础信息，`INSERT OR REPLACE` 写入 `stock_master` 表。 |
| 描述 | 维护 stock_master 表，作为策略扫描的范围基准（排除 ST/退市） |
| 处理逻辑 | 1. 初始化时从 baostock 导入全量数据<br>2. 每日行情采集后更新（名称变更、新股上市等）<br>3. 扫描范围 = stock_master WHERE status='N' AND 有行情数据 |

**F-DC-006 基本面数据采集**

| 属性 | 说明 |
|------|------|
| 优先级 | P1 |
| 实现状态 | ✅ 已实现。通过 baostock 三个接口采集：PE/PB（`query_history_k_data_plus`）、ROE（`query_profit_data`）、增长率（`query_growth_data`）。智能季度回退，优先获取年报数据。 |
| 处理逻辑 | 1. 获取全量股票列表<br>2. 对每只股票查询 PE/PB（近 7 天最新值）<br>3. 查询 ROE 和增长率（智能回退：去年年报 → 今年 Q1）<br>4. 写入 `stock_fundamentals` 表（`INSERT OR REPLACE`） |

**F-DC-007 宕机补采**

| 属性 | 说明 |
|------|------|
| 优先级 | P0 |
| 实现状态 | ✅ **已实现**。`crawler/catch_up.py` 自动检测缺失交易日并调用 `download_daily_update()` 补采。 |
| 描述 | 当系统因宕机、网络故障等原因错过某交易日的采集时，次日 cron 自动检测并补采缺失数据。 |
| 触发 | 每次 cron 采集前自动执行（`daily_crawl.sh` 步骤2） |
| 处理逻辑 | 1. 查询 `daily_quote` 中 MAX(trade_date)<br>2. 从该日期+1 遍历到最近交易日<br>3. 用交易日历判断哪些是缺失的交易日<br>4. 对每个缺失日调用 `download_daily_update()`<br>5. 单日最多重试 3 次（致命错误等待 30-60 秒）<br>6. 上限 max_days（默认 10 天），超出告警并仅补采最近 N 天 |
| 异常处理 | 单日失败记录到日志，不阻塞后续日期；全部失败由 shell 层重试接管；超出上限发飞书告警 |

#### 4.1.3 统一数据格式

**daily_quote 表（个股日行情）**

| 字段名 | 类型 | 说明 |
|--------|------|------|
| trade_date | DATE | 交易日期 |
| exchange | VARCHAR(4) | 交易所代码（SSE/SZSE/BSE） |
| stock_code | VARCHAR(6) | 证券代码 |
| stock_name | VARCHAR(20) | 证券简称 |
| open | NUMERIC(10,2) | 开盘价（交易所原始值） |
| high | NUMERIC(10,2) | 最高价（交易所原始值） |
| low | NUMERIC(10,2) | 最低价（交易所原始值） |
| close | NUMERIC(10,2) | 收盘价（交易所原始值） |
| close_hfq | NUMERIC(10,3) | 后复权收盘价（策略计算主输入，物化存储） |
| close_qfq | NUMERIC(10,3) | 前复权收盘价（Web K 线展示、持仓盈亏参考） |
| adj_factor_hfq | NUMERIC(10,6) DEFAULT 1.000000 | 后复权因子 |
| adj_factor_qfq | NUMERIC(10,6) DEFAULT 1.000000 | 前复权因子 |
| is_ex_date | BOOLEAN DEFAULT false | 当日是否为除权除息日 |
| ex_date_confirmed | BOOLEAN DEFAULT false | 除权事件是否已人工确认 |
| volume | BIGINT | 成交量（股） |
| amount | NUMERIC(18,2) | 成交金额（元） |
| turnover | NUMERIC(8,4) | 换手率（%） |
| created_at | TIMESTAMP | 入库时间 |

> **复权处理**：采用后复权方式，复权价格物化存储在 daily_quote 中（空间换时间，避免策略计算时动态 JOIN）。所有策略引擎统一以 `close_hfq` 作为输入。复权因子的推导依据 `corporate_actions` 表（见 4.1.7），采用三层获取机制：收盘价跳空自动检测 → 交易所公告定期扫描 → akshare 兜底补充。交易所原始数据始终保留，确保可追溯。

**index_daily_quote 表（指数日行情）**

| 字段名 | 类型 | 说明 |
|--------|------|------|
| trade_date | DATE | 交易日期 |
| index_code | VARCHAR(8) | 指数代码 |
| index_name | VARCHAR(20) | 指数名称 |
| open | NUMERIC(10,2) | 开盘价 |
| high | NUMERIC(10,2) | 最高价 |
| low | NUMERIC(10,2) | 最低价 |
| close | NUMERIC(10,2) | 收盘价 |
| volume | BIGINT | 成交量 |
| amount | NUMERIC(18,2) | 成交金额 |

**stock_industry 表（行业归属）**

| 字段名 | 类型 | 说明 |
|--------|------|------|
| stock_code | VARCHAR(6) | 证券代码 |
| exchange | VARCHAR(4) | 交易所 |
| industry_sw | VARCHAR(20) | 申万行业分类（如有） |
| industry_exchange | VARCHAR(20) | 交易所行业分类 |
| updated_at | DATE | 更新日期 |

**trade_calendar 表（交易日历）**

| 字段名 | 类型 | 说明 |
|--------|------|------|
| cal_date | DATE | 日期 |
| is_trade_day | BOOLEAN | 是否交易日 |
| exchange | VARCHAR(4) | 交易所 |

**stock_master 表（股票基础信息）**

| 字段名 | 类型 | 说明 |
|--------|------|------|
| stock_code | VARCHAR(6) | 证券代码（主键） |
| stock_name | VARCHAR(20) | 证券简称 |
| exchange | VARCHAR(4) | 交易所代码（SSE/SZSE/BSE） |
| ipo_date | DATE | 上市日期 |
| status | VARCHAR(10) DEFAULT 'N' | 股票状态：N=正常, S=ST, X=退市, P=暂停上市 |
| status_date | DATE | 状态变更日期 |
| updated_at | TIMESTAMP | 更新时间 |

> **数据来源**：三大交易所"股票列表"文件（通常每季度更新），采集时同步更新。用于全市场买点扫描的范围过滤（排除 ST/退市/停牌）和个股基础信息查询。
> **与 daily_quote 的关系**：stock_master 为股票维度主表，daily_quote 为日行情事实表。扫描范围 = stock_master WHERE status='N' AND 当日 daily_quote 中存在记录（存在即非停牌）。

**signal_history 表（策略信号记录）**

| 字段名 | 类型 | 说明 |
|--------|------|------|
| id | SERIAL | 主键 |
| signal_date | DATE | 信号日期 |
| stock_code | VARCHAR(6) | 证券代码 |
| stock_name | VARCHAR(20) | 证券简称 |
| direction | VARCHAR(6) | 方向：buy / sell / neutral |
| strength | SMALLINT | 强度：1-3 |
| strategy_name | VARCHAR(30) | 来源策略名 |
| reason | TEXT | 信号原因描述 |
| price | NUMERIC(10,2) | 信号触发时价格（后复权价 close_hfq） |
| preference | VARCHAR(10) DEFAULT 'balanced' | 信号产生时的全局偏好 mode |
| suggested_action | VARCHAR(20) | 建议操作 |
| combined_signal | BOOLEAN | 是否为融合信号（默认 false） |
| source_strategies | TEXT | 融合信号的来源策略列表（JSON） |
| is_pushed | BOOLEAN | 是否已推送 |
| pushed_at | TIMESTAMP | 推送时间 |
| created_at | TIMESTAMP | 创建时间 |

**failed_downloads 表（下载失败记录）**

| 字段名 | 类型 | 说明 |
|--------|------|------|
| id | SERIAL | 主键 |
| exchange | VARCHAR(4) | 交易所 |
| trade_date | DATE | 目标日期 |
| data_type | VARCHAR(20) | 数据类型（quote/calendar/index） |
| error_msg | TEXT | 错误信息 |
| retry_count | SMALLINT | 已重试次数 |
| status | VARCHAR(10) | pending / resolved / manual |
| created_at | TIMESTAMP | 创建时间 |
| resolved_at | TIMESTAMP | 解决时间 |

**corporate_actions 表（除权除息事件）**

| 字段名 | 类型 | 说明 |
|--------|------|------|
| id | SERIAL | 主键 |
| stock_code | VARCHAR(6) | 证券代码 |
| stock_name | VARCHAR(20) | 证券简称 |
| ex_date | DATE | 除权除息日 |
| cash_div | NUMERIC(10,4) DEFAULT 0 | 每股现金分红（税前，元） |
| bonus_ratio | NUMERIC(10,4) DEFAULT 0 | 送股比例（0.5=10送5） |
| transfer_ratio | NUMERIC(10,4) DEFAULT 0 | 转增比例（0.5=10转5） |
| rights_ratio | NUMERIC(10,4) DEFAULT 0 | 配股比例（0.3=10配3） |
| rights_price | NUMERIC(10,2) DEFAULT 0 | 配股价（元） |
| source | VARCHAR(20) DEFAULT 'detect' | 来源：detect(跳空检测)/manual(手动确认)/akshare(补充)/exchange(公告) |
| confirmed | BOOLEAN DEFAULT false | 是否已人工确认 |
| confirmed_at | TIMESTAMP | 确认时间 |
| created_at | TIMESTAMP | 创建时间 |

> **唯一约束**：`UNIQUE(stock_code, ex_date)`，一只股票同一天只有一条除权记录。
> 此表为复权因子的唯一事实来源，所有 adj_factor 推导均从此表计算得出。详见 4.1.7。

**strategy_param_log 表（策略参数变更记录）**

| 字段名 | 类型 | 说明 |
|--------|------|------|
| id | SERIAL | 主键 |
| strategy_name | VARCHAR(30) | 策略名称 |
| param_key | VARCHAR(30) | 参数名 |
| old_value | TEXT | 旧值 |
| new_value | TEXT | 新值 |
| changed_by | VARCHAR(20) | 变更来源（feishu/manual） |
| created_at | TIMESTAMP | 变更时间 |

**portfolio_history 表（持仓操作历史）**

| 字段名 | 类型 | 说明 |
|--------|------|------|
| id | SERIAL | 主键 |
| stock_code | VARCHAR(6) | 证券代码 |
| stock_name | VARCHAR(20) | 证券简称 |
| action | VARCHAR(10) | 操作类型：add/reduce/clear/modify |
| quantity_before | INTEGER | 操作前数量 |
| quantity_after | INTEGER | 操作后数量 |
| cost_before | NUMERIC(10,3) | 操作前成本 |
| cost_after | NUMERIC(10,3) | 操作后成本 |
| created_at | TIMESTAMP | 操作时间 |

#### 4.1.4 数据库索引策略

5 年全量数据约 600 万+ 条 daily_quote 记录，无索引时全表扫描策略计算将不可接受。关键索引如下：

```sql
-- daily_quote 核心索引
CREATE UNIQUE INDEX idx_dq_unique ON daily_quote (stock_code, exchange, trade_date);
CREATE INDEX idx_dq_date ON daily_quote (trade_date);
CREATE INDEX idx_dq_stock_date ON daily_quote (stock_code, trade_date DESC);
CREATE INDEX idx_dq_ex_date ON daily_quote (is_ex_date, ex_date_confirmed);

-- signal_history 查询索引
CREATE INDEX idx_sh_date ON signal_history (signal_date);
CREATE INDEX idx_sh_stock ON signal_history (stock_code, signal_date DESC);
CREATE INDEX idx_sh_direction ON signal_history (direction, signal_date);

-- trade_calendar 查询索引
CREATE INDEX idx_tc_date ON trade_calendar (cal_date, is_trade_day);

-- failed_downloads 处理索引
CREATE INDEX idx_fd_status ON failed_downloads (status, exchange);

-- portfolio 索引
CREATE UNIQUE INDEX idx_portfolio_active_stock ON portfolio (stock_code) WHERE is_active = true;
CREATE INDEX idx_portfolio_active ON portfolio (is_active, updated_at DESC);

-- portfolio_history 查询索引
CREATE INDEX idx_ph_stock ON portfolio_history (stock_code, created_at DESC);

-- corporate_actions 查询索引
CREATE INDEX idx_ca_ex_date ON corporate_actions (ex_date);
CREATE INDEX idx_ca_confirmed ON corporate_actions (confirmed, ex_date);

-- strategy_config 约束
ALTER TABLE strategy_config ADD CONSTRAINT chk_params_is_object CHECK (jsonb_typeof(params) = 'object');

-- stock_master 查询索引
CREATE INDEX idx_sm_status ON stock_master (status, exchange);

-- system_metrics 查询索引
CREATE INDEX idx_sm_name_time ON system_metrics (metric_name, checked_at DESC);
CREATE INDEX idx_sm_status ON system_metrics (status, checked_at DESC);
```

> **维护策略**：PostgreSQL 自动 ANALYZE + 每月手动 REINDEX（低峰期执行），保持查询计划准确性。

#### 4.1.5 数据质量保障

| 检查项 | 规则 | 处理 |
|--------|------|------|
| 完整性 | 每日行情记录数与交易所公布股票数对比 | 偏差 >5% 告警 |
| 准确性 | OHLC 关系校验：open/close ≤ high 且 ≥ low | 异常记录标记 review |
| 连续性 | 交易日序列无断档 | 断档自动补采 |
| 时效性 | 最新数据日期与交易日历对比 | 滞后 1 日以上告警 |

#### 4.1.6 复权机制详述

> **⚠️ 实现状态**：本节所述复权机制为**计划但尚未实现**的功能。当前代码中 `close_hfq = close`（原始收盘价）、`adj_factor_hfq = 1.0`（占位值），所有策略实际使用**不复权价格**进行信号计算。A 股约 60% 个股每年至少分红一次，不复权可能导致除权日前后产生虚假信号。此缺陷应在后续迭代中优先修复。

复权处理是技术分析数据质量的关键基础。不复权的行情数据在除权除息日会产生人为跳空缺口，导致布林线、MACD、量价背离等技术指标产生虚假信号（详见下方影响分析）。本系统采用**后复权 + 物化存储**方案。

##### 4.1.6.1 复权方式选择

| 复权类型 | 原理 | 使用场景 |
|----------|------|---------|
| **后复权 (hfq)** | 最新价保持真实，历史价格等比放大 | **策略计算主输入**（布林线/MACD/均线等不会因除权断裂） |
| **前复权 (qfq)** | 历史价保持真实，最新价等比缩小 | **Web K 线展示**（视觉更自然）、持仓盈亏参考 |

`daily_quote` 中 `close` 为交易所原始收盘价（不变），`close_hfq` 和 `close_qfq` 为物化复权价。策略引擎统一以 `close_hfq` 作为输入。

##### 4.1.6.2 不复权对策略的影响

| 策略 | 不复权时的错误信号（以除权除息日为例） |
|------|---------------------------------------|
| 布林线 | 股价从 10.00 "跳空下跌"到 9.55，可能跌破下轨 → **虚假买入信号** |
| 量价背离 | 价格"创近期新低"但成交量正常 → **虚假底背离信号** |
| 周趋势 | 5 日均线出现 **虚假下弯**，可能触发假死叉 |
| MACD | DIF 线出现 **虚假加速下行** |

A 股市场中约 60% 个股每年至少分红一次，不复权的策略信号对这类股票基本不可用。

##### 4.1.6.3 除权除息事件获取机制（三层递进）

```
优先级从高到低：

┌─ 第一层：收盘价跳空自动检测 ────────────────────────────────┐
│ 每日数据入库后，对比当日 close 与 prev_close                  │
│ 跳空幅度 > 8% → INSERT corporate_actions (confirmed=false)   │
│ → 飞书通知 "检测到 300274 阳光电源疑似除权除息，请确认"       │
│ → 用户回复确认即可填充分红/送转明细                           │
│ → 系统重算该股 adj_factor 并更新 close_hfq                   │
└─────────────────────────────────────────────────────────────┘
         │ (用户忽略 / 长期未确认)
         ▼
┌─ 第二层：交易所公告定期扫描 ────────────────────────────────┐
│ 每周末扫描上交所/深交所 "分红送转公告" 页面                   │
│ 匹配本周除权除息的股票 → 自动填充 corporate_actions          │
│ source='exchange', confirmed=true（公告为权威来源）           │
└─────────────────────────────────────────────────────────────┘
         │ (公告爬取失败 / 格式变更)
         ▼
┌─ 第三层：akshare 兜底补充 ───────────────────────────────────┐
│ 用户主动触发 或 每周日定时执行                                │
│ akshare 拉取单只股票除权除息历史 → diff 对比                  │
│ 本地缺失的事件 → INSERT corporate_actions                    │
│ source='akshare', confirmed=false → 仍需用户确认              │
└─────────────────────────────────────────────────────────────┘
```

> **akshare 的角色**：只做补充，不做主线。即使 akshare 完全不可用，系统仍可通过第一层跳空检测 + 第二层交易所公告维持运转。

##### 4.1.6.4 后复权因子计算公式

从 `corporate_actions` 的事件明细推导后复权因子：

```
除权日除权参考价：
  close_adjusted = (prev_close - cash_div + rights_price × rights_ratio)
                 / (1 + bonus_ratio + transfer_ratio + rights_ratio)

后复权因子（除权日之前的所有交易日）：
  adj_factor_hfq_before = adj_factor_hfq_after × (prev_close / close_adjusted)

最新交易日 adj_factor_hfq = 1.000000（恒等式）
```

**计算流程**（每只股票独立计算）：

```python
def compute_all_factors(stock_code: str):
    hfq_factor = 1.0  # 最新交易日 = 1
    actions = query_corporate_actions(stock_code)  # 按 ex_date 升序
    quotes = query_daily_quotes(stock_code)        # 按 trade_date 升序

    # 从最新日期向前遍历
    action_idx = len(actions) - 1  # 从最新的除权事件开始匹配
    for i in range(len(quotes) - 1, -1, -1):
        quote = quotes[i]

        # 检查当日是否为除权日
        if action_idx >= 0 and quote.trade_date == actions[action_idx].ex_date:
            action = actions[action_idx]
            # prev_close = 除权日前一交易日的原始收盘价
            # 注意：最新交易日(i=len-1)不会命中此分支，因为除权日不可能晚于最新数据日
            prev_quote = quotes[i - 1] if i > 0 else quote
            close_adjusted = calc_adjusted_close(
                prev_quote.close, action.cash_div,
                action.bonus_ratio, action.transfer_ratio,
                action.rights_ratio, action.rights_price
            )
            hfq_factor = hfq_factor * (prev_quote.close / close_adjusted)
            quote.is_ex_date = True
            action_idx -= 1

        # 写入后复权价格和因子
        quote.adj_factor_hfq = hfq_factor
        quote.close_hfq = round(quote.close * hfq_factor, 3)

    # 前复权因子 = 后复权因子 / 最新后复权因子
    latest_hfq = quotes[-1].adj_factor_hfq if quotes else 1.0
    for quote in quotes:
        quote.adj_factor_qfq = quote.adj_factor_hfq / latest_hfq
        quote.close_qfq = round(quote.close * quote.adj_factor_qfq, 3)
```

**示例**：某股 5/30 收盘价 10.00，5/31 每10股派 5 元除权（每股 0.50），5/31 实际收盘 9.55：

| 交易日 | close(原始) | adj_factor_hfq | close_hfq |
|--------|-------------|----------------|-----------|
| 5/31 | 9.55 | 1.000000 | 9.55 |
| 5/30 | 10.00 | 1.052632 (= 1.0 × 10.00/9.50) | 10.53 |

##### 4.1.6.5 初始化与日常流程

**历史数据初始化时**（前置条件：trade_calendar 已导入）：
1. 根据交易日历下载 5 年原始日行情 → `daily_quote` (close/open/high/low 为原始值，只下载交易日)
2. 对每只股票，尝试通过 akshare 获取历史除权除息列表 → `corporate_actions`（akshare 不可用时跳过，后续逐步补）
3. 执行 `compute_all_factors()` → 填充 `close_hfq` / `close_qfq`
4. 对 akshare 未覆盖的股票，用跳空检测补标 → 飞书提交用户确认清单

**每日增量更新时**：
1. 下载当日行情 CSV → 写入 `daily_quote`（close 为原始值）
2. 检查 `close` vs `prev_close` 跳空 → 若 >8% 且未匹配已有 `corporate_actions`，则 INSERT（confirmed=false，飞书通知）
3. 对当日有除权事件的股票 → 重新计算该股**全部历史** adj_factor 及 `close_hfq` / `close_qfq`
4. 其余股票 → `close_hfq = close`，adj_factor 不变
5. 周末定时 → 扫描交易所公告 + akshare 补充

##### 4.1.6.6 异常处理

| 场景 | 处理 |
|------|------|
| 公告爬取失败 | 跳空检测仍可捕获除权日（标记 confirmed=false），不影响当日计算 |
| akshare 不可用 | 不影响日常流程，仅影响历史数据回补效率 |
| 用户长期未确认跳空事件 | 每周提醒一次；未确认的除权事件 `adj_factor` 仍按自动检测值计算（可能有偏差但不阻塞） |
| 配股事件（A 股少见） | 按同样公式处理，rights_price > 0 时纳入计算 |

#### 4.1.7 数据采集容错与监控

> **当前状态**：以下部分功能已实现，部分仍为计划：
> - ✅ baostock 连接管理（login/logout）+ 单只股票异常跳过
> - ✅ 断点续传（进度文件 `data/download_progress.json` + 数据库去重）
> - ✅ 每 100 只提交事务 + 记录进度
> - ⚠️ `catch_up.py` 补采逻辑尚为 **TODO 占位**，未真正实现
> - ❌ `failed_downloads` 表已建但无代码写入
> - ❌ 飞书告警未实现（无主动推送能力）

| 场景 | 处理策略 |
|------|---------|
| 单只股票下载失败 | 跳过该股票，记录日志不阻塞后续，最多记录前 5 次失败 |
| baostock 连接超时/断开 | 自动重连（重新 login），每批次失败后 sleep 0.5s 避免过载 |
| baostock 限速 | 25 只/分钟，全量 5500 只需 3.5 小时，属已知瓶颈（计划优化） |
| 数据去重 | 使用 `INSERT OR IGNORE` 防止重复行（SQLite/PostgreSQL 均支持） |
| 服务器宕机 / cron 错过 | 下次 cron 触发时，`catch_up.py` 自动检测缺失交易日（**当前为 TODO**） |
| 断点续传 | 进度文件 + DB 中已有股票双重判定，中断后可续传 |

---

### 4.2 策略引擎模块

#### 4.2.1 策略框架设计（3 + 1 结构）

![数据流程图](./diagrams/data_pipeline_flow.svg)

**整体架构**：系统采用 **3 个信号策略 + 1 个全局偏好修饰器** 的结构。全局偏好（左右侧交易风格）不独立产生信号，而是运行时调整 3 个信号策略的参数阈值。

```
                    ┌───────────────────────┐
                    │  全局偏好 (元策略修饰器) │
                    │  left / right /        │
                    │  balanced              │
                    └───┬───┬───┬───────────┘
                        │   │   │
               ┌────────┘   │   └────────┐
               ▼            ▼            ▼
          ┌─────────┐ ┌─────────┐ ┌─────────┐
          │ 日布林线  │ │ 量价背离  │ │ 周趋势   │  ← 3 个独立信号策略
          │ F-ST-001 │ │ F-ST-002 │ │ F-ST-003 │
          └────┬─────┘ └────┬─────┘ └────┬─────┘
               │            │            │
               └────────────┼────────────┘
                            ▼
                   ┌────────────────┐
                   │ Signal Combiner│  ← F-ST-004 融合 3 路独立信号
                   └────────────────┘
```

**为什么不是 4 个平级策略？** 原设计中"左右侧偏好"的信号条件（布林线上下轨突破、RSI 超买超卖、均线金叉死叉）与布林线策略、周趋势策略高度重叠。当一次布林线下轨反弹同时触发左右侧和布林线两个信号时，Signal Combiner 会误判为"两个策略独立确认"，导致信号强度系统性虚高。将其改为元策略修饰器后，消除了信号重叠，3 路信号投票更诚实。

策略执行流程：

1. 每日行情数据入库后，触发策略计算
2. 读取全局偏好 mode（left / right / balanced）
3. 根据 mode 动态调整 3 个信号策略的运行时参数
4. 逐策略计算，输出原始信号（每个策略独立，信号条件不重叠）
5. Signal Combiner 融合 3 路信号，生成综合买卖建议
6. 将信号写入 signal_history 表
7. 根据推送规则生成飞书消息

#### 4.2.2 全局偏好配置（元策略修饰器）

**F-ST-005 全局交易偏好配置**

| 属性 | 说明 |
|------|------|
| 优先级 | P0 |
| 描述 | 配置左侧/右侧/均衡交易偏好，不独立生成信号，而是运行时动态调整 3 个信号策略的参数阈值 |
| 功能编号 | F-ST-005（F-ST-001~003 为信号策略，F-ST-004 为信号融合） |
| 配置项 | `global_preference.mode`: "left" / "right" / "balanced" |

**设计原则**：全局偏好描述的是"用什么标准去判断信号"，而非"发现了什么信号"。左侧 = 宁可早一点，接受更多假信号；右侧 = 宁可晚一点，只要确定性高的信号。

**运行时参数调整表**：

| 参数（归属策略） | 左侧 (left) | 均衡 (balanced) | 右侧 (right) |
|-------------------|------------|-----------------|-------------|
| 布林线 `std_mult` | **-0.3**（提前接触上下轨） | 不变 | **+0.3**（需要更极端的价格） |
| 布林线 `bandwidth_threshold` | **+0.02**（更易判定缩口） | 不变 | 不变 |
| RSI oversold（布林线引用） | **35**（提前判定超卖） | 30 | **25**（深度超卖才触发） |
| RSI overbought（布林线引用） | **65**（提前判定超买） | 70 | **75**（更高容忍度） |
| 量价背离 `lookback` 最小间隔 | **缩短**（日线 3 天） | 日线 5 天 | **延长**（日线 7 天） |
| 量价背离 量比阈值 | **降低 0.05**（更容易判定背离） | 不变 | **提高 0.05**（更严格要求） |
| 周趋势 MACD 金叉/死叉确认 | 金叉当日即触发 | 金叉当日 | **金叉后等 1 周确认** |
| 周趋势 成交量确认 | 不要求放量 | 要求放量 | **要求显著放量（>1.5 倍 5 周均量）** |
| 止损参考（周趋势用） | 1.5 倍 ATR | 1 倍 ATR | 0.75 倍 ATR |

**策略计算引擎实现**：

```python
class StrategyEngine:
    def __init__(self):
        self.preference = load_global_preference()  # 'left' / 'right' / 'balanced'
        self.strategies = [
            BollingerDaily(),
            VolumePriceDivergence(),
            WeeklyTrend(),
        ]

    def run_all(self, df: pd.DataFrame) -> List[Signal]:
        signals = []
        for strategy in self.strategies:
            # 根据全局偏好调整该策略的运行时参数
            adjusted_params = self.apply_preference(
                strategy.default_params(),
                self.preference
            )
            signals.extend(strategy.calculate(df, adjusted_params))
        return signals

    def apply_preference(self, base_params: dict, pref: str) -> dict:
        """将全局偏好映射为各策略参数的具体偏移量"""
        adjustments = PREFERENCE_ADJUSTMENTS[pref]  # 上表映射为配置字典
        return merge_params(base_params, adjustments)
```

**飞书交互**：全局偏好通过自然语言切换，DeepSeek 调用 `confirm_set_preference`：
- 用户："我想用左侧交易" / "切换成右侧偏好" / "恢复均衡模式"
- 系统推送确认卡片 → 用户确认后回复示例：已切换为**左侧交易**偏好，各策略参数已自动调整：布林线 std_mult 2.0→1.7、RSI 超卖 30→35、量价背离最小间隔 5→3 日...

#### 4.2.3 策略一：日布林线

**F-ST-001 日布林线买卖点**

| 属性 | 说明 |
|------|------|
| 优先级 | P0 |
| 描述 | 基于日 K 线布林线通道判断超买超卖区域，生成买卖信号 |
| 默认参数 | N=20, K=2.0 |

**参数配置**：

| 参数 | 默认值 | 范围 | 说明 |
|------|--------|------|------|
| period | 20 | 10-60 | 布林线中轨周期（日） |
| std_mult | 2.0 | 1.5-3.0 | 标准差倍数 |
| bandwidth_threshold | 0.04 | 0.02-0.10 | 带宽收窄阈值，低于此值视为缩口，预示变盘 |

**信号逻辑**：

| 信号 | 触发条件 | 强度 |
|------|---------|------|
| 买入（强） | 收盘价从下方突破下轨后，次日收盘回到通道内 | ★★★ |
| 买入（中） | 收盘价触及下轨 + 布林带缩口后开口 + 成交量放大 | ★★ |
| 买入（弱） | 收盘价在通道下半区且 RSI 超卖 | ★ |
| 卖出（强） | 收盘价从上方突破上轨后，次日收盘回到通道内 | ★★★ |
| 卖出（中） | 收盘价触及上轨 + 布林带缩口后开口 + 成交量放大 | ★★ |
| 卖出（弱） | 收盘价在通道上半区且 RSI 超买 | ★ |

**附加规则**：
- 布林带缩口期（bandwidth < threshold）标记为"变盘预警"，不直接生成买卖信号，但提醒关注
- 信号生成时需排除上市不满 period 个交易日的次新股

#### 4.2.4 策略二：量价背离

**F-ST-002 日/周/月量价背离**

| 属性 | 说明 |
|------|------|
| 优先级 | P0 |
| 描述 | 检测价格与成交量走势背离的技术形态，判断趋势可靠性 |
| 周期 | 日线、周线、月线三个级别 |

**背离类型定义**：

| 背离类型 | 定义 | 信号方向 |
|----------|------|----------|
| 顶背离 | 价格创新高，但成交量未能同步创新高 | 看跌（卖出信号） |
| 底背离 | 价格创新低，但成交量未能同步创新低 | 看涨（买入信号） |
| 量增价滞 | 成交量持续放大，但价格涨幅收窄 | 看跌（卖出信号） |
| 量缩价稳 | 成交量持续萎缩，但价格跌幅收窄 | 看涨（买入信号） |

**检测逻辑（以日线顶背离为例）**：

```
1. 识别价格最近 N 日高点（N=20），记为 price_high
2. 向前寻找前一个高点 price_high_prev（需间隔 ≥5 日）
3. 判断：price_high > price_high_prev AND volume_at_high < volume_at_high_prev
4. 若成立 → 顶背离信号
```

**各周期参数**：

| 参数 | 日线 | 周线 | 月线 |
|------|------|------|------|
| 回溯窗口 | 20 日 | 24 周 | 12 月 |
| 最小间隔 | 5 日 | 3 周 | 2 月 |
| 量比阈值 | 0.8 | 0.85 | 0.9 |

**信号权重**：月线背离 > 周线背离 > 日线背离。多周期共振（如同为底背离）时信号强度加倍。

#### 4.2.5 策略三：周趋势

**F-ST-003 周趋势买卖点**

| 属性 | 说明 |
|------|------|
| 优先级 | P0 |
| 描述 | 基于周 K 线趋势判断中线买卖时机 |
| 数据来源 | 由日行情数据聚合为周线（每周一至周五，遇节假日顺延） |

**周线聚合规则**：
- 周开盘 = 周内首个交易日开盘价
- 周最高 = 周内所有交易日最高价的最大值
- 周最低 = 周内所有交易日最低价的最小值
- 周收盘 = 周内最后一个交易日收盘价
- 周成交量 = 周内所有交易日成交量之和

**趋势判断方法**：

| 方法 | 说明 |
|------|------|
| 均线趋势 | 5 周均线上穿 20 周均线 → 多头趋势；下穿 → 空头趋势 |
| 趋势线 | 连接最近 2 个周线低点画上升趋势线，跌破 → 趋势破坏 |
| MACD | 周线 MACD 金叉/死叉，配合零轴上下判断强弱 |

**信号逻辑**：

| 信号 | 触发条件 | 强度 |
|------|---------|------|
| 买入（强） | 5 周均线上穿 20 周均线 + MACD 金叉在零轴上方 | ★★★ |
| 买入（中） | 5 周均线上穿 20 周均线 + MACD 金叉在零轴下方 | ★★ |
| 买入（弱） | 仅 5 周均线上穿 20 周均线 | ★ |
| 卖出（强） | 5 周均线下穿 20 周均线 + MACD 死叉在零轴下方 | ★★★ |
| 卖出（中） | 5 周均线下穿 20 周均线 + MACD 死叉在零轴上方 | ★★ |
| 卖出（弱） | 仅 5 周均线下穿 20 周均线 | ★ |

**止损/止盈建议**：
- 买入后，止损位 = 买入周最低价 - 1 倍 ATR(14)
- 买入后，止盈参考 = 2 倍 ATR(14) 或布林线上轨

#### 4.2.6 策略配置管理

**策略配置交互**

| 属性 | 说明 |
|------|------|
| 优先级 | P0 |
| 描述 | 用户通过飞书交互，勾选/取消信号策略（布林线/量价背离/周趋势），调整策略参数。全局偏好通过独立的"设置偏好"命令调整 |
| 交互方式 | 飞书消息卡片 + 按钮回调 |

**策略配置结构**：

```json
{
  "global_preference": {
    "mode": "balanced"
  },
  "strategies": {
    "bollinger_daily": {
      "enabled": true,
      "params": { "period": 20, "std_mult": 2.0, "bandwidth_threshold": 0.04 }
    },
    "volume_price_divergence": {
      "enabled": true,
      "params": {
        "levels": ["daily", "weekly", "monthly"],
        "lookback_daily": 20,
        "lookback_weekly": 24,
        "lookback_monthly": 12
      }
    },
    "weekly_trend": {
      "enabled": true,
      "params": { "fast_period": 5, "slow_period": 20 }
    }
  }
}
```

**策略配置数据库表 (strategy_config) DDL**：

```sql
CREATE TABLE strategy_config (
    id            SERIAL PRIMARY KEY,
    strategy_name VARCHAR(30) NOT NULL UNIQUE,
    display_name  VARCHAR(30) DEFAULT '',       -- 飞书/Web 展示用中文名
    enabled       BOOLEAN     NOT NULL DEFAULT true,
    params        JSONB       NOT NULL DEFAULT '{}',
    updated_at    TIMESTAMP   NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_by    VARCHAR(20) DEFAULT 'manual'  -- feishu / manual / system
);

COMMENT ON TABLE strategy_config IS '策略配置：一行一策略，enabled+params 控制运行时行为';

-- 确保 params 是合法 JSON 对象
ALTER TABLE strategy_config
    ADD CONSTRAINT chk_params_is_object CHECK (jsonb_typeof(params) = 'object');

-- 默认数据
INSERT INTO strategy_config (strategy_name, display_name, enabled, params) VALUES
('global_preference',       '全局偏好',   true,  '{"mode": "balanced"}'),
('bollinger_daily',         '日布林线',   true,  '{"period": 20, "std_mult": 2.0, "bandwidth_threshold": 0.04}'),
('volume_price_divergence', '量价背离',   true,  '{"levels": ["daily","weekly","monthly"], "lookback_daily": 20, "lookback_weekly": 24, "lookback_monthly": 12}'),
('weekly_trend',            '周趋势',     true,  '{"fast_period": 5, "slow_period": 20}');

-- 应用层约束：global_preference 永远不允许 disabled，也不允许删除该行。
-- 飞书"关闭策略"指令仅对 3 个信号策略生效，全局偏好通过"设置偏好"独立管理。
```

> **应用层校验**：策略类通过 `StrategyBase.params_schema` 返回 JSON Schema，参数修改前由策略自身校验类型和范围（见 11.1）。

**策略参数动态调整**：
- 用户通过自然语言调整参数，例如："把布林线的周期改成15，标准差改成2.5"
- DeepSeek 识别意图 → 调用 `confirm_update_strategy_param` → 飞书推送确认卡片
- 用户确认后：系统读取当前 `strategy_config.params` → 调用对应策略的 `params_schema` 校验新值 → 校验通过则 UPDATE params + INSERT strategy_param_log；校验失败则飞书回复具体错误原因
- 系统回复：已修改布林线策略参数：period 从 20 改为 15，std 从 2.0 改为 2.5。将在下次计算生效
- 参数变更记录到 strategy_param_log，支持回滚到上一次有效参数

#### 4.2.7 多策略信号融合

**F-ST-004 信号融合**

| 属性 | 说明 |
|------|------|
| 优先级 | P0 |
| 描述 | 将 3 个信号策略的原始信号融合为综合买卖建议 |

**融合规则**：

| 场景 | 规则 |
|------|------|
| 3 策略同向 | 信号强度叠加 → 强信号 ★★★（三策略共振，确定性最高） |
| 2 策略同向 | 信号强度中等 → ★★ |
| 单策略信号 | 正常推送，强度 ★，标注来源策略名 |
| 策略信号矛盾 | 买入和卖出同时出现 → 标记"多空分歧"，不推送操作建议 |
| 信号方向+仓位结合 | 持仓股卖出信号 → 加仓/减仓/清仓建议；非持仓股买入信号 → 关注/建仓建议 |

**信号输出格式**：

```json
{
  "stock_code": "300274",
  "stock_name": "阳光电源",
  "signal_date": "2026-05-30",
  "direction": "buy",
  "strength": 3,
  "preference": "balanced",
  "strategies": ["bollinger_daily", "volume_price_divergence_daily", "weekly_trend"],
  "price": 78.50,
  "suggested_action": "关注",
  "reason": "布林线下轨反弹+日线底背离+周线金叉（三策略共振）",
  "risk_note": "仅供理论学习和理论练习使用，不构成投资建议"
}
```

---

### 4.3 持仓管理模块

#### 4.3.1 持仓录入

**F-PF-001 持仓录入**

| 属性 | 说明 |
|------|------|
| 优先级 | P0 |
| 描述 | 用户录入持有的个股、仓位数量和成本价 |
| 交互方式 | 飞书自然语言 + 确认卡片：用户说"我建仓了阳光电源300274，成本78.5，买了1000股" → 系统确认后写入 |

**持仓表 (portfolio) DDL**：

```sql
CREATE TABLE portfolio (
    id          SERIAL PRIMARY KEY,
    stock_code  VARCHAR(6)   NOT NULL,
    stock_name  VARCHAR(20)  NOT NULL,
    exchange    VARCHAR(4)   NOT NULL,
    quantity    INTEGER      NOT NULL CHECK (quantity >= 0),
    cost_price  NUMERIC(10,3) NOT NULL DEFAULT 0,
    created_at  TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at  TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    is_active   BOOLEAN      NOT NULL DEFAULT true,
    notes       VARCHAR(200) DEFAULT ''
);

-- 核心约束：同一股票只能有一条活跃持仓
CREATE UNIQUE INDEX idx_portfolio_active_stock
    ON portfolio (stock_code) WHERE is_active = true;

-- 活跃持仓快速查询
CREATE INDEX idx_portfolio_active
    ON portfolio (is_active, updated_at DESC);
```

> **与 portfolio_history 的关系**：每次 INSERT / UPDATE / DELETE（清仓）portfolio 时，在同一事务中写入 portfolio_history 记录变更前后快照。portfolio_history 为仅追加日志表，不直接修改。

**录入校验**：
- 股票代码需在 daily_quote 中存在
- 成本价 > 0
- 数量 > 0 且为 100 的整数倍（A股最小交易单位）

#### 4.3.2 持仓修改

**F-PF-002 持仓修改**

| 属性 | 说明 |
|------|------|
| 优先级 | P0 |
| 描述 | 修改已有持仓的数量和/或成本价 |
| 交互方式 | 飞书自然语言 + 确认卡片：如"阳光电源加仓500股成本80.2" → 系统确认后写入 |

**修改类型**：

| 操作 | 说明 | 成本价处理 |
|------|------|-----------|
| 加仓 | 增加数量 | 加权平均新成本 = (原成本 + 新增成本) / 新总数量 |
| 减仓 | 减少数量 | 成本价不变 |
| 改成本 | 仅调整成本价 | 直接更新为用户指定值 |
| 清仓 | 数量置 0 | is_active 置 false，归档到 portfolio_history |

#### 4.3.3 持仓盈亏计算

**F-PF-003 持仓盈亏展示**

| 属性 | 说明 |
|------|------|
| 优先级 | P1 |
| 描述 | 实时计算持仓个股的浮动盈亏 |
| 触发 | 每日行情更新后自动计算 |

**计算公式**：

| 指标 | 公式 |
|------|------|
| 浮动盈亏（金额） | (最新收盘价 - 成本价) × 持仓数量 |
| 浮动盈亏（比例） | (最新收盘价 - 成本价) / 成本价 × 100% |
| 持仓市值 | 最新收盘价 × 持仓数量 |
| 总盈亏 | Σ 各持仓浮动盈亏 |

---

### 4.4 买卖点提醒模块

#### 4.4.1 持仓个股操作提醒

**F-AL-001 持仓操作提醒**

| 属性 | 说明 |
|------|------|
| 优先级 | P0 |
| 描述 | 对持仓个股根据策略信号生成加仓/减仓/清仓提醒 |
| 触发 | 每日策略计算完成后 |
| 推送渠道 | 飞书 Bot |

**提醒逻辑**：

| 策略信号 | 持仓状态 | 浮动盈亏 | 建议操作 | 提醒优先级 |
|----------|---------|----------|----------|-----------|
| 买入（强） | 持仓 | 盈利 | 考虑加仓 | 中 |
| 买入（强） | 持仓 | 亏损 | 考虑补仓摊薄成本 | 中 |
| 卖出（弱） | 持仓 | 盈利 > 20% | 考虑减仓锁利 | 中 |
| 卖出（中） | 持仓 | 盈利 | 建议减仓 | 高 |
| 卖出（强） | 持仓 | 盈利 | 建议减仓或清仓 | 高 |
| 卖出（强） | 持仓 | 亏损 | 建议止损清仓 | 紧急 |
| 多空分歧 | 持仓 | - | 关注，暂不操作 | 低 |

**推送格式示例**：

```
📌 持仓提醒 | 2026-05-30

🔴 建议减仓
┌──────────────────────────────┐
│ 天孚通信 300394               │
│ 现价: ¥95.20  成本: ¥82.30   │
│ 盈亏: +15.7% (+¥12,900)      │
│                              │
│ 信号来源：日布林线触及上轨+   │
│ 周线MACD死叉                 │
│ 信号强度：★★★               │
│                              │
│ 建议：考虑减仓 1/3 锁利      │
└──────────────────────────────┘

⚠️ 仅供理论学习和理论练习使用，不构成投资建议
```

#### 4.4.2 全市场买点扫描

**F-AL-002 全市场买点扫描**

| 属性 | 说明 |
|------|------|
| 优先级 | P0 |
| 描述 | 每日扫描全市场个股，筛选出出现买入信号的标的 |
| 触发 | 每日策略计算完成后 |
| 推送渠道 | 飞书 Bot |
| 推送时间 | 策略计算完成后（正常约 18:30；延迟时按 4.4.4 降级策略处理） |

**扫描范围**：
- 全部 A 股（排除 ST/*ST、退市、当日停牌；新股不设最低上市天数限制，由各策略内部按自身数据要求自动过滤）
- 按信号强度降序排列
- 最多推送 Top 20 只
- 扫描范围由 `stock_master` 表（见 4.1.3）确定

**推送格式示例**：

```
📊 全市场买点扫描 | 2026-05-30

筛选条件：日线布林线下轨反弹 | 日线底背离 | 周线金叉
共扫描 5,231 只，发现 15 只买入信号

排名 | 代码 | 名称 | 现价 | 信号 | 强度 | 策略来源
1 | 300274 | 阳光电源 | 78.50 | 买入 | ★★★ | 布林线+底背离+周线金叉
2 | 603986 | 兆易创新 | 142.30 | 买入 | ★★ | 布林线下轨反弹
3 | 600160 | 巨化股份 | 28.60 | 买入 | ★★ | 周线金叉
... (共 15 只)

查看详情：直接回复"帮我看看300274"或"阳光电源什么情况"等自然语言

⚠️ 仅供理论学习和理论练习使用，不构成投资建议
```

#### 4.4.3 详情查看

**F-AL-003 个股详情**

用户通过自然语言询问（如"帮我看看300274"、"阳光电源什么情况"）后，DeepSeek 调用 `get_stock_detail` 查询并按以下格式推送分析详情：

```
📋 阳光电源 300274 策略分析

📈 日布林线（20,2）
- 当前价 ¥78.50，位于下轨（¥76.80）附近
- 带宽 3.2%，处于缩口状态，变盘在即
- 信号：下轨反弹 ★★

📊 量价背离
- 日线：5/28 底背离（价创新低，量未创新低）★★
- 周线：无明显背离
- 月线：无明显背离

📐 周趋势
- 5 周均线 ¥82.10，20 周均线 ¥85.30，空头排列
- 周线 MACD 金叉确认（零轴下方）★★
- 趋势线：跌破上升趋势线，等待重新站稳

🎯 综合建议
方向：买入 | 强度：★★★
策略共振：布林线+日线底背离+周线金叉
参考买入区间：¥76.80-80.00
止损参考：¥73.50（周最低价-1ATR）

⚠️ 仅供理论学习和理论练习使用，不构成投资建议
```

#### 4.4.4 推送降级策略

当数据采集因重试导致策略计算延迟完成时，推送时间按以下规则降级：

| 策略计算完成时间 | 推送行为 | 标注 |
|-----------------|---------|------|
| 当日 18:30 前 | 正常推送，18:30 准时发送 | 无 |
| 当日 18:30 – 21:00 | 计算完成后立即推送 | 附加 "⏰ 今日数据更新延迟" |
| 当日 21:00 后 | **不推送**，次日 09:00 补推 | 附加 "📌 昨日延迟信号（数据于 XX:XX 更新）" |
| 采集失败无计算结果 | 不推送买点扫描；有持仓的用户推送 "今日数据暂不可用" | — |

> **原则**：晚间 21:00 后不打扰用户，延迟信号展期到次日盘前，给用户充足时间在开盘前消化。

---

### 4.5 飞书交互设计（自然语言驱动）

#### 4.5.1 设计原则

用户通过飞书与系统**自然语言对话**，无需记忆固定指令格式。核心设计原则：

- **系统状态自动注入**：每次对话时，系统将当前持仓、最新信号、策略状态、数据日期等自动组装为 AI 上下文，用户无需每次描述现状
- **AI 理解意图 → Function Calling 执行**：DeepSeek 将用户自然语言转化为结构化函数调用（查询/写入/设置），Python 本地执行
- **写入操作需确认**：所有修改操作（建仓/减仓/清仓/改参数）先推飞书确认卡片，用户点击确认后执行
- **查询操作即时返回**：详情/持仓/买点扫描等只读操作直接返回，无需确认
- **AI 不可用降级**：DeepSeek 连续 3 次调用失败后，系统自动回复快捷指令菜单，用户可通过回复数字完成核心操作

```
[系统自动回复]
⚠️ AI 服务暂时不可用，你可以回复数字执行操作：
[1] 查看持仓
[2] 查看今日买点扫描
[3] 查看数据状态
[4] 查看策略配置
如需修改持仓或参数，请等待 AI 服务恢复后使用自然语言操作。
```

#### 4.5.2 动态系统上下文

每次飞书消息到达时，FastAPI 自动组装以下上下文注入到 DeepSeek 请求的 System Prompt 中：

```python
def build_system_context() -> str:
    """
    从数据库实时抓取系统快照，作为 AI 的"当前状态知识"。

    上下文内容：
    1. 角色定义（固定）
    2. 当前持仓列表（含盈亏，从 portfolio + daily_quote 取）
    3. 今日策略信号摘要（从 signal_history 取 Top 5）
    4. 系统数据状态（最新交易日/是否交易日/已启用策略/当前偏好）
    5. 可用操作清单（从已注册的 TOOLS 列表自动生成）
    6. 当前时间
    """
    ...
```

**上下文示例**（某次实际发给 DeepSeek 的内容）：

```
## 用户当前持仓
- 阳光电源 300274  3000股 成本78.50 现价82.30 盈亏+4.8%
- 天孚通信 300394  1000股 成本82.30 现价79.60 盈亏-3.3%
总市值: ¥326,500

## 今日策略信号摘要
🔴 300274 阳光电源 买入 ★★★ [布林线下轨反弹+日线底背离+周线金叉]
🟢 300394 天孚通信 卖出 ★★ [布林线上轨触及+量价顶背离]
🔴 603986 兆易创新 买入 ★★ [周线金叉]

## 系统状态
- 最新数据日期: 2026-06-06
- 今日是交易日
- 当前交易偏好: balanced
- 启用策略: 日布林线, 量价背离, 周趋势

## 可用操作
查询: 查个股详情(需代码)、查看持仓、买点扫描、数据状态
写入: 建仓/加仓/减仓/清仓 → 先确认卡片，用户确认后执行
设置: 调整策略参数、切换交易偏好(左侧/右侧/均衡)
```

有了这些上下文，用户可以说"它还能拿着吗"，AI 自然知道"它"指向持仓中的某只股票，不需要用户每次指定代码。

##### 4.5.2.1 对话历史与自动摘要

系统上下文解决短期指代问题，但用户可能几天后才回来继续讨论同一只股票。仅靠 TTL 过期无法处理这种场景——过期了丢失记忆，不过期则 token 消耗持续膨胀。

**方案**：最近 30 轮完整保留 + 超出部分自动压缩为摘要。

```python
MAX_ROUNDS = 30  # 保留最近30轮完整对话

def manage_conversation_history(chat_id: str, user_msg: str, assistant_msg: str):
    key_msgs = f"chat:{chat_id}:msgs"
    key_summary = f"chat:{chat_id}:summary"

    msgs = json.loads(r.get(key_msgs) or "[]")
    summary = r.get(key_summary) or b""
    summary = summary.decode("utf-8") if summary else ""

    # 追加新消息
    msgs.append({"role": "user", "content": user_msg})
    msgs.append({"role": "assistant", "content": assistant_msg})

    # 超过30轮 → 取最早一批，生成摘要后丢弃
    if len(msgs) > MAX_ROUNDS * 2:
        overflow = msgs[:-MAX_ROUNDS * 2]  # 超出的旧消息
        msgs = msgs[-MAX_ROUNDS * 2:]       # 保留最近30轮

        # 调用 DeepSeek 将旧消息压缩为摘要
        new_summary = summarize_conversation(overflow, summary)
        summary = new_summary

    # 写回 Redis（7天过期，足够覆盖用户几天不用的场景）
    # 注意：对话历史和摘要为仅内存数据，Redis 重启会丢失。对单用户系统影响有限
    # （最差情况=丢失最近一周对话记忆，系统上下文仍能正常注入持仓/信号等实时状态）。
    # 如需要持久化，可配置 Redis AOF（appendonly yes）或定期备份对话数据到 PostgreSQL。
    r.setex(key_msgs, 7 * 86400, json.dumps(msgs, ensure_ascii=False))
    r.setex(key_summary, 7 * 86400, summary)


def summarize_conversation(overflow_msgs: list, prev_summary: str) -> str:
    """调用 DeepSeek 将旧对话压缩为一句话摘要"""
    prompt = f"""将以下对话片段压缩为1-2句话的摘要（仅保留关键信息：用户持仓操作、关注的股票、设置的偏好变更）。
已有摘要：{prev_summary or '(无)'}

对话片段：
{json.dumps(overflow_msgs, ensure_ascii=False)}

新摘要（1-2句中文）："""

    response = client.chat.completions.create(
        model="deepseek-chat",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=100,
    )
    return response.choices[0].message.content.strip()
```

**上下文组装时的使用方式**：
发给 DeepSeek 的 `messages` 数组结构如下：

```python
messages = [
    {"role": "system", "content": system_context},  # 动态系统上下文（持仓/信号/状态）
]

# 如果有历史摘要，注入到 system 之前
if summary:
    messages.insert(0, {"role": "system", "content": f"[历史对话摘要]\n{summary}"})

# 最近30轮完整消息
messages.extend(recent_msgs)

# 当前用户消息
messages.append({"role": "user", "content": user_text})
```

**效果示例**：

```
[历史对话摘要]
用户持有阳光电源300274（成本78.50，3000股），6月5日讨论过加仓但决定观望。
6月3日将交易偏好从左侧切换为均衡。

[最近30轮对话...]

用户: 这票现在怎么看
DeepSeek: → 摘要中有"300274"，自然知道"这票"=阳光电源
```

这样就解决了"几天不用回来照样认识你"的问题。

#### 4.5.3 Function Calling 工具定义

DeepSeek 可调用的函数（白名单）：

**查询类（即时返回，无需确认）：**

| 函数名 | 描述 | 参数 |
|--------|------|------|
| `get_stock_detail` | 获取个股最新策略分析 | `code: str` |
| `get_my_portfolio` | 获取当前持仓和盈亏 | 无 |
| `get_buy_signals` | 获取最近买点扫描结果 | `top_n: int (default 10)` |
| `get_data_status` | 获取数据采集状态 | 无 |
| `get_strategy_config` | 查看当前策略配置 | 无 |

**写入类（返回确认卡片，用户确认后执行）：**

| 函数名 | 描述 | 参数 |
|--------|------|------|
| `confirm_add_portfolio` | 建仓确认 | `stock_code, quantity, cost_price` |
| `confirm_modify_portfolio` | 修改持仓确认 | `stock_code, quantity (可选), cost_price (可选)` |
| `confirm_clear_portfolio` | 清仓确认 | `stock_code` |
| `confirm_update_strategy_param` | 修改策略参数确认 | `strategy_name, param_key, new_value` |
| `confirm_set_preference` | 切换交易偏好确认 | `mode: "left"/"right"/"balanced"` |
| `confirm_toggle_strategy` | 启用/停用信号策略确认（仅限3个信号策略，global_preference 不允许关闭） | `strategy_name, enabled: bool` |

**查询函数统一返回结构**：

所有查询类函数返回以下结构，确保 DeepSeek 能正确处理异常情况：

```python
@dataclass
class QueryResult:
    success: bool          # True=查询成功，False=失败
    data: dict | None      # 成功时的数据
    error: str = ""        # 失败时的错误描述（如"未找到股票代码 123456"）
```

DeepSeek 收到 `success=false` 时会自然告知用户错误原因（如"抱歉，未找到代码 123456 对应的股票，请检查代码是否正确"），无需额外编程处理。

**API 调用方式**：

```python
# 调用 DeepSeek API，tool_choice="auto" 让模型自己决定是否调用函数
response = client.chat.completions.create(
    model="deepseek-chat",
    messages=messages,           # system上下文 + 对话历史摘要 + 最近30轮 + 当前消息
    tools=TOOLS,                 # 上述白名单函数定义
    tool_choice="auto",          # auto: DeepSeek 自己决定要不要调用函数、调用哪个
    temperature=0.1,             # 低温度保证意图识别一致性
)

msg = response.choices[0].message

# 两种情况：
# 1. msg.tool_calls 非空 → DeepSeek 决定调用函数 → Python 执行 → 结果再发给 DeepSeek 生成自然语言回复
# 2. msg.tool_calls 为空 → 纯自然语言聊天（如"你好""谢谢"），DeepSeek 直接回复
if msg.tool_calls:
    # ... 执行函数，结果发回 DeepSeek 生成最终回复
else:
    reply = msg.content  # 直接使用 DeepSeek 回复
```

**管理类（即时执行）：**

| 函数名 | 描述 | 参数 |
|--------|------|------|
| `trigger_recalculation` | 手动触发全市场策略重算（使用已有行情数据，不重新采集），覆盖当日已有信号。计算完成后按降级策略决定是否推送，推送去重由 Redis `push:{date}:{code}:{strategy}` 保证 | `scope: str ("all" 全市场)` |

#### 4.5.4 安全边界

```
                    用户消息
                       │
                       ▼
              ┌────────────────┐
              │ DeepSeek 理解   │
              │ 返回函数调用     │
              └───────┬────────┘
                      │
        ┌─────────────┼─────────────┐
        ▼             ▼             ▼
   查询类函数      写入类函数      管理类函数
   (即时执行)    (生成确认卡片)   (即时执行)
        │             │             │
        ▼             ▼             ▼
   直接返回数据   飞书推送卡片    直接执行
                     │
              ┌──────▼──────┐
              │ 用户点确认   │
              │ Python 写入  │  ← 唯一写数据库的入口
              └─────────────┘
```

**核心安全规则**：
- DeepSeek 不直接写数据库，只能返回函数调用指令
- 写入类函数只生成确认卡片，数据库 INSERT/UPDATE 由用户点击确认按钮后 Python 执行
- 用户 = 系统唯一使用者，确认卡片即为最终授权

#### 4.5.5 对话示例

```
用户: 帮我看看300274什么情况

Bot:  📋 阳光电源 300274 当前 ¥82.30
      📈 布林线(20,2)：价格从下轨反弹回通道内 ★★
      📊 量价背离：日线底背离确认 ★★
      📐 周趋势：5周均线上穿20周均线，金叉 ★★
      🎯 综合：买入 ★★★（三策略共振）
      参考买入区间 ¥80.00-83.00 | 止损 ¥76.50
      ⚠️ 仅供参考，不构成投资建议

用户: 我成本78.5有3000股，现在要不要加？

Bot:  当前浮盈+4.8%（¥11,400），三策略共振看多。
      布林线信号处于买入区间，但需要注意：
      - 建议加仓不超过现有仓位的1/3（约1000股）
      - 止损位统一上移至 ¥78.50（你的成本线）
      
      需要我帮你记录加仓操作吗？

用户: 好的，加1000股吧

Bot:  📝 确认加仓
      **阳光电源** 300274
      ├─ 原持仓：3000股 成本¥78.50
      ├─ 加仓：1000股 @ ¥82.30
      ├─ 新持仓：4000股
      └─ 新成本：¥79.45（加权平均）
      
      [✅ 确认]  [❌ 取消]

用户: [点击确认]

Bot:  ✅ 已更新！阳光电源 300274 现持仓 4000 股，成本 ¥79.45。
```

#### 4.5.6 定时推送

除自然语言交互外，系统仍保留以下主动推送：

| 触发条件 | 内容 | 说明 |
|----------|------|------|
| 策略计算完成 | 持仓提醒（如有操作信号） | 推送交互卡片 |
| 策略计算完成 | 全市场买点扫描 Top 20 | 汇总列表 |
| 周五 19:00 | 周度总结 | 周线信号 + 下周展望 |
| 数据采集失败 | 告警 | 通知用户检查 |
| 推送降级 | 按 4.4.4 规则处理 | 21:00 后延至次日 |

---

### 4.6 Web 界面模块

#### 4.6.1 功能定位

- **辅助飞书**：提供更直观的数据浏览和策略分析界面，不替代飞书交互核心功能
- **只读为主**：初期仅支持查看，不实现复杂的交易操作录入（持仓录入/修改仍通过飞书），降低安全风险
- **简单轻量**：仅展示核心数据：持仓列表、盈亏、全市场买点清单、个股详情、数据状态

#### 4.6.2 页面清单

| 页面 | 路由 | 功能说明 |
|------|------|---------|
| 登录页 | /login | 简单密码认证或飞书 OAuth，防止公开访问 |
| 持仓看板 | /portfolio | 展示当前持仓列表：代码、名称、数量、成本价、现价、浮动盈亏、盈亏比例；支持手动刷新 |
| 买点扫描 | /buy_signals | 展示当日及历史买点扫描结果（Top 20），可按日期筛选，点击股票可看详情 |
| 个股详情 | /stock/:code | 展示单只股票的 K 线图（ECharts）、策略信号详情、历史信号记录 |
| 数据状态 | /data_status | 交易日历视图 + 每日下载状态(开始时间/完成时间/条数/成功/失败) + 各交易所数据量统计 + 最近下载日志 |
| 策略配置（可选） | /strategy_config | 只读查看当前策略启用状态和参数；配置修改仍通过飞书 |

#### 4.6.3 界面原型

![持仓看板原型](../imgs/260601_00_生图/prototype_portfolio.jpg)

![买点扫描原型](../imgs/260601_00_生图/prototype_buy_signals.jpg)

![个股详情原型](../imgs/260601_00_生图/prototype_stock_detail.jpg)

![数据状态原型](../imgs/260601_00_生图/prototype_data_status.jpg)

#### 4.6.4 界面设计原则

- 响应式布局，适配 PC 和手机浏览器
- 暗色/亮色主题可选（简单实现）
- 图表使用 ECharts 展示近 30 日 K 线及布林带
- 页面顶部显示"本系统所有分析仅供参考，不构成投资建议"的免责声明

#### 4.6.5 后端 API 设计

| 接口 | 方法 | 说明 | 返回数据 |
|------|------|------|---------|
| /api/portfolio | GET | 获取当前持仓（含实时盈亏） | 持仓列表 JSON |
| /api/buy_signals?date=YYYY-MM-DD | GET | 获取指定日期的买点扫描结果 | 信号列表 |
| /api/stock/:code/detail | GET | 获取个股策略分析详情 | 策略信号+技术指标 |
| /api/stock/:code/kline?days=30 | GET | 获取最近 N 日日 K 线数据（含布林带） | OHLCV + 布林带上下轨 |
| /api/data_status | GET | 获取数据采集状态 | 最新日期、各交易所状态 |

**API 错误码**：

| HTTP 状态码 | code | 说明 |
|-------------|------|------|
| 200 | OK | 成功 |
| 401 | UNAUTHORIZED | 未认证或 Token 过期 |
| 403 | FORBIDDEN | 无权限 |
| 404 | NOT_FOUND | 资源不存在（股票代码无效等） |
| 422 | VALIDATION_ERROR | 参数校验失败 |
| 429 | RATE_LIMITED | 请求过频 |
| 500 | INTERNAL_ERROR | 服务端内部错误 |

所有 API 均需认证（JWT Token），且强制 HTTPS。

#### 4.6.6 认证与安全

- **推荐方案**：集成飞书 OAuth，用户通过飞书扫码登录 Web 界面，复用飞书身份认证
- **备选方案**：Nginx 配置 HTTP Basic Auth，用户名密码硬编码（仅单用户）
- **IP 白名单**：可额外限制只有用户家庭/办公 IP 可访问 Web 界面（可选）
- 所有 API 均需认证，且强制 HTTPS

#### 4.6.7 技术实现

- **后端**：FastAPI 提供 REST API，与现有 PostgreSQL 直接交互，复用策略引擎的计算结果（无需重复计算）
- **前端**：Vue 3 + Vite + Element Plus，打包为静态文件，由 Nginx 托管
- **部署**：Nginx 监听 443 端口，配置 SSL 证书，代理后端 API（/api/）和飞书 Webhook（/webhook/）到 FastAPI（运行在本地 8000 端口），并托管前端静态文件

#### 4.6.8 工作量估算

| 任务 | 预估时间 |
|------|---------|
| 后端 API 开发（5 个接口） | 1 天 |
| 前端页面开发（4 个页面） | 2 天 |
| 认证集成（飞书 OAuth） | 0.5 天 |
| 域名配置 + Nginx + SSL | 0.5 天 |
| 测试与联调 | 1 天 |
| **合计** | **约 5 人天** |

---

## 5 Redis 使用详述

| 用途 | Key 格式 | Value | TTL | 说明 |
|------|---------|-------|-----|------|
| 交易日历缓存 | `cal:{date}` | `1`/`0` | 永久（手动刷新） | 避免每次采集都查数据库 |
| 对话历史 | `chat:{chat_id}:msgs` | JSON 数组（最近 30 轮消息） | 7 天（无新消息自动清理） | DeepSeek API 上下文续接 |
| 对话摘要 | `chat:{chat_id}:summary` | 文本（超过 30 轮的旧消息自动生成摘要并入此处） | 7 天 | 长对话压缩，控制 token 消耗 |
| 推送去重 | `push:{date}:{code}:{strategy}` | `1` | 24h | 同一股票同策略同日不重复推送 |
| 策略计算结果缓存 | `signal:{date}:{code}` | JSON | 24h | Web API 直接读缓存，减少数据库查询 |
| 飞书限频计数 | `rate:feishu:{hour}` | 计数 | 1h | 每小时推送不超过 50 条，防止触发飞书限频 |
| 最新交易日 | `latest_trade_date` | `YYYY-MM-DD` | 永久 | Web 状态页快速读取 |

> **对话数据持久化**：对话历史和摘要为仅内存数据，Redis 重启会丢失。对单用户系统影响有限（最差情况 = 丢失最近一周对话记忆，系统上下文仍能正常注入持仓/信号等实时状态）。如需增强：配置 Redis AOF（`appendonly yes`），或定期将摘要回写 PostgreSQL。

---

## 6 系统监控与健康检查

### 6.1 健康检查脚本

每小时执行一次，检测以下项目：

| 检查项 | 阈值 | 异常处理 |
|--------|------|---------|
| PostgreSQL 连接 | 连接失败 | 飞书告警 + 尝试自动重启 |
| Redis 连接 | 连接失败 | 飞书告警（非致命，降级为直查数据库） |
| 数据盘空间 | 使用率 > 80% | 飞书告警；> 90% 触发日志清理 |
| CPU 使用率 | 持续 5 分钟 > 90% | 飞书告警 |
| 内存使用率 | 持续 5 分钟 > 85% | 飞书告警 |
| FastAPI 进程 | 进程不存在 | 自动重启 + 飞书告警 |
| DeepSeek API | 连续 3 次调用失败 | 飞书告警，自动回复"AI 服务暂不可用，请稍候或使用快捷指令：[1] 查看持仓 [2] 买点扫描 [3] 数据状态" |
| 数据时效性 | 最新数据日期滞后 > 1 交易日 | 飞书告警（交易日 19:00 后检测） |
| 采集成功率 | 当日 < 95% | 飞书告警 |

### 6.2 监控数据存储

监控指标写入 `system_metrics` 表：

```sql
CREATE TABLE system_metrics (
    id           SERIAL PRIMARY KEY,
    metric_name  VARCHAR(30)  NOT NULL,
    metric_value NUMERIC      NOT NULL,
    status       VARCHAR(10)  NOT NULL CHECK (status IN ('ok', 'warn', 'error')),
    detail       TEXT,                       -- 异常时记录详情
    checked_at   TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_sm_name_time ON system_metrics (metric_name, checked_at DESC);
CREATE INDEX idx_sm_status    ON system_metrics (status, checked_at DESC);
```

> **自动清理**：监控数据保留 1 年，通过定时任务执行 `DELETE FROM system_metrics WHERE checked_at < NOW() - INTERVAL '1 year'`。

### 6.3 日志管理

| 日志类型 | 路径 | 保留策略 |
|----------|------|---------|
| 数据采集日志 | /var/log/stock-monitor/crawl.log | 保留 90 天，logrotate 按日轮转 |
| 策略计算日志 | /var/log/stock-monitor/strategy.log | 保留 90 天 |
| 健康检查日志 | /var/log/stock-monitor/health.log | 保留 90 天 |
| FastAPI 访问日志 | /var/log/stock-monitor/api.log | 保留 30 天 |
| Nginx 访问日志 | /var/log/nginx/access.log | 保留 30 天 |

---

## 7 非功能需求

### 7.1 性能

| 指标 | 目标 |
|------|------|
| 历史数据初始化 | 5 年全量数据采集 ≤ 4 小时 |
| 每日增量更新 | 单日三交易所数据采集+入库 ≤ 10 分钟 |
| 全市场策略计算 | ≤ 5 分钟（约 5000 只个股 × 3 策略） |
| 飞书推送延迟 | 策略计算完成后 ≤ 30 秒内送达 |
| 单只个股策略详情 | ≤ 2 秒 |
| Web 首页加载时间 | ≤ 3 秒 |
| K 线图数据查询 | ≤ 1 秒 |

**性能估算参考**：

| 项目 | 测算依据 | 预估值 |
|------|---------|--------|
| 5 年日行情数据量 | 5000 只 × 1200 交易日 = 600 万条 | 压缩后约 6GB（含索引） |
| 每日增量更新 | 5000 只 × 1 日 = 5000 条 | 耗时 ≈ 2-4 秒（不含网络 IO） |
| 全市场策略计算 | 5000 只 × 3 策略 × 日均 50 指标 | 单线程 ≈ 12 分钟，优化后（向量化+并行）≤ 5 分钟 |
| 数据库连接池 | 最小 5，最大 20 | 满足并发采集与计算 |

### 7.2 可用性

| 指标 | 目标 |
|------|------|
| 系统可用性 | 99.5%（允许月度停机 3.6 小时） |
| 数据采集成功率 | ≥ 99%（单个交易日失败自动重试） |
| 飞书推送成功率 | ≥ 99.5%（失败重试 2 次） |
| Web 界面可用性 | 与后端独立，后端故障不影响已有静态页面展示（数据不可用时有友好提示） |

### 7.3 数据保留

| 数据类型 | 保留周期 |
|----------|---------|
| 日行情数据 | 永久保留 |
| 策略信号记录 | 永久保留 |
| 持仓操作记录 | 永久保留 |
| 推送日志 | 保留 1 年 |
| 系统运行日志 | 保留 3 个月 |
| Web 访问日志 | 保留 3 个月 |
| 系统监控指标 | 保留 1 年 |

### 7.4 可维护性

- 所有策略参数通过配置文件管理，修改参数无需重启
- 数据采集脚本按交易所独立模块化，单个交易所变更不影响其他
- 新增策略只需实现标准接口（`StrategyBase`），注册到 Strategy Pool 即可
- 数据库 Schema 变更使用 Alembic migration 脚本管理
- Web 前后端独立部署，可分别升级

---

## 8 项目里程碑

### 8.1 分期计划

| 阶段 | 内容 | 预估周期 |
|------|------|---------|
| P0-基础搭建 | 服务器部署 + 数据库 + FastAPI + 飞书 Bot 对接 + DeepSeek API 集成 | 1 周 |
| P0-数据层 | 三交易所数据采集 + 5 年历史初始化 + 交易日历 + 增量更新 | 1.5 周 |
| P0-策略引擎 | 三大策略 + 全局偏好实现 + 信号融合 + 每日自动计算 | 2 周 |
| P0-持仓管理 | 持仓 CRUD + 盈亏计算 + 飞书交互指令 | 1 周 |
| P0-提醒推送 | 持仓操作提醒 + 全市场买点扫描 + 定时推送 | 1 周 |
| P0-Web 界面 | 简单 Web 看板开发与部署（API+前端+认证） | 0.5 周（可与策略引擎并行） |
| P1-行业数据 | 行业指数 + 行业分类采集 + 板块维度分析 | 1 周 |
| P1-优化迭代 | 推送体验优化 + 策略参数调优 + 异常处理完善 | 持续 |

### 8.2 验收标准

| 里程碑 | 验收条件 |
|--------|---------|
| 基础搭建 | FastAPI 运行正常，飞书 Bot 可收发消息，DeepSeek API 可正确理解意图并调用函数 |
| 数据层 | 5 年历史数据完整入库，每日增量更新自动化运行 |
| 策略引擎 | 三大策略可独立运行，全局偏好正确修饰参数，信号融合输出正确 |
| 持仓管理 | 支持飞书指令增删改查持仓，盈亏计算准确 |
| 提醒推送 | 持仓提醒和买点扫描每日自动推送，格式清晰 |
| Web 界面 | 可通过域名 HTTPS 访问，查看持仓、买点扫描、个股详情、数据状态；认证有效 |

---

## 9 风险与约束

| 风险 | 影响 | 缓解措施 |
|------|------|---------|
| 交易所网站改版/反爬 | 数据采集中断 | 监控采集失败率，适配层隔离解析逻辑，支持人工上传补数据 |
| 数据格式不一致 | 解析错误 | 统一数据清洗层，异常数据标记不丢弃 |
| 策略信号滞后 | 日线数据天然滞后一天 | 明确告知用户信号基于前收盘数据，不作为当日操作依据 |
| 华为云服务中断 | 系统停机 | 数据盘快照备份，ECS 自动恢复 |
| 飞书 API 限频 | 推送延迟 | 消息合并，单次推送控制在 API 限频内 |
| Web 界面未授权访问 | 数据泄露 | 强制认证 + HTTPS，可选 IP 白名单 |
| DeepSeek API 不可用 | 飞书自然语言交互停摆 | 健康检查监控，连续 3 次调用失败则飞书告警 + 自动回复快捷指令菜单（查看持仓/买点扫描/数据状态）；DeepSeek 恢复后自动恢复自然语言交互 |
| 域名证书过期 | 访问失败 | Let's Encrypt 自动续期 + 监控告警 |

### 合法合规与数据源使用声明

| 条款类型 | 内容描述 |
|----------|---------|
| 数据所有权 | 所有行情数据版权归各交易所及相关权利人所有，系统不主张任何所有权 |
| 使用许可 | 依据上交所、深交所官网法律声明，个人非商业目的可浏览、下载。本系统严格限定为个人学习与研究使用 |
| 禁止行为 | 严禁以任何形式将系统数据向第三方转发、出售、公开或用于商业策略产品 |
| 爬虫合规 | 采集前检查各站点的 robots.txt；请求间隔 ≥1 秒；User-Agent 设置为合法浏览器标识；不绕过验证码、IP 封锁等技术防护措施 |
| 免责声明 | 各交易所对数据准确性、完整性、及时性不作保证；本系统所有信号仅供参考，不构成投资建议 |

---

## 10 部署 Checklist

首次部署时按以下步骤执行：

### 10.1 华为云基础设施

- [ ] 创建 VPC（10.0.0.0/16）+ 子网（10.0.1.0/24）
- [ ] 购买 ECS（4C8G，Ubuntu 22.04，系统盘 40GB SSD）
- [ ] 创建并挂载 100GB SSD 数据盘至 /data
- [ ] 配置安全组（入站白名单 + 出站全放）
- [ ] 申请弹性公网 IP（2Mbps）
- [ ] 创建 OBS 桶（备份用）

### 10.2 系统环境

- [ ] 更新系统：`apt update && apt upgrade`
- [ ] 安装 Python 3.11、PostgreSQL 15、Redis 7、Nginx
- [ ] 配置 PostgreSQL 数据目录至 /data/pgdata
- [ ] 创建数据库和用户，设置强密码
- [ ] 执行 Schema 初始化脚本（建表+索引）
- [ ] 配置 Redis：设置密码，禁用危险命令

### 10.3 应用部署

- [ ] 克隆代码仓库至 /opt/stock-monitor
- [ ] 创建 Python 虚拟环境，安装依赖（pandas, ta-lib, psycopg2, redis, fastapi, uvicorn）
- [ ] 配置环境变量（数据库密码、Redis 密码、飞书凭证）
- [ ] 配置 FastAPI 服务（飞书 Webhook + REST API + DeepSeek 集成）
- [ ] 配置飞书 Bot：创建企业自建应用，配置事件订阅和回调地址
- [ ] 配置 cron 定时任务（见 2.3）
- [ ] 配置 systemd 服务（fastapi.service, nginx.service）

### 10.4 Web 界面

- [ ] 域名 DNS 解析至 ECS 公网 IP
- [ ] 申请 SSL 证书（Let's Encrypt）
- [ ] 构建 Vue 前端：`npm run build`
- [ ] 配置 Nginx（SSL + 反向代理 + 静态文件托管）
- [ ] 配置飞书 OAuth 或 HTTP Basic Auth
- [ ] 测试 HTTPS 访问和 API 认证

### 10.5 数据初始化

**必须严格按以下顺序执行，不可调换：**

- [ ] **步骤 1**：下载交易日历，导入 trade_calendar 表（后续步骤依赖此表判断交易日）
- [ ] **步骤 2**：执行 5 年历史数据初始化脚本（依据 trade_calendar 仅下载交易日数据，跳过非交易日避免无效请求）
- [ ] **步骤 3**：执行 `compute_all_factors()` 填充复权价格
- [ ] **步骤 4**：验证数据完整性（记录数、OHLC 校验、日期连续性、复权因子单调性）
- [ ] **步骤 5**：手动触发一次策略计算，验证信号输出

### 10.6 验收确认

- [ ] 飞书 Bot 可正常收发消息和交互卡片
- [ ] 每日自动采集和策略计算可正常执行
- [ ] Web 界面可正常访问，数据展示正确
- [ ] 健康检查脚本运行正常，告警可达
- [ ] 数据库备份脚本运行正常，OBS 上传成功

---

## 11 附录

### 11.1 策略接口规范

新增策略需实现以下接口：

```python
class StrategyBase(ABC):
    """策略基类，所有策略必须继承并实现"""

    @property
    @abstractmethod
    def name(self) -> str:
        """策略名称（与 strategy_config.strategy_name 对应）"""

    @property
    @abstractmethod
    def description(self) -> str:
        """策略描述"""

    @property
    @abstractmethod
    def params_schema(self) -> dict:
        """
        参数 JSON Schema，用于校验 strategy_config.params。

        飞书用户修改参数时，先通过此 Schema 校验，校验通过才写入数据库。

        示例：
        {
            "type": "object",
            "properties": {
                "period": {"type": "integer", "minimum": 10, "maximum": 60, "default": 20},
                "std_mult": {"type": "number", "minimum": 1.5, "maximum": 3.0, "default": 2.0},
                "bandwidth_threshold": {"type": "number", "minimum": 0.02, "maximum": 0.10, "default": 0.04}
            },
            "required": ["period", "std_mult"]
        }
        """
        pass

    @abstractmethod
    def calculate(self, df: pd.DataFrame, params: dict) -> List[Signal]:
        """
        计算策略信号

        Args:
            df: 个股日行情数据，按日期升序。注意 df['close'] 已被替换为 close_hfq（后复权价）
            params: 策略参数（已通过 params_schema 校验）

        Returns:
            信号列表
        """
        pass

    @abstractmethod
    def default_params(self) -> dict:
        """返回策略默认参数（与 strategy_config 初始 INSERT 保持一致）"""
        pass
```

### 11.2 Signal 数据结构

```python
@dataclass
class Signal:
    stock_code: str          # 证券代码
    stock_name: str          # 证券简称
    signal_date: str         # 信号日期 (YYYY-MM-DD)
    direction: str           # 信号方向: buy / sell / neutral
    strength: int            # 信号强度: 1-3
    strategy_name: str       # 来源策略（bollinger_daily / volume_price_divergence / weekly_trend）
    reason: str              # 信号原因描述
    price: float             # 信号触发时价格（后复权价 close_hfq）
    suggested_action: str    # 建议操作
    preference: str = "balanced"  # 触发时的全局偏好 mode
    risk_note: str = "仅供理论学习和理论练习使用，不构成投资建议"
```

### 11.3 飞书消息卡片模板（持仓提醒）

```json
{
  "msg_type": "interactive",
  "card": {
    "header": {
      "title": { "tag": "plain_text", "content": "📌 持仓提醒" },
      "template": "red"
    },
    "elements": [
      { "tag": "div", "text": { "tag": "lark_md", "content": "**天孚通信 300394**\n现价: ¥95.20 | 成本: ¥82.30\n盈亏: +15.7%\n信号: 日布林线触及上轨 + 周线MACD死叉\n建议: 减仓 1/3" } },
      { "tag": "action", "actions": [
        { "tag": "button", "text": { "tag": "plain_text", "content": "查看详情" }, "value": { "action": "detail", "code": "300394" } },
        { "tag": "button", "text": { "tag": "plain_text", "content": "已处理" }, "value": { "action": "dismiss" } }
      ]},
      { "tag": "note", "elements": [{ "tag": "plain_text", "content": "⚠️ 仅供理论学习和理论练习使用，不构成投资建议" }] }
    ]
  }
}
```

### 11.4 Nginx 配置示例

```nginx
server {
    listen 443 ssl http2;
    server_name your-domain.com;

    ssl_certificate /etc/letsencrypt/live/your-domain.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/your-domain.com/privkey.pem;

    # 前端静态文件
    location / {
        root /var/www/stock-monitor/dist;
        try_files $uri $uri/ /index.html;
    }

    # 飞书 Webhook
    location /webhook/ {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    # 后端 API
    location /api/ {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    # HTTP → HTTPS 重定向
    error_page 497 https://$host:$server_port$request_uri;
}

server {
    listen 80;
    server_name your-domain.com;
    return 301 https://$host$request_uri;
}
```

---

## 12 测试策略

### 12.1 测试体系概览

```
                    ┌───────────────────────────┐
                    │      E2E 端到端测试        │  少量，覆盖关键路径
                    │   (每日流水线 + 宕机恢复)    │
                    └───────────┬───────────────┘
                                │
              ┌─────────────────┼─────────────────┐
              │                 │                 │
    ┌─────────▼──────┐ ┌───────▼──────┐ ┌───────▼──────┐
    │  集成测试       │ │ 集成测试      │ │ 集成测试      │
    │  采集→入库→计算 │ │ 飞书指令→响应  │ │ API→DB 读写   │
    └─────────┬──────┘ └───────┬──────┘ └───────┬──────┘
              │                 │                 │
    ┌─────────▼──────┐ ┌───────▼──────┐ ┌───────▼──────┐
    │  单元测试       │ │ 单元测试      │ │ 单元测试      │
    │  数据解析/清洗   │ │ 策略计算/融合  │ │ 复权因子/校验  │
    └────────────────┘ └──────────────┘ └──────────────┘
```

### 12.2 单元测试

#### 12.2.1 数据解析与清洗

| 测试项 | 输入 | 预期输出 | 优先级 |
|--------|------|---------|--------|
| 上交所 CSV 解析 | 真实上交所行情 CSV 文件（10 行样本） | daily_quote 记录 10 条，字段映射正确 | P0 |
| 深交所 XLSX 解析 | 真实深交所行情文件 | 同上 | P0 |
| 北交所 CSV 解析 | 真实北交所行情文件 | 同上 | P0 |
| OHLC 逻辑校验 | 构造 high < low 的异常行 | 标记 review，不阻塞其他行 | P0 |
| 字段缺失容错 | 缺少 turnover（换手率）列的文件 | 该字段置 NULL，其余字段正常入库 | P1 |
| 编码异常 | 包含 GBK 无法解码字符的文件 | fallback 到 latin-1，记录告警日志 | P1 |
| 去重幂等 | 同一日数据入库两次 | 第二次 INSERT 全部跳过（唯一约束），记录 skip 计数 | P0 |
| 空文件 | 交易所返回空 CSV（仅有 header） | 记录警告日志，不崩溃 | P1 |

#### 12.2.2 策略计算

策略测试使用**人工标注的黄金数据集**（golden dataset）作为 ground truth。

**黄金数据集构造方法**：

```
选取 10 只代表性个股（覆盖大盘/中小盘/不同行业）× 回溯 2 年日线数据
由用户人工标注 3-5 个确认的买卖点（例如"2025-03-15 布林线下轨反弹，确认为买点"）
→ 共约 30-50 个标注事件
```

| 测试项 | 方法 | 验收标准 | 优先级 |
|--------|------|---------|--------|
| 布林线信号完整性 | 黄金数据集回测 | 召回率 ≥ 80%（人工标注的买卖点至少被策略捕获到） | P0 |
| 布林线无崩溃 | 5000 只全量日线数据输入 | 无异常退出，每只返回 ≥ 0 个信号 | P0 |
| 量价背离方向正确 | 构造已知顶背离/底背离的模拟数据 | 顶背离 → sell，底背离 → buy | P0 |
| 周趋势 K 线聚合 | 构造 5 个交易日数据 | 周 OHLCV 聚合结果正确 | P0 |
| 全局偏好修饰 | 同一数据 + left mode vs right mode 分别计算 | left 信号数量 ≥ right 信号数量（左侧更敏感） | P0 |
| Signal Combiner 融合 | 输入 3 个策略的混合信号 | 3 同向 → ★★★、2 同向 → ★★、矛盾 → 分歧 | P0 |
| 新股策略自适应 | 上市仅 10 个交易日的股票 | 布林线(需20日)不产出信号，量价背离(需20日)不产出信号，周趋势(需20周)不产出信号，综合融合结果为空信号列表，不崩溃 | P1 |
| 停牌/无数据 | 仅 3 个交易日数据的股票 | 不崩溃，返回空信号列表 | P1 |

#### 12.2.3 复权因子

| 测试项 | 输入 | 预期输出 | 优先级 |
|--------|------|---------|--------|
| 现金分红除权 | 构造除权前后 2 天数据（分红 0.50/股） | adj_factor 和 close_hfq 与手工计算结果一致（误差 ≤ 0.01） | P0 |
| 送转股除权 | 构造 10 送 5 事件 | adj_factor 正确计算 | P0 |
| 无除权日 | 无除权事件的股票全量数据 | 所有 adj_factor_hfq = 1.0 | P0 |
| 多次除权累积 | 一只股票 2 年内 3 次分红 | 最新日 adj_factor = 1.0，历史值单调递增 | P0 |
| 跳空检测阈值 | 收盘价日跌幅 9%（含除权）vs 9%（不含除权/真暴跌） | 前者标记 is_ex_date=true；后者不标记，但飞书仍可告警关注 | P1 |

### 12.3 集成测试

| 测试项 | 方法 | 验收标准 | 优先级 |
|--------|------|---------|--------|
| **每日流水线** | 手动执行 `daily_crawl.sh`，使用测试数据库 | 采集 → 入库 → 策略计算 → signal_history 写入，全链路无报错 | P0 |
| **补采流水线** | 删除最近 3 日数据 → 执行脚本 | `catch_up.py` 补全缺失数据，再正常采集当日 | P0 |
| **飞书自然语言建仓** | 发送"帮我建仓阳光电源，成本78.5，买3000股" | DeepSeek 识别意图 → 返回 `confirm_add_portfolio("300274", 3000, 78.5)` → 飞书推送确认卡片 | P0 |
| **飞书建仓确认** | 用户点击确认卡片 | portfolio 表新增 1 行 + portfolio_history 新增 1 行，数据正确 | P0 |
| **飞书自然语言参数修改** | 发送"把布林线的周期改成15" | DeepSeek 识别意图 → 返回 `confirm_update_strategy_param("bollinger_daily", "period", 15)` → 飞书推送确认卡片 | P1 |
| **飞书非法参数校验** | DeepSeek 返回参数值超出范围（如 period=999） | Python `params_schema` 校验 → 飞书回复错误原因（"period 范围 10-60"），数据库未变更 | P1 |
| **飞书自然语言查询** | 发送"帮我看看持仓" | DeepSeek → `get_my_portfolio()` → 返回持仓列表，格式正确 | P1 |
| **DeepSeek 降级** | 模拟 DeepSeek 连续 3 次返回错误 | 飞书收到快捷指令菜单回复，[1]-[4] 数字指令可正常响应 | P1 |
| **API 认证** | 无 Token 请求 `/api/portfolio` | 返回 401 | P0 |
| **API 正常响应** | 带有效 Token 请求 `/api/buy_signals?date=2026-06-05` | 返回 JSON，格式符合 4.6.5 定义 | P1 |

### 12.4 端到端测试

端到端测试覆盖关键异常路径，使用**测试数据库**（非生产库）。

| 测试场景 | 步骤 | 验收标准 | 优先级 |
|----------|------|---------|--------|
| **正常交易日全流程** | 手动置日历为交易日 → 执行脚本 → 检查飞书推送 | signal_history 有数据（含 preference 字段）、飞书推送可送达（用飞书 Bot 测试通道） | P0 |
| **自然语言交互全链路** | 飞书发送"帮我看看300274" → 检查回复 | DeepSeek → get_stock_detail → 返回策略分析详情，数据正确 | P0 |
| **非交易日静默** | 手动置日历为非交易日 → 执行脚本 | 脚本 exit 0，日志记录"非交易日，跳过"，无飞书推送 | P0 |
| **宕机恢复** | 正常跑 3 天 → 删最近 2 天数据 → 再执行脚本 | catch_up.py 补采 2 天 → 正常采集当日，最终 3 天数据完整 | P0 |
| **采集失败→告警** | 断开网络 → 执行脚本 | 3 次重试后飞书告警，failed_downloads 表有记录 | P1 |
| **除权事件→确认** | 用上市公司真实除权日数据 → 自动检测跳空 → 用户飞书确认 | corporate_actions 插入 + adj_factor 重算 + close_hfq 更新 | P1 |

### 12.5 测试工具与运行

| 工具 | 用途 | 说明 |
|------|------|------|
| pytest | 单元测试 + 集成测试框架 | 标准 Python 测试框架 |
| pytest-cov | 覆盖率 | 目标：核心模块（crawler/strategy/engine）≥ 80% |
| factory_boy | 测试数据构造 | 快速生成符合 Schema 的测试记录 |
| 测试数据库 | 集成/E2E 测试 | 独立 PostgreSQL database（`stock_monitor_test`），结构与生产一致，不污染生产数据 |
| 飞书 Bot 测试通道 | E2E 推送验证 | 飞书开放平台支持创建测试 Bot，不推送到真实用户 |

**运行策略**：

| 时机 | 执行范围 | 说明 |
|------|---------|------|
| 每次 git push | 单元测试（全量） | GitHub Actions / 华为云 DevCloud 自动触发 |
| 每日夜间 | 单元测试 + 集成测试 | 定时执行，结果飞书通知 |
| 发版前 | 全量（含 E2E） | 手动触发，在测试数据库上执行 |

### 12.6 不测试的内容（明确边界）

| 不测试 | 原因 |
|--------|------|
| ta-lib / pandas 等第三方库 | 依赖库自身有测试覆盖 |
| 飞书 API 本身 | 飞书平台保证，我们只测消息格式正确性 |
| 交易所官网可访问性 | 由健康检查监控覆盖（见第 6 章），非测试范畴 |
| PostgreSQL / Redis 功能 | 基础设施保证 |
| Vue 前端渲染 | 初期无复杂交互逻辑，仅静态页面 + API 调用；后续如有增加可通过 Playwright 补充 |

---

*文档结束 — v2.0 — 2026-06-06*
