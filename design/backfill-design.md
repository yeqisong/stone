# 历史补数功能模块 — 设计文档

> 版本：v3.0 | 状态：设计中 | 2026-06-18

---

## 变更记录

| 日期 | 版本 | 变更内容 | 作者 |
|------|------|----------|------|
| 2026-06-17 | v1.0 | 初版 | — |
| 2026-06-17 | v2.0 | 异步后台任务 + WS 进度推送；补数区域实时进度条；单日/跨日期差异化策略；停牌处理；弹窗风格统一 | — |
| 2026-06-18 | v3.0 | 基本面补数支持日期范围 + 历史季度数据回填；PE/PB API 增加重试；stock_fundamentals_history 落库设计 | — |

---

## 一、功能定位

在状态页底部新增「历史补数」模块，提供 5 个独立补数按钮。用户点击弹窗设置参数后提交**异步后台任务**，可关闭弹窗继续浏览其他页面。补数区域（非弹窗）通过 WebSocket 实时显示进度。系统复用适配器层原子功能执行，不写定制化下载逻辑。

**核心原则**：
- 复用适配器层（`DataSourceManager` → `Adapter.fetch_xxx()` + `writers.batch_upsert_xxx()`）
- 异步后台执行，弹窗提交后即可关闭
- 补数区域通过 WebSocket 实时推送进度，无需保持弹窗打开
- 非强制模式 = 断点续传（查询 DB 跳过已有数据）
- 单日期与跨日期采用不同的增量策略
- 一次只允许一个补数任务运行（baostock 非线程安全）
- 弹窗 UI 严格遵循项目 Naive UI 弹窗规范（`n-modal` + `n-card`，禁用 `preset="card"`）

---

## 二、前端设计

### 2.1 页面位置

`StatusView.vue` 底部，数据状态表格下方，新增「历史补数」卡片模块。

该模块包含两部分：
- **补数按钮区**：5 个补数按钮，点击打开弹窗
- **当前任务进度区**：当有后台任务运行时，实时显示进度条和状态（通过 WebSocket 更新）

### 2.2 补数区域布局（非弹窗部分）

```
┌─────────────────────────────────────────────────────────┐
│  📥 历史补数                                            │
├─────────────────────────────────────────────────────────┤
│  [📈 个股日K线] [📊 指数日K线] [💹 ETF日K线]            │
│  [📋 基本面] [⚙️ 基础指标加工]                           │
├─────────────────────────────────────────────────────────┤
│  ┌─ 仅在有运行中/刚完成的任务时显示 ─────────────────┐  │
│  │  📈 个股日K线  ● 运行中                           │  │
│  │  ████████░░░░░░░░ 8/20 批  1600/3921 只            │  │
│  │  245,000 行  |  失败 2 只  |  已耗时 12m 30s       │  │
│  │  [取消补数]                                        │  │
│  └──────────────────────────────────────────────────┘  │
│  ┌─ 最后一次完成的任务 ─────────────────────────────┐  │
│  │  📊 指数日K线  ✅ 已完成  2026-06-17 15:30        │  │
│  │  1,200 行  |  耗时 2m 15s  |  失败 0 只            │  │
│  └──────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────┘
```

**进度区规则**：
- 有 running 任务时：显示实时进度条 + 取消按钮
- 任务 completed/failed/cancelled 后：保留显示 30 秒，然后自动折叠到"最后一次完成的任务"
- "最后一次完成的任务"始终显示最近一条已完成/失败的任务摘要
- 所有进度更新通过 WebSocket `backfill_progress` 消息推送，不轮询 API

### 2.3 补数按钮（5 个）

| 按钮 | 类型 | 说明 |
|------|------|------|
| 个股日K线 | `kline` | 补全 A 股历史日K线 |
| 指数日K线 | `index` | 补全指数历史日K线 |
| ETF日K线 | `etf` | 补全 ETF 历史日K线 |
| 基本面 | `fund` | 补全 PE/PB/ROE/市值 |
| 基础指标加工 | `indicator` | 基于已有K线计算 BOLL/MACD/RSI/ATR/MA/量 |

### 2.4 补数弹窗（BackfillModal.vue）— UI 规范

