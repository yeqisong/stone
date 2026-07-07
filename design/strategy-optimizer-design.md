# 策略参数优化引擎 — 开发设计文档

> 版本：v1.0 | 日期：2026-07-08 | 状态：设计中

---

## 1. 核心认知

模型参数（XGBoost）和策略参数（止盈止损）是**双塔**，两者并列，都需要被优化。

| 维度 | 模型超参数 | 策略交易规则 |
|------|-----------|-------------|
| 优化目标 | Val R² | 回测 Sharpe / 收益回撤比 |
| 参数类型 | learning_rate, max_depth... | stop_loss, take_profit, trailing_stop... |
| 计算成本 | 极高（需重训练 XGBoost） | 极低（模型固定，只重放逐日模拟） |
| 运行时间 | 分钟~小时 | 秒~几十秒 |

**结论**：可枚举成千上万种策略参数组合，几乎零算力成本。

---

## 2. 数据模型变更

### 2.1 model_versions 新增字段

```sql
ALTER TABLE model_versions ADD COLUMN train_params JSONB;         -- 模型训练参数快照
ALTER TABLE model_versions ADD COLUMN trading_rules JSONB;        -- 最优交易规则（验证集搜索得出）
ALTER TABLE model_versions ADD COLUMN strategy_scan_results JSONB; -- 策略扫描全部结果 [{params,sharpe,max_dd,...}]
ALTER TABLE model_versions ADD COLUMN test_performance JSONB;     -- 测试集性能（仅展示，不回传优化）
```

### 2.2 新表：strategy_scan_tasks

```sql
CREATE TABLE strategy_scan_tasks (
    task_id       VARCHAR(16) PRIMARY KEY,
    version       VARCHAR(16) REFERENCES model_versions(version),
    status        VARCHAR(16) DEFAULT 'pending',  -- pending/running/completed/failed
    total_combos  INTEGER DEFAULT 0,
    completed     INTEGER DEFAULT 0,
    best_params   JSONB,
    best_sharpe   DECIMAL(8,4),
    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    finished_at   TIMESTAMP
);
```

---

## 3. 嵌套验证架构（防止 Look-ahead Bias）

```
全量数据时间线:
|────── train ──────|── val_strategy ──|──── test ────|
  2016-01 ~ 2021-12    2022-01 ~ 2023-12   2024-01 ~ 今

阶段1: train 集 → 训练 XGBoost → 模型 .pkl
阶段2: val_strategy 集 → 搜索最优 trading_rules (仅在这段数据上)
阶段3: test 集 → 最终回测 (确定参数后只跑一次，结果写入 test_performance)
```

### model_versions.config 结构变更

```json
{
  "train_params": {
    "train_start": "2016-01-01", "train_end": "2021-12-31",
    "features": [...], "model_type": "xgboost"
  },
  "val_strategy_range": {
    "start": "2022-01-01", "end": "2023-12-31",
    "purpose": "策略参数搜索专用，严禁用于模型训练"
  },
  "test_range": {
    "start": "2024-01-01", "end": null
  },
  "trading_rules": {
    "stop_loss": 0.08, "take_profit": 0.15, "trailing_stop": 0.05,
    "max_positions": 5, "position_size": 0.2,
    "optimized_on": "val_strategy",
    "optimized_sharpe": 1.85
  },
  "test_performance": {
    "sharpe": 1.42, "max_dd": 0.18, "win_rate": 0.55,
    "evaluated_at": "2026-07-08"
  }
}
```

---

## 4. 策略参数扫描引擎

### 4.1 API

**`POST /api/models/versions/{version}/strategy-scan`**

```json
// Request
{
  "param_grid": {
    "stop_loss": [0.03, 0.05, 0.08, 0.10],
    "take_profit": [0.05, 0.08, 0.10, 0.15, 0.20],
    "trailing_stop": [0.03, 0.05, 0.08]
  },
  "val_range": {"start": "2022-01-01", "end": "2023-12-31"}
}
// Response
{ "task_id": "scan-a1b2", "total_combos": 60, "status": "started" }
```

**`GET /api/models/versions/{version}/strategy-scan/{task_id}`**

