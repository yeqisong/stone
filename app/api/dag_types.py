"""DAG 节点类型 API（v2.0 重构 迭代 3.3 + 内部依赖子图）。"""
from fastapi import APIRouter, HTTPException
from scripts.pipeline import NODE_FN_MAP

router = APIRouter(prefix="/api/dag", tags=["dag"])

# 节点内部依赖子图定义（迭代 3.3 补齐）
NODE_SUB_STEPS = {
    'kline': [
        {'name': '检测交易日', 'desc': '校验目标日期是否为交易日'},
        {'name': '拉取行情', 'desc': 'baostock/AKShare 下载日K线'},
        {'name': '写入 daily_quote', 'desc': '批量 INSERT ... ON CONFLICT UPDATE'},
        {'name': '触发下游', 'desc': '通知 fund/treemap 等下游节点'},
    ],
    'fund': [
        {'name': '遍历股票', 'desc': '逐只股票查询基本面数据'},
        {'name': '拉取财务', 'desc': 'baostock query_stock_basic + growth/profit'},
        {'name': '写入 stock_fundamentals', 'desc': 'INSERT ... ON CONFLICT UPDATE'},
        {'name': '计算市值', 'desc': 'market_cap = close × total_shares'},
    ],
    'treemap': [
        {'name': '拉取行情快照', 'desc': '最新交易日 close + pe + industry'},
        {'name': '构建树图数据', 'desc': '按 industry 分组 → 市值排序 → JSON'},
        {'name': '写入缓存表', 'desc': 'stock_treemap_cache'},
    ],
    'stats': [
        {'name': '全库统计', 'desc': 'COUNT 各表行数'},
        {'name': '生成 data_stats_cache', 'desc': 'JSON 写入缓存表'},
        {'name': '更新 system_metrics', 'desc': '系统指标快照'},
    ],
    'model_train': [
        {'name': '拉取特征宽表', 'desc': 'feature_values + indicators JOIN'},
        {'name': '标签计算', 'desc': 'Triple Barrier 标签'},
        {'name': '数据集切分', 'desc': 'train/val/test 按时间顺序'},
        {'name': 'Optuna 搜索', 'desc': '超参数搜索 N trials'},
        {'name': 'XGBoost 训练', 'desc': 'early_stopping + eval_set'},
        {'name': '模型保存', 'desc': 'pkl + 元数据快照'},
    ],
    'model_signal': [
        {'name': '加载模型', 'desc': '读取 ACTIVE 模型 pkl'},
        {'name': '拉取特征', 'desc': '最新日期的特征值'},
        {'name': '预测/规则评分', 'desc': 'buy_score 计算'},
        {'name': '写入 signal_history', 'desc': 'INSERT 信号记录'},
    ],
    'feature_compute': [
        {'name': '解析 KEPL 公式', 'desc': '词法分析 + 语法分析 → AST'},
        {'name': '拉取行情', 'desc': 'daily_quote → DataFrame'},
        {'name': 'KEPL → pandas', 'desc': '公式翻译为 pandas 执行计划'},
        {'name': '批量计算', 'desc': '逐股票 groupby 计算'},
        {'name': '写入 feature_values', 'desc': 'INSERT ... ON CONFLICT UPDATE'},
    ],
    'feature_backfill': [
        {'name': '获取日期范围', 'desc': 'trade_calendar 交易日列表'},
        {'name': '拉取行情', 'desc': 'daily_quote → DataFrame'},
        {'name': '批量计算', 'desc': 'compute_feature() 全量计算'},
        {'name': '更新统计', 'desc': '_update_feature_stats_after_compute'},
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

        # 查询下游节点
        downstream = db.execute(text(
            "SELECT node_name, label FROM dag_config WHERE deps LIKE :pat"
        ), {"pat": f"%{node_name}%"}).fetchall()

        db.close()
        return {
            "node_name": r[0],
            "deps": [d.strip() for d in (r[1] or "").split(",") if d.strip()],
            "label": r[2] or r[0],
            "sort_order": r[3] or 0,
            "has_function": r[0] in NODE_FN_MAP,
            "downstream": [{"node_name": d[0], "label": d[1]} for d in downstream],
            "sub_steps": NODE_SUB_STEPS.get(node_name, [
                {'name': '执行', 'desc': '节点执行函数'}
            ]),
        }
    except HTTPException:
        raise
    except Exception as e:
        db.close()
        raise HTTPException(500, str(e)[:200])
