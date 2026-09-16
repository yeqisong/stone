"""DAG 节点类型 API（v2.0 重构 迭代 3.3 + 内部依赖子图）。"""
from fastapi import APIRouter, HTTPException
from scripts.pipeline import NODE_FN_MAP

router = APIRouter(prefix="/api/dag", tags=["dag"])

# 节点内部依赖子图定义（迭代 3.3 补齐）
NODE_SUB_STEPS = {
    'cron': [
        {'name': '交易日检查', 'desc': '查询 trade_calendar 判断今日是否为交易日'},
        {'name': '触发 daily_update', 'desc': '交易日则传播到后续节点'},
    ],
    'kline': [
        {'name': '检测交易日', 'desc': '校验目标日期是否为交易日'},
        {'name': '拉取行情', 'desc': 'tushare/baostock 下载日K线'},
        {'name': '写入 daily_quote', 'desc': '批量 INSERT ... ON CONFLICT UPDATE'},
    ],
    'factor_heal': [
        {'name': '近 7 日假跳变检测', 'desc': '后复权收益越界而原始价正常（真除权时原始价会同步下移）'},
        {'name': '按代码拉官方因子', 'desc': 'tushare adj_factor(ts_code) 全历史，1 配额/只'},
        {'name': '重写 close_hfq', 'desc': 'close × 官方因子，消除半修正窗口的跨界跳变'},
        {'name': '重算污染窗特征', 'desc': '受影响代码 × [边界, +90 天] 全部启用特征（DELETE 带代码过滤）'},
    ],
    'rolling_retrain': [
        {'name': '检查 ACTIVE 新鲜度', 'desc': 'trained_at 距今天数 vs 提醒/重训阈值'},
        {'name': '陈旧告警', 'desc': '超 warn_days 写 risk_alerts → WS 推前端铃铛/Chrome 通知（同日去重）'},
        {'name': '重训条件判定', 'desc': '无在途候选（TRAINING/待审 DRAFT）且不在冷却期'},
        {'name': '克隆起训', 'desc': '克隆 ACTIVE 配置后台训练（/train 同一入口），只落 DRAFT 待人工激活'},
    ],
    'margin_daily': [
        {'name': '缺失日扫描', 'desc': 'trade_calendar 对齐找 [start_date, 当日] 全部缺失交易日'},
        {'name': '最旧优先补数', 'desc': 'tushare margin_detail 按日全市场 1 次调用，断点续跑'},
        {'name': '配额护栏', 'desc': '余量 ≤ reserve 即收工；单次上限 max_days_per_run'},
    ],
    'index': [
        {'name': '拉取指数K线', 'desc': '从上证/深证/创业板等指数源下载日K线'},
        {'name': '写入 index_daily_quote', 'desc': '批量写入指数行情表'},
    ],
    'etf': [
        {'name': '拉取ETF日K线', 'desc': '从 tushare/baostock 下载 ETF 行情'},
        {'name': '写入 daily_quote', 'desc': '批量写入 ETF 行情数据'},
    ],
    'fund': [
        {'name': '拉取 daily_basic', 'desc': 'tushare daily_basic 全市场5400只'},
        {'name': '写入 stock_fundamentals', 'desc': 'UPSERT 按 stock_code+trade_date 去重'},
        {'name': '跨表更新行业', 'desc': '从 stock_master.industry 补全'},
    ],
    'treemap': [
        {'name': '拉取行情快照', 'desc': '最新交易日 close + pe + industry 分组聚合'},
        {'name': '构建树图数据', 'desc': '按 industry 分组 → 市值排序 → 树形 JSON'},
        {'name': '写入缓存表', 'desc': '写入 stock_treemap_cache（按日期+指标）'},
    ],
    'stats': [
        {'name': '全库统计', 'desc': 'COUNT 各表行数 + 最新日期'},
        {'name': '生成 data_stats_cache', 'desc': 'JSON 写入缓存表'},
    ],
    'daily_completeness': [
        {'name': '遍历数据表', 'desc': 'daily_quote/feature_values 等核心表'},
        {'name': '计算每日完整度', 'desc': '当日实际行数 ÷ 预期行数'},
        {'name': '写入 daily_completeness', 'desc': '每交易日一条记录'},
    ],
    'model_train': [
        {'name': '拉取特征宽表', 'desc': 'feature_values PIVOT 宽表 + 基本面数据'},
        {'name': '标签计算', 'desc': 'Triple Barrier 标签（止盈/止损/时间到期）'},
        {'name': '数据集切分', 'desc': 'train/val/test 按时间顺序 6:2:2'},
        {'name': 'Optuna 搜索', 'desc': '超参数搜索 N trials'},
        {'name': 'XGBoost 训练', 'desc': 'early_stopping + eval_set 验证'},
        {'name': '模型保存', 'desc': 'pkl + 元数据存入 model_versions'},
    ],
    'model_signal': [
        {'name': '加载模型', 'desc': '读取 ACTIVE 状态模型 pkl'},
        {'name': '拉取特征', 'desc': '最新交易日特征值宽表'},
        {'name': '预测评分', 'desc': 'XGBoost predict_proba + buy_score 计算'},
        {'name': '写入 signal_history', 'desc': 'INSERT 信号记录'},
    ],
    'model_health': [
        {'name': '遍历模型版本', 'desc': '检查所有 ACTIVE/PENDING 状态模型'},
        {'name': '健康评估', 'desc': '信号数量、新鲜度、特征完整度校验'},
        {'name': '标记异常', 'desc': '状态异常 → data_anomaly 标记'},
    ],
    'feature_compute': [
        {'name': '解析 KEPL 公式', 'desc': 'Lark LALR(1) 词法分析 + 语法分析 → AST'},
        {'name': '拉取行情', 'desc': 'daily_quote → DataFrame（含分片分批）'},
        {'name': 'KEPL → pandas', 'desc': 'AST 翻译为 pandas 向量化执行计划'},
        {'name': '批量计算', 'desc': '逐股票 groupby 计算 + 进度回调'},
        {'name': '写入 feature_values', 'desc': 'COPY 流式写入，无 OOM'},
    ],
    'feature_backfill': [
        {'name': '获取日期范围', 'desc': 'trade_calendar 交易日列表（跨月）'},
        {'name': '拉取行情', 'desc': 'daily_quote → DataFrame（日期分片）'},
        {'name': '批量计算', 'desc': 'compute_feature() 全量回填计算'},
        {'name': '更新统计', 'desc': '_update_feature_stats_after_compute'},
    ],
    'entity_stats': [
        {'name': 'JOIN 计算总格子', 'desc': 'daily_quote 行数 × 特征数 = 总预期格子'},
        {'name': '写入 entity_stats', 'desc': '每只股票一行的数据完整度基线'},
    ],
    'analyze': [
        {'name': '刷新大表统计', 'desc': 'ANALYZE feature_values/daily_quote/stock_moneyflow，防止分区大表统计过期'},
    ],
    'top_list': [
        {'name': '拉取龙虎榜', 'desc': 'tushare top_list 每日上榜明细（含上榜原因）'},
        {'name': '写入 stock_top_list', 'desc': 'UPSERT 按 trade_date+stock_code+reason 去重'},
    ],
    'margin_detail': [
        {'name': '拉取两融明细', 'desc': 'tushare margin_detail 融资融券全标的'},
        {'name': '写入 stock_margin_detail', 'desc': 'UPSERT 按 trade_date+stock_code 去重'},
    ],
    'moneyflow_hsgt': [
        {'name': '拉取沪深港通资金流向', 'desc': 'tushare moneyflow_hsgt 北向/南向整体'},
        {'name': '写入 moneyflow_hsgt', 'desc': 'UPSERT 按 trade_date 去重'},
    ],
    'block_trade': [
        {'name': '拉取大宗交易', 'desc': 'tushare block_trade 大宗交易明细'},
        {'name': '写入 block_trade', 'desc': 'UPSERT 按 trade_date+code+price+vol 去重'},
    ],
    'share_float': [
        {'name': '拉取限售解禁', 'desc': 'tushare share_float 按解禁日扫描'},
        {'name': '写入 stock_share_float', 'desc': 'UPSERT 按 code+float_date+holder 去重'},
    ],
    'repurchase': [
        {'name': '拉取回购', 'desc': 'tushare repurchase 按公告日扫描'},
        {'name': '写入 stock_repurchase', 'desc': 'UPSERT 按 code+ann_date+vol+amount 去重'},
    ],
    'dividend': [
        {'name': '拉取分红送转', 'desc': 'tushare dividend 按报告期全市场（本期中报+上年年报）'},
        {'name': '写入 stock_dividend', 'desc': 'UPSERT 按 code+end_date+div_proc 去重'},
    ],
    'forecast': [
        {'name': '拉取业绩预告', 'desc': 'tushare forecast 按公告日扫描'},
        {'name': '写入 stock_forecast', 'desc': 'UPSERT 按 code+end_date+ann_date 去重'},
    ],
    'express': [
        {'name': '拉取业绩快报', 'desc': 'tushare express 按公告日扫描'},
        {'name': '写入 stock_express', 'desc': 'UPSERT 按 code+end_date+ann_date 去重'},
    ],
    'index_weight': [
        {'name': '拉取指数成分权重', 'desc': 'tushare index_weight 沪深300/中证500 月度快照'},
        {'name': '写入 index_weight', 'desc': 'UPSERT 按 index_code+trade_date+stock_code 去重'},
    ],
    'fina_daily': [
        {'name': '扫描当日披露财报', 'desc': 'income 按 ann_date 发现当日新披露'},
        {'name': '逐票拉取财务指标', 'desc': 'fina_indicator 单票补全（上限 300 只/日）'},
    ],
    'data_backfill': [
        {'name': '读取回补参数', 'desc': 'strategy_config: backfill_ext（表清单/区间/配额保留）'},
        {'name': '循环交易日回补', 'desc': '交易日×表采集，已入库跳过，配额熔断续跑'},
    ],
    'moneyflow': [
        {'name': '拉取资金流向', 'desc': 'tushare moneyflow 全市场主力/中单/小单'},
        {'name': '写入 stock_moneyflow', 'desc': 'UPSERT 按 trade_date+stock_code 去重'},
    ],
    'stock_master': [
        {'name': '拉取股票列表', 'desc': 'tushare stock_basic 全量股票'},
        {'name': 'UPSERT stock_master', 'desc': '逐行写入/更新（IPO/退市/名称/交易所）'},
        {'name': '标记退市', 'desc': '当前列表中不存在的股票 → status=D'},
    ],
    'top_list': [
        {'name': '拉取龙虎榜', 'desc': 'tushare top_list 每日数据'},
        {'name': '写入 stock_top_list', 'desc': 'UPSERT 按 stock_code+trade_date 去重'},
    ],
    'analyze': [
        {'name': '刷新大表统计', 'desc': 'ANALYZE feature_values/daily_quote/stock_moneyflow，防止分区大表统计过期'},
    ],
    'top_list': [
        {'name': '拉取龙虎榜', 'desc': 'tushare top_list 每日上榜明细（含上榜原因）'},
        {'name': '写入 stock_top_list', 'desc': 'UPSERT 按 trade_date+stock_code+reason 去重'},
    ],
    'margin_detail': [
        {'name': '拉取两融明细', 'desc': 'tushare margin_detail 融资融券全标的'},
        {'name': '写入 stock_margin_detail', 'desc': 'UPSERT 按 trade_date+stock_code 去重'},
    ],
    'moneyflow_hsgt': [
        {'name': '拉取沪深港通资金流向', 'desc': 'tushare moneyflow_hsgt 北向/南向整体'},
        {'name': '写入 moneyflow_hsgt', 'desc': 'UPSERT 按 trade_date 去重'},
    ],
    'block_trade': [
        {'name': '拉取大宗交易', 'desc': 'tushare block_trade 大宗交易明细'},
        {'name': '写入 block_trade', 'desc': 'UPSERT 按 trade_date+code+price+vol 去重'},
    ],
    'share_float': [
        {'name': '拉取限售解禁', 'desc': 'tushare share_float 按解禁日扫描'},
        {'name': '写入 stock_share_float', 'desc': 'UPSERT 按 code+float_date+holder 去重'},
    ],
    'repurchase': [
        {'name': '拉取回购', 'desc': 'tushare repurchase 按公告日扫描'},
        {'name': '写入 stock_repurchase', 'desc': 'UPSERT 按 code+ann_date+vol+amount 去重'},
    ],
    'dividend': [
        {'name': '拉取分红送转', 'desc': 'tushare dividend 按报告期全市场（本期中报+上年年报）'},
        {'name': '写入 stock_dividend', 'desc': 'UPSERT 按 code+end_date+div_proc 去重'},
    ],
    'forecast': [
        {'name': '拉取业绩预告', 'desc': 'tushare forecast 按公告日扫描'},
        {'name': '写入 stock_forecast', 'desc': 'UPSERT 按 code+end_date+ann_date 去重'},
    ],
    'express': [
        {'name': '拉取业绩快报', 'desc': 'tushare express 按公告日扫描'},
        {'name': '写入 stock_express', 'desc': 'UPSERT 按 code+end_date+ann_date 去重'},
    ],
    'index_weight': [
        {'name': '拉取指数成分权重', 'desc': 'tushare index_weight 沪深300/中证500 月度快照'},
        {'name': '写入 index_weight', 'desc': 'UPSERT 按 index_code+trade_date+stock_code 去重'},
    ],
    'fina_daily': [
        {'name': '扫描当日披露财报', 'desc': 'income 按 ann_date 发现当日新披露'},
        {'name': '逐票拉取财务指标', 'desc': 'fina_indicator 单票补全（上限 300 只/日）'},
    ],
    'data_backfill': [
        {'name': '读取回补参数', 'desc': 'strategy_config: backfill_ext（表清单/区间/配额保留）'},
        {'name': '循环交易日回补', 'desc': '交易日×表采集，已入库跳过，配额熔断续跑'},
    ],
    'moneyflow': [
        {'name': '拉取资金流向', 'desc': 'tushare moneyflow 每日数据'},
        {'name': '写入 stock_moneyflow', 'desc': 'UPSERT 按 stock_code+trade_date 去重'},
    ],
    'margin_detail': [
        {'name': '拉取融资融券', 'desc': 'tushare margin_detail 每日数据'},
        {'name': '写入 stock_margin_detail', 'desc': 'UPSERT 按 stock_code+trade_date 去重'},
    ],
    'holder_number': [
        {'name': '拉取股东人数', 'desc': 'tushare stk_holdernumber 逐只查询'},
        {'name': '写入 stock_holder_number', 'desc': 'UPSERT 按 stock_code+end_date 去重'},
    ],
    'sync_fundamentals': [
        {'name': '拉取 daily_basic', 'desc': 'tushare daily_basic 批量100只'},
        {'name': 'UPSERT stock_fundamentals', 'desc': '按 stock_code+trade_date 去重'},
    ],
    'backup_qiniu': [
        {'name': '护栏与互斥', 'desc': 'flock 防并发重叠 + 检查暂存盘剩余空间'},
        {'name': 'pg_dump 全量导出', 'desc': 'docker exec pg_dump -Fc 一致性快照（不停库）'},
        {'name': 'restic 增量上传', 'desc': '内容分块去重，只传变化块到七牛 Kodo'},
        {'name': '保留策略', 'desc': '每周日 forget --prune 保留最近 30 个每日快照'},
    ],
}


