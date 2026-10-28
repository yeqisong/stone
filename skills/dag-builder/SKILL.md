---
name: dag-builder
title: DAG 流程搭建
description: 在平台里新建/调整定时流程——选节点、排依赖、配 cron、草稿校验与试跑。用户要「每天/每周自动跑 X」「把这几步串成一个流程」时使用。
---

# DAG 流程搭建

## 何时用
用户要求自动化：定时采集、盘后信号、盘后总结、补数、备份、新闻/雪球相关流程。

## 节点清单（node_name → 作用）
- `cron` 定时触发入口（也可以不建它，直接给流程配 cron_expr）
- 行情：`kline`（个股日K）、`index`、`etf`、`fund`（基本面）
- 衍生：`factor_heal`（复权因子自愈）、`feature_compute`（特征计算）、`feature_backfill`
- 模型：`model_signal`（信号）、`model_health`（健康度+前瞻回填）、`paper_portfolio`（纸面组合）、
  `rolling_retrain`（滚动重训，谨慎）、`factor_ic`
- 数据质量：`stats`（统计缓存）、`daily_completeness`（完整度）、`entity_stats`、`analyze`（ANALYZE）
- 拓展采集：`moneyflow`、`margin_daily`、`top_list`、`margin_detail`、`moneyflow_hsgt`、
  `block_trade`、`share_float`、`repurchase`、`dividend`、`forecast`、`express`、`index_weight`、`fina_daily`
- 资讯：`news_crawl`、`news_summary`、`xueqiu_sentiment`
- 运维：`backup_qiniu`、`stock_master`、`data_backfill`

## 依赖规则（必须遵守，否则创建会被校验拒绝）
1. 无环；2. 至少一个入口节点（deps 为空）；3. 节点名必须在上面清单里；
4. 拓扑顺序要符合数据依赖：
   - `feature_compute` 必须在 `kline` 之后；若有 `factor_heal`，它要在 `kline` 后、`feature_compute` 前
   - `model_signal` 在 `feature_compute` 之后
   - `paper_portfolio` 在 `model_signal` 之后
   - `news_summary` 在 `news_crawl` 之后
   - `stats` 一般在行情类节点之后

## 创建方式
调 `create_dag_flow`：
```json
{
  "flow_name": "盘后信号与纸面",
  "description": "每天 18:00 生成信号并更新纸面组合",
  "cron_expr": "0 18 * * *",
  "nodes": [
    {"node_name": "model_signal", "deps": []},
    {"node_name": "paper_portfolio", "deps": ["model_signal"]}
  ]
}
```
- 创建结果是 **draft**，不会自动运行；发布需要在 DAG 页人工确认（或明确要求后再调 publish 相关动作）。
- 试跑用 `run_dag_flow(flow_id, trade_date)`，注意同流程互斥（已有任务运行时会 429）。

## cron 写法
五段式（分 时 日 月 周），服务器时区为北京时间：
- 每天 17:00 `0 17 * * *`
- 每 20 分钟 `*/20 * * * *`
- 工作日 9:35 `35 9 * * 1-5`
- 每周一 8:00 `0 8 * * 1`

## 输出格式
1. 流程名 + cron + 一句话用途
2. 节点/依赖表（节点、依赖、作用）
3. 创建结果：flow_id、状态（draft）、下一步（去 DAG 页发布 / 我可以现在试跑一次）
4. 若校验失败：原样给出错误并修正后再建，不要反复试同一份结构。

## 注意
- 涉及 `rolling_retrain`、`backfill`、`backup_qiniu` 这类重任务的流程，先说明资源/配额影响再建。
- 不要创建会与现有流程重复的定时任务（先 `list_dag_flows` 看已有的）。
