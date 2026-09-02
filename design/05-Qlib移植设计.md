# 05 - Qlib 移植设计（回测引擎分层 + 组合构建 + 绩效报告 + 因子库）

> 版本：v1.1（2026-09-01）
> 依据：微软 Qlib 源码精读（`/tmp/qlib-src`，commit 79633dd，2026-07）+ 本系统现状对照
> 原则：**不整体引入 Qlib**（其数据层依赖 .bin 文件缓存与自研存储，与本系统 PostgreSQL 宽表架构冲突），只移植其经过验证的设计模式，用本系统既有基建（feature_values 宽表、dag_flows、KEPL）落地。
>
> **✅ 已全部实施完成**（M1~M7，随 v3.7.0 提交打 tag，commit bde3371f，2026-09-01）。实施结果与设计偏差见 §7.1。

---

## 1. 现状与缺口对照

### 1.1 本系统已具备（与 Qlib 等价或更强）

| 能力 | 本系统实现 | Qlib 对应 |
|---|---|---|
| 因子表达式 DSL | KEPL（含跨股票函数 avg/sum/rank/zscore） | ExpressionEngine（Ref/Mean/Std/Corr 等） |
| 特征存储 | feature_values 宽表（3.9 亿行，float32） | Qtables .bin 列存缓存 |
| 工作流编排 | dag_flows 可视化 DAG + TaskManager | qrun YAML + Experiment |
| 滚动窗口检验 | run_walk_forward + promotion_gate | RollingGen |
| 预测入口统一 | predict_for_version（scan/attribution/permutation/WF/model_health 共用） | ModelSignal |
| 训练/推理一致性 | feature_norm=cs_rank 推理端逐日截面 rank 对齐 | DataHandlerLP learn/infer 处理器分离 |
| 标签语义 | label_mode=excess（N 日收益 − 沪深300） | Ref($close,-2)/Ref($close,-1)-1（T+1 收益） |
| 涨跌停/T+1/成本 | limit_flags + _simple_backtest 内嵌约束 | Exchange（limit_buy/limit_sell/suspended） |
| 模型评估 | IC 体检 + 置换检验 + 归因 + walk-forward 门禁 | risk_analysis + long_short_backtest |
| 鲁棒性验证 | 归因三基线（ideal/random/real） | —（本系统更细） |

### 1.2 精读确认的缺口（本次移植目标）

1. **回测引擎是单体函数**：`_simple_backtest`（scripts/pipeline.py:590）把选股、交易、净值揉在一起；`paper_day_step`（pipeline.py:971）是**第二套独立实现**——两套规则同源但各自维护，已出现行为漂移风险（如 paper 用 cfg 字典、backtest 用硬编码参数）。Qlib 用 Strategy/Portfolio/Exchange/Executor 四层解耦，一套内核两处复用。
2. **无组合构建层**：买入=按预测分排序取 top slots，没有"目标仓位→换血"概念；Qlib 的 TopkDropout 提供持仓数量固定 + 每日换血 n_drop + **合并排序防高卖低买** + 最少持有天数 hold_thresh。
3. **无执行质量指标**：Qlib 逐单记录 FFR（成交率）/PA（价格优势）/POS（胜率）；本系统只有成交结果，无法区分"策略差"还是"执行差"。
4. **绩效报告缺 IR/alpha/beta**：Qlib risk_analysis 提供信息比率 + 年化 + 最大回撤（sum/product 两种累计模式）；本系统 model_health 有 sharpe/IC 但无 IR（ICIR 已有，组合层 IR 没有）。
5. **冲击成本模型粗糙**：Qlib Exchange 的 `impact_cost × (trade_val/总量)²` 平方模型 + 成交量裁剪（vol_limit） + min_cost 最低佣金；本系统固定滑点 0.001。
6. **因子库未体系化**：本系统 31 个特征（23 价量 + 8 基本面）人工挑选；Qlib Alpha158 提供 158 个分组建模因子（kbar 形态/价格比/量比/滚动统计），全部用 $close 归一化 + 1e-12 防除零。
7. **无集成鲁棒化**：Qlib DoubleEnsemble（样本重加权 + 特征选择双集成）对噪声环境（A 股特征）有实证价值。