> ⚠️ **必须遵循项目弹窗规范**（参考 `docs/naive-ui-patterns.md`）：
> 使用 `<n-modal>` 包裹 `<n-card>`，**禁止使用 `preset="card"`**。
> 与 `PortfolioView.vue`、`ModelView.vue` 的弹窗风格保持一致。

```html
<n-modal v-model:show="showModal">
  <n-card style="width:480px;max-width:92vw" :title="title" role="dialog" aria-modal="true">
    <n-space vertical>
      <!-- 参数区 -->
      ...
    </n-space>
    <template #footer>
      <n-space justify="end">
        <n-button @click="onStart" type="primary" :disabled="running">开始补数</n-button>
        <n-button @click="showModal=false">关闭</n-button>
      </n-space>
    </template>
  </n-card>
</n-modal>
```

弹窗内容：

```
┌──────────────────────────────────────────┐
│  📈 个股日K线补数                    [✕] │
├──────────────────────────────────────────┤
│  起始日期: [2021-06-17] 📅              │
│  截止日期: [2026-06-17] 📅              │
│                                          │
│  强制更新: [====○────] 关闭              │
│  说明: 开启→重新下载覆盖已有数据          │
│        关闭→跳过已有数据(断点续传)        │
├──────────────────────────────────────────┤
│          [开始补数]    [关闭]             │
└──────────────────────────────────────────┘
```

> ⚠️ 弹窗内**不再包含进度区**。进度区在弹窗外的补数卡片上显示。弹窗只是参数设置入口，提交后即可关闭。

#### 日期选择

- 起始日期默认：当天 - 5 年
- 截止日期默认：当天
- 前端校验：起始 ≤ 截止 ≤ 今天
- **基本面**类型：日期范围用于指定回填的历史季度区间（默认最近 1 季度）

#### 强制更新开关（n-switch）

- 关闭（默认）：断点续传，查询 DB 跳过已有数据的股票
- 开启：全量重新获取并覆盖已有数据

#### 弹窗按钮

| 阶段 | 开始补数 | 关闭 |
|------|:---:|:---:|
| 未开始 | 可点击 | 可点击 |
| 已提交（后台运行中） | 禁用（显示"已提交"） | 可点击 |

点击「开始补数」→ POST API 提交任务 → 成功后关闭弹窗 → 补数区域自动显示进度。

### 2.5 进度推送机制 — WebSocket

复用现有 WebSocket 连接（`/ws/dag`），新增消息类型 `backfill_progress`。

**服务端 → 客户端**：

```json
{
  "type": "backfill_progress",
  "task_id": "bf_kline_20260617_143022",
  "task_type": "kline",
  "task_label": "个股日K线",
  "status": "running",
  "start_date": "2021-06-16",
  "end_date": "2026-06-17",
  "force": false,
  "progress": {
    "current_batch": 8,
    "total_batches": 20,
    "stocks_done": 1600,
    "stocks_total": 3921,
    "rows": 245000,
    "errors": 2
  },
  "elapsed_seconds": 750,
  "eta_seconds": 1080
}
```

**推送频率**：
- 任务状态变更时（pending→running→completed/failed/cancelled）：立即推送
- 运行中：每 2 秒推送一次（与 DAG 推送复用同一广播循环）
- 任务结束后：推送最终状态，之后不再推送该 task

**前端接收处理**（StatusView.vue 中）：
- 监听 `backfill_progress` 消息
- 更新补数区域的进度条和状态
- 任务完成/失败时显示结果 30 秒后折叠

---

## 三、后端 API 设计

### 3.1 启动补数任务

```
POST /api/data_status/backfill

Request:
{
  "type": "kline",            // kline | index | etf | fund | indicator
  "start_date": "2021-06-16", // 可选，默认 5 年前。fund 类型按季度回填
  "end_date": "2026-06-17",   // 可选，默认今天
  "force": false              // 可选，默认 false
}

Response 200:
{ "ok": true, "task_id": "bf_kline_20260617_143022" }

Response 409 (busy):
{
  "ok": false, "error": "已有补数任务运行中，请等待完成或取消后再试",
  "busy": true,
  "current_task": { "task_id": "...", "type": "index", "status": "running" }
}

Response 400:
{ "ok": false, "error": "起始日期不能晚于截止日期" }
```