```json
{
  "task_id": "scan-a1b2", "status": "running",
  "completed": 45, "total_combos": 60,
  "best_so_far": {
    "stop_loss": 0.08, "take_profit": 0.15, "trailing_stop": 0.05,
    "sharpe": 1.85, "max_dd": 0.12, "win_rate": 0.58
  },
  "heatmap_data": [[0.03,0.05,0.8],[0.03,0.08,1.1],...]
}
```

### 4.2 后端扫描逻辑

```python
def run_strategy_scan(version, param_grid, val_start, val_end):
    # 1. 加载模型 .pkl（只加载一次）
    model = load_model(version)
    
    # 2. 加载验证集特征 + 标签（只加载一次，缓存到内存）
    df_val = load_features(val_start, val_end)
    daily_data = load_daily_quote(val_start, val_end)
    
    # 3. 模型预测（只跑一次，缓存预测值）
    predictions = model.predict(df_val[FEATURES])
    df_val['pred'] = predictions
    
    # 4. 笛卡尔积遍历策略参数
    combos = list(product(
        param_grid['stop_loss'],
        param_grid['take_profit'],
        param_grid.get('trailing_stop', [0])
    ))
    
    results = []
    for i, (sl, tp, ts) in enumerate(combos):
        bt = _backtest(
            df_val['target'].values, df_val['pred'].values,
            df_val['trade_date'], df_val['stock_code'],
            df_val['close'], df_val['volume'],
            hold_days=10, stop_loss=sl, take_profit=tp, trailing_stop=ts
        )
        results.append({
            'stop_loss': sl, 'take_profit': tp, 'trailing_stop': ts,
            'sharpe': bt['sharpe'], 'max_dd': bt['max_dd'],
            'win_rate': bt['win_rate'], 'total_return': bt['total_return']
        })
        # 每完成一个组合推送进度
        push_ws_progress(task_id, i+1, len(combos))
    
    # 5. 选最优
    best = max(results, key=lambda r: r['sharpe'])
    return best, results
```

### 4.3 性能优化

- 模型 .pkl 只加载 1 次
- 特征数据 + 预测值缓存在内存中
- 逐日模拟纯逻辑计算，每秒可跑数百种组合

---

## 5. 前端设计

### 5.1 策略参数扫描弹窗

- 在 ModelEval 回测页增加 **[策略参数扫描]** 按钮
- 弹窗内容：
  - 止盈阈值：多选 `[5%, 8%, 10%, 15%, 20%]` + 自定义
  - 止损阈值：多选 `[3%, 5%, 8%, 10%]` + 自定义
  - 移动止盈回撤：多选 `[3%, 5%, 8%]`
  - 验证集日期范围（自动从 config 读取）
  - 预计组合数显示

### 5.2 扫描结果页

- **热力图**：X轴=止盈, Y轴=止损, 颜色=夏普比率
- **帕累托散点图**：X轴=夏普, Y轴=最大回撤，点击选中
- **表格**：列出所有组合的详细指标，可按列排序
- **一键应用**：选中某组参数 → 写入 `model_versions.trading_rules`

---

## 6. 文件变更清单

| 文件 | 变更 |
|------|------|
| `app/db/schema.py` | model_versions 加 4 列 + strategy_scan_tasks 表 |
| `app/api/models.py` | strategy-scan API (POST + GET) |
| `scripts/pipeline.py` | `run_strategy_scan()` + `_backtest()` 支持 take_profit/trailing_stop |
| `web-v2/src/components/ModelEval.vue` | 策略扫描按钮 + 弹窗 |
| `web-v2/src/components/ModelScan.vue` | 新建：扫描结果热力图 + 表格 |
| `design/model-create-design.md` | 更新模型版本数据模型 |

---

## 7. 风险与注意事项

1. **Look-ahead Bias**：策略参数搜索**严格限定在验证集**，绝不触碰测试集
2. **过拟合风险**：验证集搜索出的参数可能在测试集表现平庸 → 热力图展示完整分布
3. **参数爆炸**：用户可能选太多值 → 限制最大组合数（如 500），超限提示减少候选值
