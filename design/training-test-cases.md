# 训练回测 — 测试用例文档

> v1.0 | 2026-06-23

---

## 一、Mock 数据构造

### 1.1 最小可用数据集

```python
# 5只股票 × 200天 = 1000行，满足 train>1000 的最低要求
import pandas as pd
import numpy as np

np.random.seed(42)
stocks = ['000001','000002','000003','000004','000005']
dates = pd.date_range('2024-01-01', periods=200, freq='B')
data = []
for code in stocks:
    base = np.random.randn() * 0.3
    for i, d in enumerate(dates):
        close = 10 + base + np.cumsum(np.random.randn(200)*0.02)[i]
        data.append({
            'trade_date': str(d.date()),
            'stock_code': code,
            'pct_b': np.random.randn()*0.5,
            'width': abs(np.random.randn())*0.1,
            'dif': np.random.randn()*0.3,
            'dea': np.random.randn()*0.3,
            'hist': np.random.randn()*0.1,
            'rsi': np.random.uniform(20,80),
            'atr': abs(np.random.randn())*0.5+0.5,
            'ma5': close+np.random.randn()*0.1,
            'ma20': close+np.random.randn()*0.2,
            'vol_ratio': np.random.uniform(0.5,2),
            'obv': np.random.randn()*1000,
            'close': max(close, 1.0),  # 保证 >0
        })

df = pd.DataFrame(data)
# 标签：5/10/20 日 forward 收益（随机漫步）
df['target_5d'] = np.random.randn(len(df))*0.02
df['target_10d'] = np.random.randn(len(df))*0.03
df['target_20d'] = np.random.randn(len(df))*0.04
```

### 1.2 边界数据集

| 场景 | 描述 | 预期行为 |
|------|------|------|
| 空数据集 | 0 行 | 返回 "指标数据不足(0行)"，状态回退 DRAFT |
| 最小数据集 | 刚好 5000 行 | 正常训练 |
| 单只股票 | 1 只 × 5000 天 | `max_positions=5` 时只能开 1 仓 |
| 全部停牌 | close 全为 NaN | `dropna` 后数据不足 5000 |
| 极端收益 | 标签 ±50% | winsorize 截断到 1%~99% |

---

## 二、测试用例

### TC-01: 数据加载

| 项 | 内容 |
|----|------|
| **前置** | 指标表有空数据列 |
| **操作** | 触发训练 |
| **期望** | JOIN 7 表成功，行数 >5000 |

| TC-01a | 某张指标表为空 | JOIN 后行数 <5000，返回失败 |
| TC-01b | `max(trade_date) > today-2` | 断言失败，返回 "数据新鲜度异常" |

### TC-02: 标签计算

| 项 | 内容 |
|----|------|
| **前置** | daily_quote 有完整 forward 数据 |
| **操作** | 计算 target_5d/10d/20d |
| **期望** | 标签值域在 [-0.5, 0.5] 经 winsorize 后 |

| TC-02a | close=0 或 close_hfq=0 | 除零 → None → dropna 过滤 |
| TC-02b | forward 日期无数据（股票停牌） | fwd_price=None → 该行标签 None |
| TC-02c | 极端涨停 10% | winsorize 不处理单日 10%（正常范围） |

### TC-03: 三重切分

| 项 | 内容 |
|----|------|
| **前置** | 200 个交易日 |
| **操作** | 60/20/20 切分 |
| **期望** | train≈120天, val≈40天, test≈40天 |

| TC-03a | 总天数 < 100 | train<60 天 → 报错 "数据量不足" |
| TC-03b | Optuna 访问 test_mask | 函数闭包隔离，无 test 数据变量在 objective 作用域 |

### TC-04: 回测引擎

| 项 | 内容 |
|----|------|
| **前置** | initial_cash=1M, max_positions=5, stop_loss=8% |
| **操作** | 单周期 5d 回测 |
| **期望** | equity_curve 单调递增或递减，无 NaN |

| TC-04a | 资金不足 | 单只股价 > cash_per_position → 0 shares → 跳过 |
| TC-04b | 止损触发 | 持仓跌 8% → 次日平仓 → trade_count+1 |
| TC-04c | T+1 约束 | 今日买入的仓位 hold_dur=0，次日才能卖 |
| TC-04d | 已持仓不再买 | held_codes 过滤 → 不在 top 列表中出现 |
| TC-04e | 全部已持仓 | slots=0 → 不开新仓，仅更新权益 |
| TC-04f | A 股整手 | 股价 50，资金 1000 → shares=int(20/50/100)*100=0 → 跳过 |
| TC-04g | 最终清仓 | 回测结束日，所有持仓按当日价卖出 |

### TC-05: Optuna

| 项 | 内容 |
|----|------|
| **前置** | n_trials=10 |
| **操作** | 触发训练 |
| **期望** | 每轮返回不同夏普（参数敏感） |

| TC-05a | 某次预测全 NaN | objective 返回 `-np.inf`（需加防护）|
| TC-05b | early_stopping 触发 | eval_set 上 loss 不再下降 → 提前停止 |
| TC-05c | reg_alpha/lambda 生效 | 参数文件含这两个字段 |

### TC-06: 存储

| 项 | 内容 |
|----|------|
| **前置** | 训练完成 |
| **操作** | 检查 model_versions |
| **期望** | sharpe≠0, win_rate∈[0,1], max_dd∈[0,1] |

---

## 三、运行测试

```bash
# 本地先用最小 Mock 数据测回测函数
python3 -c "
import pandas as pd, numpy as np
# 构造 5只×200天 mock 数据
np.random.seed(42)
codes = ['000001']*200 + ['000002']*200
dates = [str(d.date()) for d in pd.date_range('2024-01-01',periods=200,freq='B')]*2
close = np.abs(np.random.randn(400)*2+10)
y_true = np.random.randn(400)*0.02
y_pred = np.random.randn(400)*0.01

# 模拟回测
from scripts.pipeline import dag_task_model_train  # 不可直接调，需mock DB
print('Mock data ready, test framework setup pending')
"
```
