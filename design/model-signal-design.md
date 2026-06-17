# 模型应用设计

> 创建日期：2026-06-17 | 状态：v2.5+ 设计中

---

## 一、设计目标

1. **取消旧策略系统**：移除 bollinger_daily / volume_price_divergence / weekly_trend / signal_combiner 四条旧策略
2. **模型成为唯一信号源**：所有交易信号由当前上线的 ACTIVE 模型产生
3. **信号可追溯**：每条信号记录产生它的模型版本号和配置快照
4. **每日自动运行**：模型信号生成融入 daily_update DAG 链路，无需手动触发

---

## 二、每日完整流程

```
⏰ 17:35 cron 触发 daily_update
  │
  ├── [1] kline        ← 下载个股日K线 (BaostockCrawler)
  ├── [2] index        ← 下载指数日K线
  ├── [3] etf          ← 下载 ETF 日K线
  │
  ├── [4] fund         ← 下载基本面 (PE/PB/ROE/市值)
  │    └── [5] treemap ← 生成树图
  │
  ├── [6] indicator_incr ← 增量计算技术指标 (BOLL/MACD/RSI/ATR/MA/量)
  │    │                  依赖: kline (需要今日 K 线数据)
  │    │
  │    └── [7] model_signal ← 模型信号生成
  │         │  ① 读 ACTIVE 模型版本 + 全局偏好 (left/balanced/right)
  │         │  ② JOIN 今日指标数据 (boll/rsi/macd/volume)
  │         │  ③ 逐股评分: BOLL下轨(+1) + RSI超卖(+1) + MACD正柱(+1)
  │         │  ④ 评分 ≥ buy_score_min(偏好决定) → 生成买入信号
  │         │  ⑤ BOLL上轨+RSI超买 → 生成卖出信号
  │         │  ⑥ 写入 signal_history (含 model_version + preference + params_snapshot)
  │         │
  │         └── [8] model_health ← 模型健康评估
  │             ① 回填 N 日前信号的 forward 收益 (5/10/20天)
  │             ② 了结触发止损的信号 (止损比例由偏好决定)
  │             ③ 计算胜率/夏普/平均 forward 收益 → 写入 model_health
  │             ④ 生成本日健康报告
  │
  ├── [5] treemap ──→ [9] stats  ← 全系统统计 (含模型信号数)
  └── [8] model_health ──→ [9] stats
```

## 三、信号生成规则

### 3.1 指标来源

| 指标 | 表 | 字段 |
|------|------|------|
| 布林带 | stock_indicators_boll | pct_b (价格在布林带中的位置, 0~1) |
| RSI | stock_indicators_rsi | rsi (14日RSI) |
| MACD | stock_indicators_macd | dif, hist (MACD柱) |
| 量比 | stock_indicators_volume | vol_ratio (量比) |

### 3.2 评分规则

```
buy_score = 0
if pct_b < boll_lower:    buy_score += 1  # BOLL下轨超卖
if rsi < rsi_oversold:    buy_score += 1  # RSI超卖
if dif > hist:            buy_score += 1  # MACD正柱
```

### 3.3 信号判断

```
if buy_score >= buy_score_min → 买入信号
if pct_b > sell_boll_upper AND rsi > sell_rsi_overbought → 卖出信号
```

### 3.4 偏好联动

| 参数 | left(激进) | balanced(均衡) | right(保守) |
|------|-----------|---------------|-------------|
| buy_score_min | ≥1 | ≥2 | ≥3 |
| boll_lower | 0.25 | 0.20 | 0.15 |
| rsi_oversold | 40 | 35 | 30 |
| sell_boll_upper | 0.75 | 0.80 | 0.85 |
| sell_rsi_overbought | 60 | 65 | 70 |
| 止损比例 | 10% | 8% | 5% |

---

## 四、数据结构

### 4.1 signal_history 表（已有，补充说明）