---

## 2. 移植设计总览

```
                    ┌─────────────────────────────────────────────┐
                    │            strategy/（新目录）                │
                    │  signal.py     —— 预测信号源（读宽表/预测）    │
                    │  strategy.py   —— 策略：信号→订单列表          │
                    │    TopkDropoutStrategy（组合构建层）          │
                    └──────────────────┬──────────────────────────┘
                                       │ TradeDecision(orders)
                    ┌──────────────────▼──────────────────────────┐
                    │          backtest/（新目录）                  │
                    │  exchange.py    —— 撮合：涨跌停/停牌/T+1/成本  │
                    │                  冲击成本/成交量裁剪/延迟结算   │
                    │  executor.py    —— 执行器：订单→成交记录       │
                    │  account.py     —— 账户：现金/持仓/净值/换手    │
                    │  report.py      —— 绩效：IR/alpha/beta/回撤    │
                    │  engine.py      —— 主循环（幂等、可断点）       │
                    └──────────────────┬──────────────────────────┘
                                       │
        ┌──────────────────────────────┼──────────────────────────┐
        ▼                              ▼                          ▼
  _simple_backtest 兼容层        dag_task_paper_portfolio     run_attribution /
  （旧评估入口，行为不变）       （纸面组合改走新内核）        strategy_scan / WF
```

**核心决策**：
- 新内核 `strategy/backtest/` 为**纯 Python 无框架依赖**模块（numpy/pandas 即可），以 dataclass 传递数据，方便单测。
- `_simple_backtest` **保留不动**（兼容历史 evaluation_report 口径），新引擎作为 v2 并行上线，评估入口逐步切换（对比实验通过后）。
- `paper_day_step`/`dag_task_paper_portfolio` 迁移到新内核（同一套 Exchange 约束，消除双实现漂移）。

---

## 3. 模块设计

### 3.1 撮合层 `backtest/exchange.py`（移植 Qlib Exchange）

```python
@dataclass
class TradeConfig:
    comm: float = 0.00025        # 佣金（双边）
    st_tax: float = 0.001        # 印花税（卖出）
    slip: float = 0.001          # 固定滑点（买卖各半）
    impact_cost: float = 0.0     # 冲击成本系数（Qlib 默认 0，可选启用平方模型）
    min_cost: float = 5.0        # 最低佣金
    vol_limit: Optional[float] = None   # 成交量上限比例（如 0.05 = 当日成交量 5%）
    deal_price: str = "close"    # 成交价基准：close / vwap / open
    forbid_all_trade_at_limit: bool = True  # 涨停完全禁买（含允许卖涨停的开关）
```

关键行为（对齐 Qlib exchange.py 精读结论）：

1. **可交易性** `is_tradable(code, date, direction)`：
   - 停牌：当日无行情行（close 缺失/0）→ 不可交易
   - 涨跌停：`limit_flags()` 复用现有实现；`direction=BUY` 时涨停禁买，`direction=SELL` 时跌停禁卖；`forbid_all_trade_at_limit=True` 时涨停/跌停双向全禁（Qlib 默认，更保守）
2. **成交价**：默认 close；若启用 `deal_price=vwap`，从 daily_quote 的 amount/volume 派生（amount×1000/volume，注意单位）
3. **成交量裁剪**（可选，Qlib `_clip_amount_by_volume`）：买入股数 ≤ 当日成交量 × vol_limit；累计已成交从当日预算中扣除（`dealt_order_amount` 字典跨订单记账）
4. **冲击成本**（可选）：`cost_impact = impact_cost × (trade_val / 当日总成交额)²`，大单非线性惩罚
5. **最低佣金**：`fee = max(comm × trade_val, min_cost)`
6. **延迟结算**（T+1 现金）：卖出所得现金进入 `cash_delay`，**当日不可用于买入**，次日 `settle_commit()` 到账（Qlib Position settle_start/commit 语义；本系统 paper 现在是卖出后立刻可用——这是行为修正点，需在迁移时对比净值差异）