### 3.2 取消任务

```
POST /api/data_status/backfill/{task_id}/cancel

Response 200:
{ "ok": true, "message": "终止信号已发送，当前批次完成后停止" }
```

### 3.3 历史记录

```
GET /api/data_status/backfill/history?limit=20

Response 200:
{
  "tasks": [{ "task_id": "...", "type": "kline", "status": "completed", ... }]
}
```

> ⚠️ 不再提供 polling 式的 `GET /backfill/{task_id}` 端点。进度完全通过 WebSocket 推送。

---

## 四、后端执行引擎

### 4.1 架构

```
POST /api/data_status/backfill
  │
  ▼
BackfillManager (crawler/backfill.py, 新建)
  │
  ├─ 1. 校验：检查是否有运行中任务 → 409 or 继续
  ├─ 2. 创建 task_id，初始化任务状态进内存
  ├─ 3. 启动后台线程执行 _run_backfill()
  │     │
  │     ├─ 3.1 按类型分发策略:
  │     │     ├─ kline/index/etf → _run_kline_backfill()
  │     │     ├─ fund           → _run_fund_backfill()
  │     │     └─ indicator      → _run_indicator_backfill()
  │     │
  │     ├─ 3.2 单日 vs 跨日期判断 (kline/index/etf):
  │     │     ├─ start == end → 单日增量策略
  │     │     └─ start <  end → 跨日期批量策略
  │     │
  │     ├─ 3.3 分批循环（每批 200 只）:
  │     │     ├─ adapter.fetch_xxx(batch, start, end)
  │     │     ├─ writers.batch_upsert_xxx(db, rows)
  │     │     ├─ 更新进度到内存 + 触发 WS 广播唤醒
  │     │     ├─ 检查 _stop_requested → 终止
  │     │     └─ 批次间 relogin (baostock 会话维护)
  │     │
  │     └─ 3.4 标记 completed / failed，触发最后一次 WS 推送
  │
  └─ 4. WS 广播循环读取内存中的任务状态，推送给所有 WS 客户端
```

### 4.2 单日增量 vs 跨日期批量 — K线补数核心差异

这是设计的关键区分点，两种模式的取数策略不同：

#### 单日增量策略（start_date == end_date）

适用场景：补缺某一天的数据，如昨天漏采了。

```
1. 从 adapter.get_stock_list("stock") 获取全量股票代码
2. 查询 daily_quote: 哪些股票当天已有数据
   SELECT stock_code FROM daily_quote WHERE trade_date = :date
3. remaining = 全量代码 - 已有数据代码
4. 对 remaining 逐批请求 adapter.fetch_stock_kline(batch, date, date)
   → adapter 内部对每只股票请求单日的 K 线数据
```

**优势**：只请求缺失的股票，极大减少网络调用。日增量场景下通常只有个别股票漏采，大部分跳过。

#### 跨日期批量策略（start_date < end_date）

适用场景：补一段历史区间，如过去 5 年。

```
1. 从 adapter.get_stock_list("stock") 获取全量股票代码
2. 非 force 模式:
   查询 daily_quote: 哪些股票在区间内完全没有数据
   SELECT stock_code FROM daily_quote
   WHERE trade_date BETWEEN :start AND :end
   GROUP BY stock_code
   有数据 = 跳过；无数据 = 待下载
   force 模式: 全部下载
3. 对 remaining 逐批请求 adapter.fetch_stock_kline(batch, start, end)
   → adapter 内部对每只股票请求整个区间的 K 线
```

**与单日增量的关键区别**：
- 单日增量按「日期维度」跳过（查某一天哪些股票有数据）
- 跨日期按「股票维度」跳过（查哪些股票在区间内有任何数据）
- 跨日期下载时 adapter 一次请求拉取该股票的全部历史，比逐日请求高效得多

> ⚠️ 跨日期非强制模式下，如果股票有 1 天数据就整只跳过，不会填补区间内的空白天。这是有意为之：填充空白需要逐日对比，复杂度高且场景罕见。如果确实需要填补空白天，用户可开启「强制更新」。

### 4.3 各任务类型的适配器函数映射