@router.get("/node-types")
def list_node_types():
    """返回所有已注册的 DAG 节点类型。"""
    from app.db.connection import get_sync_db
    from sqlalchemy import text

    db = get_sync_db()
    try:
        rows = db.execute(text("SELECT node_name, deps, label, sort_order FROM dag_config ORDER BY sort_order")).fetchall()
        items = []
        for r in rows:
            items.append({
                "node_name": r[0],
                "deps": [d.strip() for d in (r[1] or "").split(",") if d.strip()],
                "label": r[2] or r[0],
                "sort_order": r[3] or 0,
                "has_function": r[0] in NODE_FN_MAP,
                "status": "active" if r[0] in NODE_FN_MAP else "unregistered",
            })
        db.close()
        return {"items": items, "total": len(items)}
    except Exception as e:
        db.close()
        return {"items": [], "total": 0, "error": str(e)[:200]}


@router.get("/node-types/{node_name}")
def get_node_type(node_name: str):
    """获取单个节点类型的详情（含内部依赖子图）。"""
    from app.db.connection import get_sync_db
    from sqlalchemy import text

    db = get_sync_db()
    try:
        r = db.execute(text(
            "SELECT node_name, deps, label, sort_order FROM dag_config WHERE node_name = :n"
        ), {"n": node_name}).fetchone()
        if not r:
            db.close()
            raise HTTPException(404, f"节点 '{node_name}' 不存在")

        db.close()
        return {
            "node_name": r[0],
            "deps": [d.strip() for d in (r[1] or "").split(",") if d.strip()],
            "label": r[2] or r[0],
            "sort_order": r[3] or 0,
            "has_function": r[0] in NODE_FN_MAP,
            "sub_steps": NODE_SUB_STEPS.get(node_name, [
                {'name': '执行', 'desc': '节点执行函数'}
            ]),
        }
    except HTTPException:
        raise
    except Exception as e:
        db.close()
        raise HTTPException(500, str(e)[:200])