### 3.2 组合构建 `strategy/strategy.py`（移植 TopkDropout）

```python
class TopkDropoutStrategy:
    def __init__(self, topk: int = 5, n_drop: int = 1,
                 method_sell: str = "bottom", method_buy: str = "top",
                 hold_thresh: int = 1, risk_degree: float = 0.95):
```

每日决策（对齐 qlib/contrib/strategy/signal_strategy.py:138-295）：
1. 取当日信号（宽表已含预测分；T 日信号 T+1 执行——`shift=1` 窗口语义）
2. **候选买入**：信号 top `(n_drop + topk − 当前持仓数)` 名
3. **合并排序**：`pred.reindex(持仓 ∪ 候选).sort_values(desc)` —— 卖出名单 = 合并集中末 n_drop 名（**杜绝卖掉高分买低分**，这是本系统现实现没有的）
4. **卖出过滤**：持仓天数 < hold_thresh 不卖（`count_*` 由引擎维护）；跌停/停牌不卖
5. **买入预算**：`现金 × risk_degree / 买入数` 等额分配；整手 100 股；现金约束
6. 输出订单列表（含方向/股数/原因），交给 Executor 撮合

与现实现的差异点：
- 现实现：卖出由 止损/止盈/trailing/到期 触发（**事件驱动**）；TopkDropout 是**定期换血**（周期驱动）。两者不互斥：新策略 = 事件驱动退出（保留）+ 周期换血（新增），组合持仓数量从"≤max_pos"变为"固定 topk"
- `hold_thresh` 防止新买入次日被换掉（配合 T+1 天然成立）

### 3.3 执行器 `backtest/executor.py`

- 输入：当日订单列表 + 行情快照；输出：成交记录（含 deal_price/deal_amount/fee/ffr）
- **先卖后买**（现金约束天然满足，与现实现一致）
- 逐单记录执行指标：`ffr`（成交率=实际/目标）、`pa`（价格优势=成交价 vs 收盘价）、`pos`（该单是否盈利）——落到 `paper_trades` 新列或新表 `execution_stats`

### 3.4 账户与净值 `backtest/account.py`

- 持仓 dict：`{code: {amount, price, weight, count_days, buy_date, buy_price, peak, cost_basis}}`
- 逐日记录：账户值/现金/持仓市值/换手率（当日成交额/账户值）/成本——对齐 Qlib PortfolioMetrics 列（account/return/total_turnover/turnover/cost/value/cash/bench）
- **净值曲线与基准同表**：bench 用沪深300（index_daily_quote 已有）

### 3.5 绩效报告 `backtest/report.py`（移植 risk_analysis）

```python
def risk_analysis(rets: pd.Series, N: int = 238, mode: str = "sum") -> dict
# 输出：mean/std/annualized_return/information_ratio/max_drawdown
# mode=sum：算术累计（Qlib 默认，避免指数复合偏差）；product：几何
```

新增指标（写入 model_health / evaluation_report）：
- **信息比率 IR** = mean/std×√N（N=238 交易日）
- **excess sharpe / excess 年化**：净值 − 沪深300 同期收益后重算
- **alpha/beta**：对沪深300 日收益做一元回归（`beta = cov(r, rb)/var(rb)`，`alpha = mean(r) − beta×mean(rb)`），年化 alpha = alpha×N
- **换手率**：日均换手 = Σ|成交额|/账户值/天数
- **win_rate 细化**：Qlib pos（按单盈利比例）vs 现 win_rate（按交易笔数）并存

### 3.6 主循环 `backtest/engine.py`

```
for d in trade_days:
    signal = signal_source.get(d)            # 宽表预测分（T 日）
    decision = strategy.generate(d, signal)  # 订单列表
    fills = executor.execute(d, decision)    # 撮合（先卖后买、约束）
    account.settle_commit(); account.mark(d) # 延迟结算 + 记账
```