| type | 获取代码列表 | 拉取函数 | 写入函数 | 跳过检查 |
|------|-------------|----------|----------|----------|
| `kline` | `adapter.get_stock_list("stock")` | `adapter.fetch_stock_kline(codes, start, end)` | `writers.batch_upsert_kline()` | 单日：查当日 daily_quote；跨日期：查区间内 daily_quote |
| `index` | `adapter.get_stock_list("index")` | `adapter.fetch_index_kline(codes, start, end)` | `writers.batch_upsert_index_kline()` | 同 kline，查 index_daily_quote |
| `etf` | `adapter.get_stock_list("etf")` | `adapter.fetch_etf_kline(codes, start, end)` | `writers.batch_upsert_kline()` | 同 kline，过滤 ETF 代码 |
| `fund` | `adapter.get_stock_list("stock")` | `adapter.fetch_fundamentals(codes)` | `writers.batch_upsert_fundamentals()` | 查 stock_fundamentals |
| `indicator` | `adapter.get_stock_list("stock")` | `pipeline.dag_task_indicator_full()` | 指标写入内部处理 | 查 indicator_calc_log |

> ⚠️ `indicator` 不依赖外部数据源，直接从 `daily_quote` 计算。无需 DataSourceManager 选源。

### 4.4 停牌处理

#### 问题

baostock 在股票停牌期间不返回任何数据行。如果不加处理会导致：

1. **反复补数循环**（核心问题）：非强制模式下，停牌日的股票在 `daily_quote` 中无记录 → 下次补数被判定为"漏采" → 再次尝试下载 → baostock 仍然无数据 → 无限循环
2. **MA 等指标计算偏差**：如果简单填入 0 或前值，会扭曲均线计算

#### 设计方案

**新增 `is_suspended` 列**：

```sql
ALTER TABLE daily_quote ADD COLUMN IF NOT EXISTS is_suspended BOOLEAN DEFAULT false;
ALTER TABLE index_daily_quote ADD COLUMN IF NOT EXISTS is_suspended BOOLEAN DEFAULT false;
```

**写入策略**（在 `writers.py` 或 backfill 执行逻辑中实现）：

| 场景 | 行为 |
|------|------|
| 正常交易数据 | 正常写入，`is_suspended=false` |
| 单日补数：某股票返回 0 行，且该股票已上市 | 写入一条停牌标记行：trade_date=该日，close/volume=0，`is_suspended=true` |
| 跨日期补数：某股票返回 0 行（整个区间停牌/退市），且已上市 | 写入一条停牌标记行：trade_date=end_date，close/volume=0，`is_suspended=true` |
| 未上市的股票（ipo_date > 查询日期） | 不写入任何行（正常跳过） |

**断点续传（非强制模式）的影响**：

- 单日补数：查 `WHERE trade_date = :date` → 停牌标记行被查出来 → 判定"已有数据" → 跳过 ✓
- 跨日期补数：查 `WHERE trade_date BETWEEN :start AND :end GROUP BY stock_code` → 停牌标记行被查出来 → 判定"已有数据" → 跳过 ✓
- **不会再有反复补数循环**

**MA / 技术指标计算的影响**：

- 指标计算时过滤 `WHERE is_suspended = false`，停牌日不参与均线窗口
- pandas rolling/ewm 遇到 NaN 会传播，过滤后连续交易日正常计算
- 前端 K 线图：`is_suspended=true` 的日期不画 K 线柱（显示为空白缺口），提示"停牌"

**`stock_indicators` 指标表的处理**：

- 指标计算按 `daily_quote` 有数据的日期逐行计算
- 停牌日无 K 线数据，指标表中该 stock+date 也无对应行
- 读取指标时自然跳过停牌日，无需特殊处理

**基本面市值计算中的停牌处理**（已有，不变）：

- `writers.py:_enrich_market_cap()` 查询 `volume > 0 AND turnover > 0` 的最新 K 线
- 停牌标记行 volume=0 自动被过滤
- 极端情况（完全没有交易记录）：市值保持 None

### 4.4-B 基本面补数 — 日期范围与历史季度回填

#### 数据分层