| 字段 | 值 | 说明 |
|------|-----|------|
| strategy_name | `'model_signal'` | 固定值 |
| model_version | `'v2.1'` | 产生信号的模型版本 |
| params_snapshot | JSON | 模型配置快照（features/ml/risk/search_space） |
| preference | `'left'/'balanced'/'right'` | 信号产生时的偏好模式 |
| combined_signal | `true` | 始终标记为融合信号（唯一信号源） |
| source_strategies | `'[\"model_signal\"]'` | JSON 数组 |

### 4.2 params_snapshot 内容（从 model_versions.config 截取关键字段）

```json
{
  "model_version": "v2.1",
  "model_name": "BOLL+MACD+RSI 多策略融合",
  "features": ["boll", "macd", "rsi", "atr", "ma", "volume"],
  "ml_enabled": false,
  "search_space": {"n_estimators": [100,500], "max_depth": [3,10], "learning_rate": [0.01,0.3]},
  "signal_thresholds": {"buy_score_min": 2, "boll_lower": 0.2, "rsi_oversold": 35, "sell_boll_upper": 0.8, "sell_rsi_overbought": 65},
  "preference": "balanced",
  "risk": {"stop_loss_pct": 8, "signal_timeout_days": 20}
}
```

---

## 五、信号消费

### 5.1 前端页面

| 页面 | 数据来源 | 展示 |
|------|----------|------|
| 信号面板 (SignalsView) | `GET /buy_signals` → 查 `signal_history WHERE strategy_name='model_signal' AND direction='buy'` | 买点扫描列表 |
| 个股详情 (DetailView) | `GET /stock/{code}/detail` → 查最新信号 | 信号卡片 (含模型版本) |
| 持仓列表 (PortfolioView) | `GET /portfolio` → 关联最新信号 | 每只持仓附带信号方向 |

### 5.2 AI / 飞书

| 模块 | 端点/工具 | 说明 |
|------|----------|------|
| DeepSeek AI | `get_stock_detail(code)` | 返回个股最新信号 + 基本面 |
| DeepSeek AI | `get_buy_signals()` | 返回当日 Top N 买入信号 |
| 飞书 | 上下文构建 | 包含 Top 5 信号摘要 |

---

## 六、旧系统清理清单

### 6.1 删除文件

```
strategy/bollinger.py          ← BOLL 策略类
strategy/divergence.py         ← 量价背离策略类
strategy/weekly_trend.py       ← 周趋势策略类
strategy/combiner.py           ← 信号融合器
strategy/engine.py             ← 策略引擎
strategy/preference.py         ← PREFERENCE_ADJUSTMENTS (已被 _get_preference_thresholds 替代)
strategy/base.py               ← 旧 Signal 基类
tests/unit/test_strategies.py  ← 旧策略单元测试
```

### 6.2 保留文件

```
strategy/indicators.py         ← Boll/RSI/MACD 计算函数 (K线图 API 使用)
```

### 6.3 代码删除

| 位置 | 删除内容 |
|------|----------|
| `scripts/pipeline.py` | `run_strategies()` 函数 |
| `scripts/pipeline.py` | `dag_task_strategy()` 函数 |
| `scripts/pipeline.py` | `NODE_FN_MAP['strategy']` |
| `crawler/daily_crawl.py` | 旧策略执行代码块 (约 60 行死代码) |
| `app/db/schema.py` | DAG_CONFIG 中 `strategy` 节点 |
| `app/db/schema.py` | DEFAULT_STRATEGY_CONFIG 中旧策略条目 |
| `app/api/signals.py` | 旧 `/buy_signals` 端点 (改为查 model_signal) |

### 6.4 前端改动

| 组件 | 改动 |
|------|------|
| SignalsView.vue | 删除「生成」按钮 (不再触发 strategy 节点) |
| DetailView.vue | 信号卡片显示模型版本号 |
| DagView.vue | DAG 图移除 strategy 节点 |

---

## 七、DAG 拓扑变更

### 变更前

```
dag_config 中: strategy (sort=7, deps=fund) → model_signal (sort=13, deps=indicator_incr)
```

### 变更后

```
dag_config 中: 删除 strategy 节点
              indicator_incr (sort=10) → model_signal (sort=13) → model_health (sort=14)
              所有节点通过 daily_update 链自动触发
```