**幂等/可断点**（生产级要求）：
- 引擎以 `(start_date, end_date)` 区间运行，逐日 commit 到 `paper_trades`/`backtest_records`（EOD 行）
- 已有 EOD 的日期跳过（复用现 dag_task_paper_portfolio 的幂等键 `action='EOD'`）
- 中途失败：回滚当日事务，重跑续跑

### 3.7 兼容层

- `_simple_backtest` 保留，新增薄封装 `backtest_v2_metrics(...)`：新引擎结果 → 旧 evaluation_report 字段格式（sharpe/max_dd/win_rate/total_return），保证 ModelEval 页面零改动可对比
- 评估入口（run_attribution/strategy_scan/run_walk_forward/permutation）增加 `engine: "v1"|"v2"` 参数，默认 v1，灰度切换 v2

---

## 4. 因子库扩充设计（Alpha158 式，KEPL 落地）

### 4.1 因子组（全部可用 KEPL 表达，从 Qlib loader.py 直接翻译）

| 组 | 因子 | KEPL 示例（本系统语法） |
|---|---|---|
| K 线形态（9） | KMID/KLEN/KMID2/KUP/KUP2/KLOW/KLOW2/KSFT/KSFT2 | `(close-open)/open`、`(high-low)/open`、`(2*close-high-low)/(high-low)` |
| 价格比（5×windows） | OPEN/HIGH/LOW/CLOSE/VWAP 相对最新 close | `Ref(close, d)/close`（KEPL 需补 Ref 算子，见下） |
| 量比（5×windows） | VOLUME(d) | `Ref(volume, d)/volume` |
| 滚动统计（16 类 × 5 窗） | ROC/MA/STD/BETA/RSQR/RESI/MAX/MIN/QTLU/QTLD/RANK/RSV/IMAX/IMIN/IMXD | `ma(close,20)/close`、`std(close,20)/close`、`hhv(high,20)/close` 等 |

要点（对齐 Qlib 设计）：
- **全部用最新 close 归一化**（去单位、跨股票可比），除零保护 `+1e-12`
- windows = [5, 10, 20, 30, 60]（与现有 atr_14/pct_5d/pct_20d 窗口体系兼容）
- KEPL 缺失算子需补：`Ref`（含负偏移=未来，显式语义防 shift 方向 bug）、`hhv/llv`（rolling max/min）、`quantile`、`rsquare/resi/slope`（线性回归）——新增算子按现有 registry 模式注册
- 入库：features 表批量 INSERT（status=enabled），公式由生成器产出（`_gen_alpha158_formulas()`），**一次生成 100+ 条，经 IC 体检流水线筛选后启用**（traffic light + greedy_dedup 已有）

### 4.2 标签统一

- 新因子训练统一用现有 label_mode=excess + horizon ∈ {5,10,20}，与 v11 一致

---

## 5. 模型侧：DoubleEnsemble 实验（v12 候选）

移植自 qlib/contrib/model/double_ensemble.py（精读结论）：

```
循环 k ∈ [1..num_models]:
  1. 用当前样本权重 weights + 特征子集 features 训练子模型（XGBoost，early_stopping）
  2. 从 booster 取训练 loss 曲线（每轮迭代的样本误差矩阵 N×T）
  3. 集成预测 = Σ(sub_weights × pred_k)/Σsub_weights
  4. sample_reweight：
     h1 = rank(-集成损失)；h2 = rank(首 10% 迭代误差均值 / 末 10% 迭代误差均值)
     h = alpha1×h1 + alpha2×h2；按 h 分箱（bins_sr）
     weights = 1 / (decay^k × h_avg(bin) + 0.1)
  5. feature_selection：按特征与损失的相关系数排序取 ceil(sample_ratios × F)
```