| 表 | 数据粒度 | 当前状态 | 补数行为 |
|---|---------|---------|---------|
| `stock_fundamentals` | 每只股票 1 行（最新快照） | ✅ 有数据 | 更新最新值 |
| `stock_fundamentals_history` | 每只股票 × 每季度 1 行 | ❌ 0 行 | **本次新增回填** |

#### 日期范围含义

基本面补数的起始/截止日期**按季度对齐**：

```
起始日期 2021-06-16 → 对齐到 2021Q2 (2021-04-01 ~ 2021-06-30)
截止日期 2026-06-18 → 对齐到 2026Q2 (2026-04-01 ~ 2026-06-30)
```

遍历区间内每个季度，对每只股票查询该季度的 ROE/增长率/PE/PB。

#### 季度数据来源

| 指标 | baostock 接口 | 参数 | 说明 |
|------|--------------|------|------|
| ROE | `query_profit_data(code, year, quarter)` | 年份 + 季度 | 小数，需 ×100 转 % |
| 营收同比增长率 | `query_growth_data(code, year, quarter)` | 同上 | 第 3 列 |
| 净利同比增长率 | `query_growth_data(code, year, quarter)` | 同上 | 第 4 列 |
| PE(TTM) / PB(MRQ) | `query_history_k_data_plus(code, "date,peTTM,pbMRQ", ...)` | 季度末日期 | 取该季度最后交易日的值 |

#### DB 写入逻辑

对每个季度 Q(y, q)，report_date 固定为该季度末日：

```
2021Q2 → report_date = '2021-06-30'
2021Q3 → report_date = '2021-09-30'
```

写入 `stock_fundamentals_history`：

```sql
INSERT INTO stock_fundamentals_history (stock_code, report_date, pe_ttm, pb_mrq, roe)
VALUES (:code, :report_date, :pe, :pb, :roe)
ON CONFLICT (stock_code, report_date) DO UPDATE SET ...
```

同时对**最新季度**同步更新 `stock_fundamentals`（保持两边一致）。

#### PE/PB API 重试

当前 `fetch_fundamentals` 中 PE/PB 的 `bs.query_history_k_data_plus` 调用**无重试**，baostock 不稳定时静默跳过导致大量股票缺失 PE/PB。改为与 `_fetch_kline_single` 同级的重试逻辑（4 次重试 + 指数退避 + login 检测）。

#### 断点续传

- `stock_fundamentals_history` 已有对应 (stock_code, report_date) 的记录时跳过
- `stock_fundamentals` 已有该股票记录时，仅更新（不跳过，保证是最新值）

#### 非强制模式 vs 强制模式

| 模式 | `stock_fundamentals` | `stock_fundamentals_history` |
|------|---------------------|------------------------------|
| 非强制（续传） | 仅更新无记录的股票 | 已有 (code, quarter) 记录则跳过 |
| 强制 | 全部重新查询覆盖 | 全部重新查询覆盖 |

### 4.5 进度存储与 WebSocket 推送

**进度存储**：内存字典 `dict[str, BackfillTask]`，进程重启丢失但不影响数据完整性。

**WebSocket 推送机制**（扩展现有 `broadcast_dag_status` 循环）：

```
现有 broadcast_dag_status() 循环（每 2s）:
  ├─ 查询 dag_run_log → 检测变化 → 推送 dag_status / dag_log
  └─ [新增] 查询 BackfillManager.get_active_task()
       → 如果有状态变更 → 推送 backfill_progress
```

触发方式：
- BackfillManager 更新进度后调用 `_wake_broadcast()`（复用现有 `app/signal.py` 的唤醒机制）
- WS 广播循环被唤醒后立即拉取最新 backfill 状态并推送

### 4.6 与现有 init_5year_adapter.py 的关系

两者核心逻辑相同（适配器拉取 + 断点续传），但 `backfill.py`：
- 不再通过命令行参数控制，改为 API 请求
- 增加后台线程 + WebSocket 进度推送
- 增加取消功能 + 历史记录
- 增加单日/跨日期差异化策略

未来可将 `init_5year_adapter.py` 替换为对 `backfill.py` 的调用，本次迭代暂不合并。

---

## 五、异常场景及应对

### 5.1 数据源不可用

