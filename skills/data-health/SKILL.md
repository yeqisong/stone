---
name: data-health
title: 数据健康巡检
description: 巡检行情/特征/资讯/雪球/信号的数据新鲜度与缺口，定位「页面没数据/统计停更」的根因。用户问数据是否正常、某模块停摆、补数进度时使用。
---

# 数据健康巡检

## 何时用
- 「今天数据采到了吗」「状态页某个模块停摆」
- 「特征/资讯/雪球怎么不更新了」
- 补数后确认是否补齐

## 巡检顺序（先整体，再定位）

1. **各表最新日期与量**（一条 SQL 看全）：
   ```sql
   SELECT 'daily_quote' t, MAX(trade_date)::text latest, COUNT(*) FILTER (WHERE trade_date >= CURRENT_DATE - 7) n7 FROM daily_quote
   UNION ALL SELECT 'index_daily_quote', MAX(trade_date)::text, COUNT(*) FILTER (WHERE trade_date >= CURRENT_DATE - 7) FROM index_daily_quote
   UNION ALL SELECT 'feature_values', MAX(trade_date)::text, NULL FROM feature_values
   UNION ALL SELECT 'signal_history', MAX(signal_date)::text, COUNT(*) FILTER (WHERE signal_date >= CURRENT_DATE - 7) FROM signal_history
   UNION ALL SELECT 'news_item', MAX(published_at)::text, COUNT(*) FILTER (WHERE published_at >= NOW() - INTERVAL '1 day') FROM news_item
   ```
2. **当日完整度**：
   ```sql
   SELECT trade_date, stock_rows, stock_baseline,
          ROUND(stock_rows * 100.0 / NULLIF(stock_baseline, 0), 1) AS stock_pct,
          index_rows, etf_rows
   FROM daily_completeness ORDER BY trade_date DESC LIMIT 7
   ```
3. **DAG 节点最近运行**（最重要：区分「没跑」和「跑了失败」）：
   ```sql
   SELECT trade_date, node_name, status, rows, LEFT(detail, 100), finished_at
   FROM dag_run_log
   WHERE trade_date >= CURRENT_DATE - INTERVAL '3 days'
   ORDER BY id DESC LIMIT 60
   ```
4. **补数任务**：
   ```sql
   SELECT task_id, task_type, status, stocks_done, stocks_total, rows, error_message
   FROM backfill_tasks ORDER BY created_at DESC LIMIT 10
   ```
5. **资讯/雪球源健康**（各自状态表）：
   ```sql
   SELECT source, last_fetched_at, last_ok, fail_streak, note FROM news_crawl_state ORDER BY source
   ```
   雪球常驻采集看 `xueqiu_runtime` / `/api/xueqiu` 心跳（心跳超过 10 分钟即停摆）。

## 常见根因对照
| 现象 | 先查 | 典型原因 |
|---|---|---|
| 行情没更新 | dag_run_log 的 kline 节点 | tushare 配额耗尽 / 非交易日 / 数据源连接失败 |
| 信号为 0 但节点成功 | model_signal 节点 detail | 空仓闸门（沪深300 < MA20）禁买，属设计内 |
| 特征不更新 | feature_compute 节点 | 上游 kline 失败 / 特征补数任务在跑 |
| 新闻/雪球停 | 状态表与心跳 | 长驻进程崩溃 / IP 软封禁 / 论坛接口 405 |
| 统计卡片旧 | data_stats_cache.computed_at | stats 节点未跑，可在状态页手动刷新 |

## 输出格式
1. 结论：正常 / 局部延迟 / 停摆（附判断依据）
2. 一张表：数据域、最新日期、近 7 日量、状态
3. 根因链（节点 → 错误信息 → 影响面）
4. 修复建议（手动触发哪个节点 / 是否需要重启进程 / 是否需要人工介入）

## 注意
- 周末与节假日没有行情属正常，先用 `trade_calendar` 判断是否交易日。
- 不要直接改数据；修复一律走 DAG 节点、补数任务或状态页操作。