落地要点：
- XGBoost 有 `evals_result` 可取每轮验证损失，但**训练集逐样本 loss 曲线**需要 `xgb.train` 的 `pred_leaf`/自定义目标或改用 LightGBM（lgb 原生返回 loss 曲线，Qlib 也是 lgb）——**建议本系统实验用 LightGBM 实现**（与 XGBoost 并行，不替换）
- 参数默认（Qlib）：num_models=5, alpha1=0.6, alpha2=0.4, bins_sr=30, decay=0.1, sample_ratios=(0.7,0.7,0.7,0.6,0.6), enable_sr=True, enable_fs=True
- 训练任务走现有 dag_task_model_train 框架，产物同样落 model_versions（v12.0 实验版），走同一套 walk-forward 门禁

---

## 6. 数据表/API 变更

### 6.1 表变更

| 表 | 变更 |
|---|---|
| `paper_trades` | + `ffr`、`pa`（执行质量）、`engine`（v1/v2 标识）、`strategy`（topk_dropout/signal）列 |
| `backtest_records` | + `engine`、`strategy`、`params` JSONB（topk/n_drop/hold_thresh） |
| `model_health` | + `ir`、`excess_sharpe`、`alpha`、`beta`、`annual_turnover` 列 |
| `features` | + 100+ 条 Alpha158 因子（批量生成器写入） |
| `execution_stats`（新表） | 逐日执行质量聚合：ffr/pa/pos 按日、按模型 |

### 6.2 API 变更

| 端点 | 变更 |
|---|---|
| `POST /api/v1/models/{v}/backtest`（新） | 参数化回测：engine/strategy/topk/n_drop/val 范围 → 返回报告 + 交易明细 |
| `POST /api/v1/models/{v}/strategy-scan` | + engine 参数（v2 可用） |
| `GET /api/status/paper` | + 执行质量指标 |
| `GET /api/v1/models/{v}` | evaluation_report + ir/alpha/beta 新字段 |

---

## 7. 实施里程碑

| 阶段 | 内容 | 验收标准 |
|---|---|---|
| **M1 撮合内核** | backtest/exchange + account + engine v2（纯函数） | 单测：涨跌停/T+1/延迟结算/最低佣金/冲击成本 全绿；用 v9.0 验证集跑 v2，与 _simple_backtest 净值曲线对比（差异 < 5% 或逐项解释） |
| **M2 策略层** | TopkDropoutStrategy + signal 接入 predict_for_version | 单测：合并排序防高卖低买、hold_thresh、n_drop 换血；参数扫描支持 topk/n_drop |
| **M3 纸面组合迁移** | dag_task_paper_portfolio 换新内核；旧 paper_day_step 删除 | 回放 2026 信号历史，净值与旧实现逐日核对（延迟结算修正点需用户确认口径） |
| **M4 绩效报告** | risk_analysis 移植 + model_health 新列 + 前端展示 | 页面显示 IR/excess/alpha/beta/换手；与手工计算对照 |
| **M5 因子库** | KEPL 补算子（Ref/hhv/llv/quantile/回归系）+ Alpha158 生成器 + 入库 | 100+ 因子进 features；IC 体检跑通；greedy_dedup 给出精选集 |
| **M6 DoubleEnsemble 实验** | LightGBM 版 DE 训练任务（v12 实验） | 与 v11 同区间 walk-forward 对比 |
| **M7 切换** | 评估入口默认 engine=v2（对比通过后）；打 tag v3.7.0 | 全量回归：tests/ 通过 + 既有模型评估数字不变（v1 兼容层） |

### 7.1 实施结果（v3.7.0，2026-09-01 交付）