| 场景 | 应对 |
|------|------|
| AKShare 和 baostock 都不可用 | 任务标记 `failed`，error_message = "所有数据源均不可用" |
| 运行中途数据源断开 | 当前批次失败，relogin × 3 次；仍失败 → `failed`，保留已写入数据 |
| baostock 会话超时/退化 | 每 200 只 relogin（与 `init_5year_adapter.py` 策略一致） |

### 5.2 数据库异常

| 场景 | 应对 |
|------|------|
| DB 连接断开 | 重试 × 3 次，间隔 5s；仍失败 → `failed` |
| UPSERT 冲突 | `ON CONFLICT DO UPDATE` 天然幂等，无需额外处理 |
| 写入超时 | batch_size 减半重试当前批 |
| 磁盘空间不足 | 捕获异常，标记 `failed` |

### 5.3 任务并发

| 场景 | 应对 |
|------|------|
| 用户重复点击「开始」 | API 返回 409 busy |
| 两个浏览器 Tab 同时操作 | 后端全局锁（`BackfillManager._active_task`） |

### 5.4 数据为空

| 场景 | 应对 |
|------|------|
| 非强制模式下所有数据已存在 | 所有股票被跳过 → 任务瞬间完成，进度显示 "数据已完整，无需补数" |
| 退市股票无此期间数据 | 写入停牌标记行后跳过，不计入 errors |
| 上市晚于起始日期的股票 | 只从 IPO 日期起有数据，正常 |
| 停牌导致某日无数据 | 写入停牌标记行（is_suspended=true），后续不再重复下载 |
| 停牌标记行被误判为"有效数据" | 指标计算和市值计算均过滤 is_suspended=true 的行 |

### 5.5 用户操作

| 场景 | 应对 |
|------|------|
| 用户点「取消补数」（补数区域的取消按钮） | 设置 `_stop_requested` 标志，当前批次完成后停止，状态 `cancelled` |
| 用户关闭弹窗 | 任务继续后台运行，进度在补数区域可见 |
| 用户刷新页面 | 重新挂载时重连 WS → 收到当前 running 任务的最近一次 `backfill_progress` |
| 用户选择非法日期 | 前端校验 + 后端 400 |

### 5.6 系统异常

| 场景 | 应对 |
|------|------|
| 进程重启 | 内存任务状态丢失，DB 数据已写入。补数区域无 running 任务显示 |
| 内存溢出 | 批次大小 200 只/批，每批即时提交事务释放内存 |

### 5.7 各补数类型的特有异常

| 类型 | 特有场景 | 应对 |
|------|----------|------|
| kline | 股票代码冲突（000001 = 平安银行 + 上证指数） | adapter 已区分 stock/index/etf 列表 |
| kline | 单日增量：当天是节假日/非交易日 | 后端校验交易日，非交易日返回提示 |
| index | 指数无复权概念 | `fetch_index_kline` 不请求后复权 |
| etf | ETF 和个股共用 `daily_quote` | 跳过检查时用 ETF 代码列表过滤 |
| fund | PE/PB 可能为 null | 写入时允许 null，前端显示 "—" |
| fund | 市值计算依赖 daily_quote 有数据 | `_enrich_market_cap` 查不到 → 市值留空 |
| indicator | 依赖 daily_quote 有数据 | 前置检查：daily_quote 为空则提示 "请先补全日K线数据" |

---

## 六、文件变更清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `web-v2/src/components/BackfillModal.vue` | **新建** | 补数弹窗（参数设置，不含进度） |
| `web-v2/src/components/StatusView.vue` | 修改 | 底部新增「历史补数」卡片（按钮 + 进度区）；WS 监听 `backfill_progress` |
| `app/api/status.py` | 修改 | 新增 3 个 backfill 端点（启动/取消/历史）；WS 广播循环中新增 `backfill_progress` 推送 |
| `crawler/backfill.py` | **新建** | BackfillManager：任务管理、单日/跨日期策略分发、后台线程执行、进度存储 |

---

## 七、暂不考虑

- 定时自动补数（由 DAG 负责）
- 多任务并行（baostock 非线程安全）
- 邮件/飞书通知补数完成（后续可加）
- 与 `init_5year_adapter.py` 合并（后续迭代）
- WebSocket 新开独立端点（复用现有 `/ws/dag`，新增消息类型）