| 里程碑 | 结果 | 验收 |
|---|---|---|
| M1 撮合内核 | ✅ strategy/backtest/{models,exchange,account,engine}.py | 单测 15 全绿；v9.0 验证集 173 点净值曲线与 v1 **逐日 0 差异** |
| M2 策略层 | ✅ TopkDropoutStrategy（strategy/strategy.py） | 单测 7 全绿（合并排序/hold_thresh/n_drop）；扫描支持 topk/n_drop（前端引擎选择入口） |
| M3 纸面迁移 | ✅ dag_task_paper_portfolio 换 v2 内核；paper_day_step 删除 | 173 日回放终值与旧实现**精确到分一致**；种子账户 + EOD 幂等续跑 |
| M4 绩效报告 | ✅ report.py + model_health 新列 ir/alpha_annualized/beta/excess_annualized/turnover_daily | 8 项指标手工对照全一致；ModelEval 绩效指标条 |
| M5 因子库 | ✅ KEPL 补 14 算子 + 负字面量语法；alpha158.py 生成 **114 因子**入库 | IC 体检：绿 75 / 黄 20 / 红 19；greedy_dedup 精选 41；2000→今全量回补进行中 |
| M6 DoubleEnsemble | ✅ strategy/models/double_ensemble.py（LightGBM）+ train_de.py | v12.0 PENDING：WF 3/4 sharpe 胜但 RankIC 未达标 FAIL（保持 PENDING 不激活） |
| M7 切换 | ✅ `_backtest` 统一入口默认 engine=v2（v1 口径兼容 dict）；tag v3.7.0 | 单测 118 全绿；显示精度内既有评估数字不变 |

**与设计的偏差**（实施中的务实取舍）：
1. **executor.py 未独立成模块**——撮合循环并入 engine.py（两轮：先卖后买；BUY 逐笔立即入账保持 v1 现金递减语义）。拆分收益不抵间接层成本。
2. **execution_stats 表未建**——逐单 ffr/pa 执行质量指标暂未落库（成交明细在 backtest_trades + 内存 records）；vol_limit/impact_cost 参数已实现但默认关闭。
3. **paper_trades 未加 ffr/pa/engine/strategy 列**——v2 标识经 detail JSONB 承载，表结构未动。
4. **`POST /{v}/backtest` 参数化回测端点未建**——strategy-scan（含 engine/strategy/topk/n_drop 参数）已覆盖该需求。
5. **model_health 列名**：设计的 excess_sharpe/annual_turnover 实际落为 excess_annualized/turnover_daily。
6. **Alpha158 为 114 因子**（非 158）：剔除需要 high/low 盘中价以外的Fundamental 组与重复窗口组；价格/量比/滚动统计三族全量保留。
7. **M5 附带产物**：算子目录单一事实源 `GET /api/kepl/functions`（32 算子 `_OPERATOR_DOCS` 强校验）+ AI 上下文动态化 + Monaco 补全 + 函数页内置算子合并展示（48 条 = 32 算子 + DB 函数）。

---

## 8. 生产级要求清单

1. **幂等**：回测/纸面/训练任务可断点续跑（EOD 幂等键已设计）
2. **可观测**：engine v2 每步日志（订单/成交/拒绝原因：涨停/停牌/现金不足/最低佣金）+ execution_stats 落库
3. **测试**：每个模块独立单测（纯函数设计）；关键行为对照用例（Qlib 语义逐条）：延迟结算、vol_limit 裁剪、impact 平方模型、hold_thresh、合并排序
4. **兼容**：v1 引擎与 evaluation_report 历史口径不变；新列可空，前端向后兼容
5. **性能**：v2 引擎向量化（每日操作 ≤ 5 万行宽表切片）；单次回测 ≤ 30s（与 v1 同级）
6. **数据安全**：因子批量入库前 dry-run 校验公式可执行；异常公式自动禁用（现有 status 机制）

## 9. 风险与对策

| 风险 | 对策 |
|---|---|
| v2 与 v1 净值差异引起口径混乱 | 差异逐项可解释（延迟结算、最低佣金、deal_price）；ModelEval 双引擎对比视图 |
| 延迟结算修正纸面组合历史 | 迁移日记录口径变更说明；不回溯历史（EOD 从迁移日起重放） |
| Alpha158 因子大量冗余 | IC 体检 + greedy_dedup 流水线先行；分批启用（每批 ≤ 30 个） |
| DoubleEnsemble 训练耗时 | num_models 可配、GPU 优先；与 v11 同资源对比 |
