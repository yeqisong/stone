"""模型版本管理 API。"""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from pydantic import BaseModel, Field
from typing import Optional
import json
import threading
from datetime import date as _dt, timedelta as _td

from app.db.connection import get_sync_db
from app.auth.auth import get_current_user

router = APIRouter(tags=["models"])

# 训练终止事件注册表：version → threading.Event（stop/删除 联动训练线程）
_train_events: dict = {}
_train_events_lock = threading.Lock()


def _safe_fetch_model(db, version: str):
    """查询模型版本，兼容 deleted_at 列未迁移的情况。返回行或 None。"""
    try:
        return db.execute(text(
            "SELECT status, activated_at, deleted_at FROM model_versions WHERE version = :v"
        ), {"v": version}).fetchone()
    except Exception:
        db.rollback()
        return db.execute(text(
            "SELECT status, activated_at, NULL as deleted_at FROM model_versions WHERE version = :v"
        ), {"v": version}).fetchone()


def _deep_merge(base: dict, patch: dict) -> dict:
    """递归合并配置字典：patch 覆盖 base，嵌套字典逐层合并。"""
    out = dict(base)
    for k, v in patch.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


from typing import Optional, List

class CreateModel(BaseModel):
    model_name: str
    # 数据配置
    entity: str = "stock"            # stock / index / etf
    stock_pool: str = "all"          # all / custom
    train_start: str = "2021-01-01"
    train_end: str = "2025-12-31"
    test_start: str = "2026-01-01"
    test_end: str = ""
    # 特征配置
    features: List[str] = ["boll", "macd", "rsi", "atr", "ma", "volume"]
    feature_names: Optional[List[str]] = None
    # 模型配置
    ml_enabled: bool = False
    model_type: str = "xgboost"
    # 搜索空间
    n_estimators_min: int = 100
    n_estimators_max: int = 500
    max_depth_min: int = 3
    max_depth_max: int = 10
    learning_rate_min: float = 0.01
    learning_rate_max: float = 0.3
    # 训练参数
    optuna_trials: int = 50
    initial_cash: int = 1000000
    max_positions: int = 5
    # 信号配置
    buy_threshold: float = 0.6
    sell_threshold: float = 0.4
    # ML 买入阈值模式：quantile=当日预测分布 top N%（默认，自适应模型能力）；absolute=绝对预测收益率
    signal_threshold_mode: str = "quantile"
    buy_top_pct: float = 0.05              # quantile 模式：top 5% 触发买入
    ml_confidence_threshold: float = 0.02  # absolute 模式：预测平均涨幅 > 2% 触发买入
    # 标签与标准化（v3.5 方法论）：excess=N日超额收益（相对沪深300）；cs_rank=特征逐日截面排名
    # 旧模型 config 无这两个键时训练端按 absolute/none 兼容处理
    label_mode: str = "excess"
    feature_norm: str = "cs_rank"
    # 交易规则（买卖规则描述，前端表单整段提交）
    trading_rules: Optional[dict] = None
    # 交易成本
    stamp_tax: float = 0.001      # 印花税 0.1%
    commission: float = 0.00025   # 佣金 0.025%
    slippage: float = 0.001       # 滑点 0.1%
    # 风险控制
    stop_loss_pct: float = 8.0
    signal_timeout_days: int = 20


@router.get("/v1/models")
def list_models(entity: str = Query("stock")):
    """模型版本列表。"""
    db = get_sync_db()
    try:
        try:
            rows = db.execute(text("""
                SELECT version, model_name, status, config, best_params,
                       evaluation_report, sharpe, win_rate, max_drawdown, annual_return,
                       created_at, trained_at, activated_at
                FROM model_versions
                WHERE deleted_at IS NULL AND COALESCE(config->>'entity', 'stock') = :ent
                ORDER BY created_at DESC
            """), {"ent": entity}).fetchall()
        except Exception:
            db.rollback()
            # deleted_at 列未迁移时回退
            rows = db.execute(text("""
                SELECT version, model_name, status, config, best_params,
                       evaluation_report, sharpe, win_rate, max_drawdown, annual_return,
                       created_at, trained_at, activated_at
                FROM model_versions
                ORDER BY created_at DESC
            """)).fetchall()
        versions = []
        for r in rows:
            versions.append({
                "version": r[0], "model_name": r[1], "status": r[2],
                "config": r[3] if isinstance(r[3], dict) else (json.loads(r[3]) if r[3] else {}),
                "best_params": r[4] if isinstance(r[4], dict) else (json.loads(r[4]) if r[4] else None),
                "evaluation_report": r[5] if isinstance(r[5], dict) else (json.loads(r[5]) if r[5] else None),
                "sharpe": float(r[6]) if r[6] else None,
                "win_rate": float(r[7]) if r[7] else None,
                "max_drawdown": float(r[8]) if r[8] else None,
                "annual_return": float(r[9]) if r[9] else None,
                "created_at": str(r[10]) if r[10] else None,
                "trained_at": str(r[11]) if r[11] else None,
                "activated_at": str(r[12]) if r[12] else None,
            })
        return {"versions": versions, "count": len(versions)}
    finally:
        db.close()


@router.get("/v1/models/{version}")
@router.get("/v1/models/paper-portfolio")
def get_paper_portfolio(user: str = Depends(get_current_user)):
    """纸面组合（影子运行）：净值曲线 vs 沪深300 + 当前持仓 + 最近模拟成交。"""
    db = get_sync_db()
    try:
        meta_row = db.execute(text(
            "SELECT params FROM strategy_config WHERE strategy_name='paper_portfolio'")).scalar()
        meta = json.loads(meta_row) if isinstance(meta_row, str) else (meta_row or {})

        eod = db.execute(text("""
            SELECT trade_date, equity, cash, detail FROM paper_trades
            WHERE action='EOD' ORDER BY trade_date
        """)).fetchall()
        equity = []
        base_bm = None
        initial = float(meta.get('initial_cash', 1_000_000))
        for d, eq, ca, dt in eod:
            det = dt if isinstance(dt, dict) else (json.loads(dt) if dt else {})
            bm = det.get('benchmark_close')
            if bm and base_bm is None:
                base_bm = float(bm)
            equity.append({'date': str(d), 'equity': float(eq), 'cash': float(ca),
                           'benchmark': round(initial * float(bm) / base_bm, 2) if bm and base_bm else None})
        last_eq = equity[-1]['equity'] if equity else initial

        positions = []
        for r in db.execute(text("""
            SELECT p.stock_code, COALESCE(p.stock_name, sm.stock_name, '') AS sname, p.shares,
                   p.buy_price, p.cost_basis, p.buy_date, p.peak, p.model_version
            FROM paper_positions p LEFT JOIN stock_master sm ON sm.stock_code = p.stock_code
            ORDER BY p.buy_date
        """)).fetchall():
            positions.append({'stock_code': r[0], 'stock_name': r[1], 'shares': r[2],
                              'buy_price': float(r[3]), 'cost_basis': float(r[4]),
                              'buy_date': str(r[5]), 'peak': float(r[6]) if r[6] else None,
                              'model_version': r[7]})

        trades = []
        for r in db.execute(text("""
            SELECT trade_date, action, stock_code, stock_name, price, shares, amount,
                   commission, pnl, reason, equity FROM paper_trades
            WHERE action IN ('BUY','SELL') ORDER BY trade_date DESC, id DESC LIMIT 50
        """)).fetchall():
            trades.append({'date': str(r[0]), 'action': r[1], 'stock_code': r[2], 'stock_name': r[3],
                           'price': float(r[4]) if r[4] is not None else None,
                           'shares': r[5], 'amount': float(r[6]) if r[6] is not None else None,
                           'commission': float(r[7]) if r[7] is not None else None,
                           'pnl': float(r[8]) if r[8] is not None else None,
                           'reason': r[9], 'equity': float(r[10]) if r[10] is not None else None})

        w = db.execute(text(
            "SELECT COUNT(*) FILTER (WHERE pnl > 0), COUNT(*) FROM paper_trades WHERE action='SELL'")).fetchone()
        n_trades = db.execute(text(
            "SELECT COUNT(*) FROM paper_trades WHERE action IN ('BUY','SELL')")).scalar()
        win_rate = (w[0] / w[1]) if w[1] else None
        last_bm = equity[-1]['benchmark'] if equity and equity[-1]['benchmark'] else None
        db.close()
        return {
            'meta': meta,
            'stats': {
                'initial_cash': initial, 'equity': last_eq,
                'total_return': round(last_eq / initial - 1, 4) if equity else None,
                'benchmark_return': round(last_bm / initial - 1, 4) if last_bm else None,
                'days': len(equity),
                'n_trades': n_trades,
                'win_rate': round(win_rate, 4) if win_rate is not None else None,
                'n_positions': len(positions),
            },
            'equity': equity, 'positions': positions, 'trades': trades,
        }
    finally:
        db.close()


def get_model(version: str):
    """模型版本详情。"""
    db = get_sync_db()
    try:
        r = db.execute(text("""
            SELECT version, model_name, status, config, best_params,
                   evaluation_report, sharpe, win_rate, max_drawdown, annual_return,
                   created_at, trained_at, activated_at, archived_at,
                   strategy_scan_results, perm_test
            FROM model_versions WHERE version = :v
        """), {"v": version}).fetchone()
        if not r:
            raise HTTPException(404, "版本不存在")
        return {
            "version": r[0], "model_name": r[1], "status": r[2],
            "config": r[3] if isinstance(r[3], dict) else (json.loads(r[3]) if r[3] else {}),
            "best_params": r[4] if isinstance(r[4], dict) else (json.loads(r[4]) if r[4] else None),
            "evaluation_report": r[5] if isinstance(r[5], dict) else (json.loads(r[5]) if r[5] else None),
            "sharpe": float(r[6]) if r[6] else None,
            "win_rate": float(r[7]) if r[7] else None,
            "max_drawdown": float(r[8]) if r[8] else None,
            "annual_return": float(r[9]) if r[9] else None,
            "created_at": str(r[10]) if r[10] else None,
            "trained_at": str(r[11]) if r[11] else None,
            "activated_at": str(r[12]) if r[12] else None,
            "archived_at": str(r[13]) if r[13] else None,
            "strategy_scan_results": r[14] if isinstance(r[14], (dict, list)) else (json.loads(r[14]) if r[14] else None),
            "perm_test": r[15] if isinstance(r[15], (dict, list)) else (json.loads(r[15]) if r[15] else None),
        }
    finally:
        db.close()


@router.post("/v1/models")
def create_model(body: CreateModel, user: str = Depends(get_current_user)):
    """创建新模型版本（状态 DRAFT，自动生成版本号）。"""
    if not body.model_name or not body.model_name.strip():
        raise HTTPException(400, "模型名称不能为空")
    db = get_sync_db()
    try:
        # 生成版本号: 数值解析所有版本号取最大主版本（字符串 MAX 会让 v9>v10）
        rows = db.execute(text("SELECT version FROM model_versions")).fetchall()
        majors = []
        for (v,) in rows:
            try:
                majors.append(int(str(v).lstrip('v').split('.')[0]))
            except (ValueError, IndexError):
                continue
        if majors:
            major = max(majors) + 1
            minor = 0
        else:
            major, minor = 1, 0
        version = f"v{major}.{minor}"

        config = {
            "entity": body.entity,
            "stock_pool": body.stock_pool,
            "train_start": body.train_start,
            "train_end": body.train_end,
            "test_start": body.test_start,
            "test_end": body.test_end or None,
            "features": body.features,
            "feature_names": body.feature_names or body.features,
            "ml_enabled": body.ml_enabled,
            "model_type": body.model_type,
            "optuna_trials": body.optuna_trials,
            "initial_cash": body.initial_cash,
            "max_positions": body.max_positions,
            "stamp_tax": body.stamp_tax,
            "commission": body.commission,
            "slippage": body.slippage,
            "search_space": {
                "n_estimators": [body.n_estimators_min, body.n_estimators_max],
                "max_depth": [body.max_depth_min, body.max_depth_max],
                "learning_rate": [body.learning_rate_min, body.learning_rate_max],
            },
            "signal": {
                "buy_threshold": body.buy_threshold,
                "sell_threshold": body.sell_threshold,
                "threshold_mode": body.signal_threshold_mode,
                "buy_top_pct": body.buy_top_pct,
                "ml_confidence_threshold": body.ml_confidence_threshold,
            },
            "trading_rules": body.trading_rules or {},
            "label_mode": body.label_mode,
            "feature_norm": body.feature_norm,
            "risk": {
                "stop_loss_pct": body.stop_loss_pct,
                "signal_timeout_days": body.signal_timeout_days,
            },
        }
        db.execute(text("""
            INSERT INTO model_versions (version, model_name, status, config)
            VALUES (:v, :n, 'DRAFT', :cfg)
        """), {"v": version, "n": body.model_name, "cfg": json.dumps(config)})
        db.commit()
        return {"ok": True, "version": version, "model_name": body.model_name, "status": "DRAFT"}
    finally:
        db.close()


@router.get("/v1/models/{version}/health")
def get_model_health(version: str):
    """模型健康度最新记录。"""
    db = get_sync_db()
    try:
        r = db.execute(text("""
            SELECT health_status, live_win_rate, signal_count, avg_forward_5d, detail, check_date,
                   rank_ic, rank_icir
            FROM model_health WHERE version=:v ORDER BY check_date DESC LIMIT 1
        """), {"v": version}).fetchone()
        if not r:
            return {"health_status": "HEALTHY", "live_win_rate": 0, "signal_count": 0,
                    "avg_forward_5d": 0, "rank_ic": None, "rank_icir": None}
        return {
            "health_status": r[0], "live_win_rate": float(r[1]) if r[1] else 0,
            "signal_count": r[2] or 0, "avg_forward_5d": float(r[3]) if r[3] else 0,
            "detail": r[4] if isinstance(r[4], dict) else (json.loads(r[4]) if r[4] else {}),
            "check_date": str(r[5]) if r[5] else None,
            "rank_ic": float(r[6]) if r[6] is not None else None,
            "rank_icir": float(r[7]) if r[7] is not None else None,
        }
    finally:
        db.close()

@router.get("/v1/models/{version}/diagnosis")
def get_model_diagnosis(version: str):
    """模型质量诊断卡片（拟合度/收益能力/盈亏比/vs指数/集中度）。"""
    db = get_sync_db()
    try:
        from app.db.connection import get_sync_db as gdb
        mv = db.execute(text(
            "SELECT sharpe, win_rate, max_drawdown, annual_return, evaluation_report FROM model_versions WHERE version=:v"
        ), {"v": version}).fetchone()
        if not mv:
            raise HTTPException(404, "模型版本不存在")

        sharpe = float(mv[0]) if mv[0] else 0
        win_rate = float(mv[1]) if mv[1] else 0
        max_dd = float(mv[2]) if mv[2] else 0
        annual = float(mv[3]) if mv[3] else 0
        eval_data = json.loads(mv[4]) if isinstance(mv[4], str) else (mv[4] or {})

        # 计算诊断指标
        # 1. 拟合度（val_sharpe - test_sharpe，越小越好）
        val_sharpe = float(eval_data.get("val_sharpe", 0) or 0)
        test_sharpe = float(eval_data.get("test_sharpe", 0) or 0)
        overfit_gap = round(abs(val_sharpe - test_sharpe), 4)
        overfit_level = "green" if overfit_gap < 0.5 else ("yellow" if overfit_gap < 1.0 else "red")

        # 2. 盈亏比
        win_loss_ratio = win_rate / (1 - win_rate) if win_rate and win_rate < 1 else 0

        # 3. 基准对比（vs 沪深300 同期，训练时已算并存入 evaluation_report）
        benchmark_return = float(eval_data.get("benchmark_return", 0) or 0)
        vs_benchmark = annual - benchmark_return

        # 4. 集中度（Top3 盈利占比）
        top3_ratio = float(eval_data.get("top3_profit_ratio", 0) or 0)

        return {
            "version": version,
            "overfit": {"gap": overfit_gap, "level": overfit_level, "val_sharpe": val_sharpe, "test_sharpe": test_sharpe},
            "return_capability": {"sharpe": sharpe, "win_rate": win_rate, "annual_return": annual},
            "win_loss": {"win_rate": win_rate, "loss_rate": round(1-win_rate,4), "ratio": round(win_loss_ratio,2)},
            "vs_benchmark": {"model_return": annual, "benchmark_return": benchmark_return, "excess": round(vs_benchmark,4)},
            "concentration": {"top3_profit_ratio": round(top3_ratio,4), "level": "green" if top3_ratio < 0.5 else ("yellow" if top3_ratio < 0.7 else "red")},
            "max_drawdown": max_dd,
        }
    except HTTPException:
        db.close()
        raise
    except Exception as e:
        db.close()
        raise HTTPException(500, str(e)[:200])
    finally:
        try: db.close()
        except: pass


@router.get("/v1/models/{version}/signals")
def get_model_signals(version: str):
    """模型信号明细列表（最近 50 条）。"""
    db = get_sync_db()
    try:
        rows = db.execute(text("""
            SELECT id, signal_date, stock_code, stock_name, direction, strength, price
            FROM signal_history
            WHERE strategy_name = 'model_signal' AND model_version = :version
            ORDER BY signal_date DESC LIMIT 50
        """), {"version": version}).fetchall()
        signals = []
        for r in rows:
            signals.append({
                "id": r[0], "signal_date": str(r[1]) if r[1] else None,
                "stock_code": r[2], "stock_name": r[3],
                "direction": r[4], "strength": r[5],
                "price": float(r[6]) if r[6] else 0,
            })
        return {"signals": signals, "count": len(signals)}
    except Exception as e:
        db.rollback()
        raise HTTPException(500, f"查询信号失败: {str(e)[:200]}")
    finally:
        db.close()

@router.get("/v1/models/{version}/delete-check")
def check_model_delete(version: str):
    """检查模型是否可以物理删除，返回关联数据统计。"""
    db = get_sync_db()
    try:
        # 先查模型是否存在（兼容 deleted_at 列未迁移的情况）
        r = _safe_fetch_model(db, version)
        if not r:
            raise HTTPException(404, f"版本 {version} 不存在")
        if r[2] is not None:
            raise HTTPException(400, "该模型已被删除")
        if r[0] == 'ACTIVE':
            raise HTTPException(400, "ACTIVE 状态模型不能删除，请先归档")

        activated = r[1] is not None
        signals = db.execute(text(
            "SELECT COUNT(*) FROM signal_history WHERE model_version = :v"
        ), {"v": version}).scalar() or 0
        trials = db.execute(text(
            "SELECT COUNT(*) FROM training_trials WHERE version = :v"
        ), {"v": version}).scalar() or 0
        health = db.execute(text(
            "SELECT COUNT(*) FROM model_health WHERE version = :v"
        ), {"v": version}).scalar() or 0
        comparisons = db.execute(text(
            "SELECT COUNT(*) FROM version_comparisons WHERE version_a = :v OR version_b = :v"
        ), {"v": version}).scalar() or 0

        can_physical = not activated and signals == 0 and trials == 0 and health == 0 and comparisons == 0
        return {
            "version": version,
            "can_physical_delete": can_physical,
            "related_data": {
                "signals": signals,
                "training_trials": trials,
                "health_records": health,
                "comparisons": comparisons,
                "activated": activated,
            }
        }
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(500, f"删除检查失败: {str(e)[:200]}")
    finally:
        db.close()


@router.delete("/v1/models/{version}")
def delete_model(version: str, mode: str = Query("soft"), user: str = Depends(get_current_user)):
    """删除模型版本。mode=soft 逻辑删除，mode=hard 物理删除。"""
    if mode not in ("soft", "hard"):
        raise HTTPException(400, "mode 参数只能是 soft 或 hard")
    db = get_sync_db()
    try:
        # 兼容 deleted_at 列未迁移的情况
        r = _safe_fetch_model(db, version)
        if not r:
            raise HTTPException(404, f"版本 {version} 不存在")
        if r[2] is not None:
            raise HTTPException(400, "该模型已被删除")
        if r[0] == 'ACTIVE':
            raise HTTPException(400, "ACTIVE 状态模型不能删除，请先归档")

        if mode == "soft":
            db.execute(text(
                "UPDATE model_versions SET deleted_at = CURRENT_TIMESTAMP WHERE version = :v"
            ), {"v": version})
            db.commit()
            # 如果正在训练中，通过事件终止训练线程（A4，替代无效的 dag.terminate）
            if r[0] == 'TRAINING':
                with _train_events_lock:
                    ev = _train_events.pop(version, None)
                if ev:
                    ev.set()
            return {"ok": True, "version": version, "mode": "soft"}

        # mode == "hard" — 物理删除
        activated = r[1] is not None
        signals = db.execute(text(
            "SELECT COUNT(*) FROM signal_history WHERE model_version = :v"
        ), {"v": version}).scalar() or 0
        trials = db.execute(text(
            "SELECT COUNT(*) FROM training_trials WHERE version = :v"
        ), {"v": version}).scalar() or 0
        health = db.execute(text(
            "SELECT COUNT(*) FROM model_health WHERE version = :v"
        ), {"v": version}).scalar() or 0
        comparisons = db.execute(text(
            "SELECT COUNT(*) FROM version_comparisons WHERE version_a = :v OR version_b = :v"
        ), {"v": version}).scalar() or 0
        backtests = db.execute(text(
            "SELECT COUNT(*) FROM backtest_records WHERE version = :v"
        ), {"v": version}).scalar() or 0

        if activated or signals > 0 or trials > 0 or health > 0 or comparisons > 0 or backtests > 0:
            raise HTTPException(400, "该模型有关联数据，不能物理删除，请使用逻辑删除（mode=soft）")

        # 按 FK 依赖顺序删除
        db.execute(text("DELETE FROM backtest_trades WHERE version = :v"), {"v": version})
        db.execute(text("DELETE FROM backtest_records WHERE version = :v"), {"v": version})
        db.execute(text("DELETE FROM version_comparisons WHERE version_a = :v OR version_b = :v"), {"v": version})
        db.execute(text("DELETE FROM model_health WHERE version = :v"), {"v": version})
        db.execute(text("DELETE FROM training_trials WHERE version = :v"), {"v": version})
        db.execute(text("DELETE FROM signal_history WHERE model_version = :v"), {"v": version})
        db.execute(text("DELETE FROM model_versions WHERE version = :v"), {"v": version})
        db.commit()
        return {"ok": True, "version": version, "mode": "hard"}
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(500, f"删除失败: {str(e)[:200]}")
    finally:
        db.close()


@router.get("/v1/models/{version}/feature-check")
def check_model_features(version: str, force: bool = Query(False)):
    """训练前置检查：特征数据覆盖（分训练/验证/测试三阶段，仅 A 股）。"""
    db = get_sync_db()
    try:
        r = db.execute(text("SELECT config FROM model_versions WHERE version=:v"), {"v": version}).fetchone()
        if not r:
            raise HTTPException(404, "版本不存在")
        cfg = r[0] if isinstance(r[0], dict) else (json.loads(r[0]) if r[0] else {})
        feature_names = cfg.get("feature_names") or cfg.get("features") or []

        # 有缓存且非强制 → 校验特征集未变 + 特征数据未更新，才复用缓存（C2）
        cached = cfg.get("_feature_check")
        if cached and not force:
            try:
                fns = feature_names[:3] or ['boll']
                latest = db.execute(text(
                    "SELECT MAX(trade_date) FROM feature_values WHERE feature_name = ANY(:fns)"
                ), {"fns": fns}).scalar()
                latest_str = str(latest)[:10] if latest else ''
                if cached.get('cached_features') == feature_names and latest_str and \
                        latest_str <= (cached.get('cached_data_date') or '9999-12-31'):
                    db.close()
                    return cached
            except Exception:
                pass

        if not feature_names:
            return {"ready": False, "warnings": ["模型未配置特征"], "features": [], "phases": None}

        # A 股过滤条件：仅排除 ETF（15/5 开头），与训练口径一致
        # （训练 build_feature_wide_table entity='stock' 用 exchange IN (SSE,SZSE)，含 0/3/68 开头）
        a_stock_filter = "stock_code NOT LIKE '15%' AND stock_code NOT LIKE '5%'"

        # 取前 3 个样本特征算日期范围（用于三点切分）
        all_dates = set()
        for fn in feature_names[:3]:
            dr = db.execute(text(
                f"SELECT DISTINCT trade_date FROM feature_values WHERE feature_name=:fn AND {a_stock_filter}"
            ), {"fn": fn}).fetchall()
            all_dates.update(str(d[0]) for d in dr)

        if not all_dates:
            return {"ready": False, "warnings": ["所有特征均无A股数据，请先执行特征补数"], "features": [], "phases": None}

        dates = sorted(all_dates)
        n = len(dates)
        cut1 = int(n * 0.6) if n > 5 else n
        cut2 = int(n * 0.8) if n > 5 else n

        def dr(dl):
            if not dl: return {"start": None, "end": None, "days": 0}
            return {"start": dl[0], "end": dl[-1], "days": len(dl)}

        phases = {
            "train": {**dr(dates[:cut1]), "label": "训练 60%"},
            "val":   {**dr(dates[cut1:cut2]), "label": "验证 20%"},
            "test":  {**dr(dates[cut2:]), "label": "测试 20%"},
        }

        warnings = []
        features_info = []
        all_ready = True
        limits = [cut1, cut2]

        for fn in feature_names:
            stats = {}
            for phase, pi in [("train", 0), ("val", 1), ("test", 2)]:
                s = dates[limits[pi-1]] if pi > 0 and limits[pi-1] < len(dates) else dates[0]
                e = dates[min(limits[pi], len(dates))-1] if pi < len(limits) and limits[pi] > 0 and limits[pi] <= len(dates) else dates[-1]
                if not dates: cnt = 0
                else:
                    cnt = db.execute(text(
                        f"SELECT COUNT(*) FROM feature_values WHERE feature_name=:fn AND trade_date BETWEEN :s AND :e AND {a_stock_filter}"
                    ), {"fn": fn, "s": s, "e": e}).scalar() or 0
                stats[phase] = cnt

            fn_row = db.execute(text(
                f"SELECT MIN(trade_date), MAX(trade_date), COUNT(DISTINCT stock_code), COUNT(*) "
                f"FROM feature_values WHERE feature_name=:fn AND {a_stock_filter}"
            ), {"fn": fn}).fetchone()

            if not fn_row or not fn_row[0]:
                features_info.append({"name": fn, "train": 0, "val": 0, "test": 0, "start": None, "end": None, "stocks": 0})
                warnings.append(f"「{fn}」无 A 股数据")
                all_ready = False
                continue

            info = {
                "name": fn, "start": str(fn_row[0]), "end": str(fn_row[1]),
                "stocks": fn_row[2] or 0, "total_rows": fn_row[3] or 0,
                "train": stats["train"], "val": stats["val"], "test": stats["test"],
            }
            tot = sum(stats.values()) or 1
            info["train_pct"] = round(stats["train"] / tot * 100, 1)
            info["val_pct"] = round(stats["val"] / tot * 100, 1)
            info["test_pct"] = round(stats["test"] / tot * 100, 1)
            for phase, label in [("train", "训练"), ("val", "验证"), ("test", "测试")]:
                if stats[phase] < 1000:
                    warnings.append(f"「{fn}」{label}阶段仅 {stats[phase]} 行，建议 ≥1000")
                    all_ready = False
            features_info.append(info)

        # 缓存结果到 config._feature_check（带特征集与数据日期，供过期校验）
        result = {"ready": all_ready, "warnings": warnings, "features": features_info, "phases": phases,
                  "cached_features": feature_names,
                  "cached_data_date": str(max(all_dates)) if all_dates else ''}
        cfg["_feature_check"] = result
        db.execute(text("UPDATE model_versions SET config=:cfg WHERE version=:v"),
                   {"cfg": json.dumps(cfg), "v": version})
        db.commit()
        db.close()
        return result
    except HTTPException:
        raise
    except Exception as e:
        try: db.close()
        except: pass
        raise HTTPException(500, str(e)[:200])
    finally:
        db.close()


@router.post("/v1/models/{version}/approve")
def approve_model(version: str, force: bool = Query(False), user: str = Depends(get_current_user)):
    """审批模型上线：旧 ACTIVE → ARCHIVED，新版本 → ACTIVE。

    晋升门槛（v3.6）：最近一次 walk-forward 对比判定为 FAIL 且未传 force=true 时拒绝。
    """
    db = get_sync_db()
    try:
        r = db.execute(text("SELECT status, config FROM model_versions WHERE version=:v"), {"v": version}).fetchone()
        if not r:
            raise HTTPException(404, "版本不存在")
        if r[0] != 'PENDING':
            raise HTTPException(400, f"当前状态为 {r[0]}，只有 PENDING 状态可审批")
        cfg = r[1] if isinstance(r[1], dict) else (json.loads(r[1]) if r[1] else {})
        entity = cfg.get("entity", "stock")

        # 晋升门槛：最近一次 walk-forward 对比 FAIL → 拒绝（force=true 可覆盖）
        cmp_row = db.execute(text("""
            SELECT report FROM version_comparisons
            WHERE version_a=:v ORDER BY created_at DESC LIMIT 1
        """), {"v": version}).fetchone()
        if cmp_row and not force:
            report = cmp_row[0] if isinstance(cmp_row[0], dict) else (json.loads(cmp_row[0]) if cmp_row[0] else {})
            if report.get('verdict') == 'FAIL':
                raise HTTPException(400, f"晋升门槛未通过：{report.get('reason')}"
                                         f"（多窗口稳定性不足；如仍要上线请加 force=true）")

        # 仅归档同主体的旧 ACTIVE
        db.execute(text(
            "UPDATE model_versions SET status='ARCHIVED', archived_at=CURRENT_TIMESTAMP "
            "WHERE status='ACTIVE' AND (config->>'entity') = :ent"
        ), {"ent": entity})
        db.execute(text("UPDATE model_versions SET status='ACTIVE', activated_at=CURRENT_TIMESTAMP WHERE version=:v"), {"v": version})
        db.commit()
        return {"ok": True, "version": version, "status": "ACTIVE"}
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(500, f"审批失败: {str(e)[:200]}")
    finally:
        db.close()

@router.put("/v1/models/{version}/config")
def update_model_config(version: str, body: dict, user: str = Depends(get_current_user)):
    """更新 DRAFT 状态模型配置（deep-merge：未提交字段保留，不再整体覆盖）。"""
    db = get_sync_db()
    try:
        r = db.execute(text("SELECT status, config FROM model_versions WHERE version=:v"), {"v": version}).fetchone()
        if not r:
            raise HTTPException(404, "版本不存在")
        if r[0] != 'DRAFT':
            raise HTTPException(400, f"只有 DRAFT 状态可编辑，当前为 {r[0]}")
        old_cfg = r[1] if isinstance(r[1], dict) else (json.loads(r[1]) if r[1] else {})
        name = body.get('model_name')
        patch = {k: v for k, v in body.items() if k != 'model_name'}
        merged = _deep_merge(old_cfg, patch)
        db.execute(text("UPDATE model_versions SET config=:cfg WHERE version=:v"),
                   {"v": version, "cfg": json.dumps(merged)})
        if name:
            db.execute(text("UPDATE model_versions SET model_name=:n WHERE version=:v"),
                       {"v": version, "n": name})
        db.commit()
        return {"ok": True, "version": version}
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(500, f"更新失败: {str(e)[:200]}")
    finally:
        db.close()


@router.post("/v1/models/{version}/stop")
def stop_training(version: str, user: str = Depends(get_current_user)):
    """强制停止训练，模型回到 DRAFT。

    通过 _train_events 触发训练线程的终止事件（A4），再回写状态；
    training 线程内的 DB 状态检查作为第二重保险。
    """
    db = get_sync_db()
    try:
        r = db.execute(text("SELECT status FROM model_versions WHERE version=:v"), {"v": version}).fetchone()
        if not r:
            raise HTTPException(404, "版本不存在")
        if r[0] != 'TRAINING':
            raise HTTPException(400, f"只有 TRAINING 状态可停止，当前为 {r[0]}")
        # 先唤醒训练线程（若正在运行）
        with _train_events_lock:
            ev = _train_events.pop(version, None)
        if ev:
            ev.set()
        db.execute(text("UPDATE model_versions SET status='DRAFT', best_params=NULL, evaluation_report=NULL, sharpe=NULL, win_rate=NULL, max_drawdown=NULL, annual_return=NULL WHERE version=:v"), {"v": version})
        db.commit()
        return {"ok": True, "version": version, "status": "DRAFT"}
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(500, f"操作失败: {str(e)[:200]}")
    finally:
        db.close()


@router.post("/v1/models/{version}/train")
def train_model(version: str, user: str = Depends(get_current_user)):
    """触发模型训练（通过 TaskManager 管理进度）。

    C1: 训练线程创建真实 dag_run_log（node_name='model_train'）并注入
    _node_log_ids，训练函数内部 update_node_progress 的进度即可经 WS 广播
    到前端；同时注入 _stop_event 供停止/删除联动（A4）。
    """
    from app.task import TaskManager

    tm = TaskManager()
    nodes = [
        {"node_name": "train_load_data"},
        {"node_name": "train_feature_eng"},
        {"node_name": "train_optuna"},
        {"node_name": "train_evaluate"},
    ]
    task = tm.create_task(task_type="model_train", flow_name=f"训练 {version}", nodes=nodes)
    if task.status == "failed":
        return {"ok": False, "error": task.error}

    db = get_sync_db()
    try:
        r = db.execute(text("SELECT status, config FROM model_versions WHERE version=:v"), {"v": version}).fetchone()
        if not r:
            raise HTTPException(404, "版本不存在")
        if r[0] not in ('DRAFT', 'REJECTED'):
            raise HTTPException(400, f"当前状态为 {r[0]}，只有 DRAFT/REJECTED 可训练")
        db.execute(text("UPDATE model_versions SET status='TRAINING', trained_at=CURRENT_TIMESTAMP WHERE version=:v"), {"v": version})
        db.commit()
    except HTTPException:
        raise
    finally:
        db.close()

    stop_event = threading.Event()
    with _train_events_lock:
        _train_events[version] = stop_event

    def _bg(tid, ver, ev):
        from datetime import date as _dt
        from app.task import TaskManager as _TM
        from scripts.pipeline import dag_task_model_train as _train, write_node_log as _write_log
        tm2 = _TM()
        tm2.start_task(tid)
        log_id = None
        try:
            # 创建真实 dag_run_log 行：训练函数内 update_node_progress 才能被 WS 广播
            log_id = _write_log(trade_date=str(_dt.today()), node_name='model_train',
                                status='pending', run_id=tid[:20])
            tm2.update_node(tid, "train_load_data", status="running")
            _train(trade_date="", version=ver,
                   _node_log_ids={'model_train': log_id}, _stop_event=ev)
            tm2.update_node(tid, "train_load_data", status="success")
            tm2.update_node(tid, "train_feature_eng", status="success")
            tm2.update_node(tid, "train_optuna", status="success")
            tm2.update_node(tid, "train_evaluate", status="success")
            tm2.complete_task(tid)
        except Exception as e:
            tm2.update_node(tid, "train_optuna", status="failed", error=str(e)[:200])
            tm2.fail_task(tid, str(e)[:200])
        finally:
            with _train_events_lock:
                _train_events.pop(ver, None)

    thread = threading.Thread(target=_bg, args=(task.task_id, version, stop_event), daemon=True)
    thread.start()
    return {"ok": True, "task_id": task.task_id, "version": version, "status": "training started"}


@router.post("/v1/models/{version}/retrain")
def retrain_model(version: str, user: str = Depends(get_current_user)):
    """REJECTED 模型重新训练：清空旧数据，回到 DRAFT。"""
    db = get_sync_db()
    try:
        r = db.execute(text("SELECT status FROM model_versions WHERE version=:v"), {"v": version}).fetchone()
        if not r:
            raise HTTPException(404, "版本不存在")
        if r[0] != 'REJECTED':
            raise HTTPException(400, f"只有 REJECTED 状态可重新训练，当前为 {r[0]}")
        db.execute(text("UPDATE model_versions SET status='DRAFT', best_params=NULL, evaluation_report=NULL, sharpe=NULL, win_rate=NULL, max_drawdown=NULL, annual_return=NULL WHERE version=:v"), {"v": version})
        db.commit()
        return {"ok": True, "version": version, "status": "DRAFT"}
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(500, f"操作失败: {str(e)[:200]}")
    finally:
        db.close()


@router.post("/v1/models/{version}/reject")
def reject_model(version: str, user: str = Depends(get_current_user)):
    """拒绝模型。"""
    db = get_sync_db()
    try:
        r = db.execute(text("SELECT status FROM model_versions WHERE version=:v"), {"v": version}).fetchone()
        if not r:
            raise HTTPException(404, "版本不存在")
        if r[0] != 'PENDING':
            raise HTTPException(400, f"当前状态为 {r[0]}，只有 PENDING 状态可拒绝")
        db.execute(text("UPDATE model_versions SET status='REJECTED', best_params=NULL, evaluation_report=NULL, sharpe=NULL, win_rate=NULL, max_drawdown=NULL, annual_return=NULL WHERE version=:v"), {"v": version})
        db.commit()
        return {"ok": True, "version": version, "status": "REJECTED"}
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(500, f"操作失败: {str(e)[:200]}")
    finally:
        db.close()



@router.get("/v1/models/{version}/quality-dashboard")
def quality_dashboard(version: str):
    db = get_sync_db()
    try:
        mv = db.execute(text("SELECT feature_list, config FROM model_versions WHERE version=:v"),{"v":version}).fetchone()
        if not mv: raise HTTPException(404,"不存在")
        features = mv[0] if isinstance(mv[0],list) else (json.loads(mv[0]) if mv[0] else [])
        if not features:  # 列未填充时回退 config
            cfg = mv[1] if isinstance(mv[1],dict) else (json.loads(mv[1]) if mv[1] else {})
            features = cfg.get('feature_names', [])
        fq = []
        for fn in (features or [])[:20]:
            fr = db.execute(text("SELECT data_completeness FROM features WHERE feature_name=:fn"),{"fn":fn}).fetchone()
            fq.append({"feature":fn,"completeness":round(float(fr[0])*100 if fr and fr[0] else 0,1)})
        db.close()
        return {"version":version,"phases":None,"features":fq,"correlation_warnings":[],"note":"特征质量数据需训练完成后生成，当前为占位数据"}
    except HTTPException:
        db.close(); raise
    except Exception as e:
        db.close(); raise HTTPException(500, str(e)[:200])


# ── v2.7 策略优化 + 归因分析 ──

import threading as _threading
import time as _time
import uuid as _uuid

_scan_tasks: dict = {}
_scan_lock = _threading.Lock()


class StrategyScanRequest(BaseModel):
    param_grid: dict  # {stop_loss:[0.03,...], take_profit:[0.05,...], trailing_retracement:[0.03,...]}
    val_start: str = "2022-01-01"
    val_end: str = "2023-12-31"
    hold_days: int = 10


@router.post("/v1/models/{version}/strategy-scan")
def start_strategy_scan(version: str, body: StrategyScanRequest, user: str = Depends(get_current_user)):
    """启动策略参数网格扫描。"""
    db = get_sync_db()
    try:
        r = db.execute(text("SELECT status, config FROM model_versions WHERE version=:v"), {"v": version}).fetchone()
        if not r:
            db.close(); raise HTTPException(404, "版本不存在")
        db.close()

        param_grid = body.param_grid
        stop_losses = param_grid.get('stop_loss', [0.05, 0.08])
        take_profits = param_grid.get('take_profit', [0.10, 0.15])
        trailings = param_grid.get('trailing_retracement', [0.05])
        # M2：param_grid 含 topk 时用 v2 引擎 + TopkDropoutStrategy 扫描（固定持仓数+每日换血）
        topks = param_grid.get('topk')
        n_drops = param_grid.get('n_drop', [1])

        if topks:
            combos = [{'stop_loss': sl, 'take_profit': tp, 'trailing_retracement': tr,
                       'topk': k, 'n_drop': nd}
                      for sl in stop_losses for tp in take_profits for tr in trailings
                      for k in topks for nd in n_drops]
        else:
            combos = [{'stop_loss': sl, 'take_profit': tp, 'trailing_retracement': tr}
                      for sl in stop_losses for tp in take_profits for tr in trailings]
        task_id = f"scan-{_uuid.uuid4().hex[:6]}"

        with _scan_lock:
            _scan_tasks[task_id] = {
                'task_id': task_id, 'version': version, 'status': 'running',
                'total_combos': len(combos), 'completed': 0,
                'best_sharpe': -999, 'best_params': None,
                'results': [], 'started_at': _time.time(),
            }

        thread = _threading.Thread(target=_run_scan, args=(
            task_id, version, combos, body.val_start, body.val_end or str(_dt.today()), body.hold_days
        ), daemon=True)
        thread.start()

        return {"ok": True, "task_id": task_id, "total_combos": len(combos), "status": "started"}
    except HTTPException:
        raise
    except Exception as e:
        db.close(); raise HTTPException(500, str(e)[:200])


@router.get("/v1/models/{version}/strategy-scan/{task_id}")
def get_scan_progress(version: str, task_id: str):
    """查询策略扫描进度 + 热力图数据。"""
    with _scan_lock:
        t = _scan_tasks.get(task_id)
        if not t:
            return {"status": "not_found"}
        return {
            'task_id': t['task_id'], 'status': t['status'],
            'completed': t['completed'], 'total_combos': t['total_combos'],
            'best_so_far': t['best_params'],
            'heatmap_data': t['results'][-100:],  # 最近100条
        }


@router.post("/v1/models/{version}/strategy-scan/{task_id}/apply")
def apply_scan_result(version: str, task_id: str, user: str = Depends(get_current_user)):
    """应用策略扫描的最优参数到 trading_rules。"""
    with _scan_lock:
        t = _scan_tasks.get(task_id)
        if not t or t['status'] != 'completed':
            raise HTTPException(400, "扫描未完成")
        best = t['best_params']

    db = get_sync_db()
    try:
        db.execute(text("""
            UPDATE model_versions SET trading_rules = :tr, strategy_scan_results = :sr,
                stage = 'strategy_optimized'
            WHERE version = :v
        """), {"v": version, "tr": json.dumps(best), "sr": json.dumps(t['results'])})
        db.commit()
        db.close()
        return {"ok": True, "applied": best}
    except Exception as e:
        db.close(); raise HTTPException(500, str(e)[:200])


def _run_scan(task_id, version, combos, val_start, val_end, hold_days):
    """后台执行策略扫描（预测一次，各参数组合复用同一预测列）。"""
    from scripts.pipeline import build_feature_wide_table, predict_for_version, _backtest
    from app.db.connection import get_sync_db
    from loguru import logger as _log

    try:
        db = get_sync_db()
        # 加载特征
        row = db.execute(text("SELECT feature_list, config FROM model_versions WHERE version=:v"),
                         {"v": version}).fetchone()
        if not row:
            db.close(); return
        feature_names = row[0] if isinstance(row[0], list) else (json.loads(row[0]) if row[0] else [])
        cfg = row[1] if isinstance(row[1], dict) else (json.loads(row[1]) if row[1] else {})
        if not feature_names:
            feature_names = cfg.get('feature_names', [])

        df = build_feature_wide_table(db, feature_names, val_start, val_end, 'stock')
        if df.empty:
            db.close()
            with _scan_lock: _scan_tasks[task_id].update({'status': 'failed', 'error': '验证集无数据'})
            return

        # 真模型预测一次（horizon 由 hold_days 映射），参数组合只换执行规则
        horizon = 5 if hold_days <= 5 else (20 if hold_days > 10 else 10)
        pred, err = predict_for_version(db, version, df, val_start, val_end, horizon)
        db.close()
        if pred is None:
            _log.warning(f"[scan] {version} 预测不可用: {err}")
            with _scan_lock: _scan_tasks[task_id].update({'status': 'failed', 'error': err})
            return

        results = []
        best_sharpe = -999
        best_params = None

        for i, combo in enumerate(combos):
            sl, tp, tr = combo['stop_loss'], combo['take_profit'], combo['trailing_retracement']
            if 'topk' in combo:
                # v2 引擎 + TopkDropoutStrategy（M2）：固定 topk 持仓 + 每日换血 n_drop
                from strategy.backtest.engine import run_backtest
                from strategy.backtest.models import TradeConfig
                from strategy.strategy import TopkDropoutStrategy
                c = TradeConfig(initial_cash=1_000_000, max_positions=combo['topk'],
                                stop_loss=sl, take_profit=tp, trailing=tr, hold_days=hold_days)
                bt = run_backtest(df, pred, TopkDropoutStrategy(c, topk=combo['topk'],
                                                               n_drop=combo['n_drop']),
                                  c, val_start, val_end)
                item = dict(combo, engine='v2', sharpe=bt.sharpe, max_dd=bt.max_dd,
                            win_rate=bt.win_rate, total_return=bt.total_return,
                            total_trades=bt.total_trades, total_cost=bt.total_cost)
            else:
                # 普通网格（无 topk）：v2 内核 + SignalStrategy（M7 起默认；返回 v1 口径 dict）
                bt = _backtest(df, pred, val_start, val_end, hold_days, sl, tp, trailing=tr)
                item = dict(combo, engine='v2', sharpe=bt.get('sharpe', 0), max_dd=bt.get('max_dd', 0),
                            win_rate=bt.get('win_rate', 0), total_return=bt.get('total_return', 0),
                            total_trades=bt.get('total_trades', 0), total_cost=bt.get('total_cost', 0))
            results.append(item)
            if item['sharpe'] > best_sharpe:
                best_sharpe = item['sharpe']; best_params = item

            with _scan_lock:
                _scan_tasks[task_id].update({'completed': i+1, 'results': results,
                    'best_sharpe': best_sharpe, 'best_params': best_params})

        with _scan_lock:
            _scan_tasks[task_id].update({'status': 'completed', 'results': results,
                'best_sharpe': best_sharpe, 'best_params': best_params})
    except Exception as e:
        _log.error(f"[scan] 失败: {e}")
        with _scan_lock: _scan_tasks[task_id].update({'status': 'failed', 'error': str(e)[:200]})


@router.post("/v1/models/{version}/attribution")
def get_attribution(version: str, body: dict = {}, user: str = Depends(get_current_user)):
    """执行归因分析（三基线 + Brinson分解）。"""
    from scripts.pipeline import run_attribution, build_feature_wide_table
    db = get_sync_db()
    try:
        r = db.execute(text("SELECT feature_list, config FROM model_versions WHERE version=:v"),
                       {"v": version}).fetchone()
        if not r: raise HTTPException(404, "版本不存在")
        feature_names = r[0] if isinstance(r[0], list) else (json.loads(r[0]) if r[0] else [])
        cfg = r[1] if isinstance(r[1], dict) else (json.loads(r[1]) if r[1] else {})
        if not feature_names:
            feature_names = cfg.get('feature_names', [])

        val_start = body.get('val_start', '2022-01-01')
        val_end = body.get('val_end') or str(_dt.today())  # 空 end → 今天（mask 过滤用，不能留空）

        # 宽表终点向后扩 45 自然日：ideal 基线"未来 hold_days 实际涨幅"需要窗外价格
        try:
            wide_end = (_dt.fromisoformat(val_end) + _td(days=45)).isoformat()
        except Exception:
            wide_end = val_end
        df = build_feature_wide_table(db, feature_names, val_start, wide_end, 'stock')
        db.close()
        if df.empty:
            return {"error": "验证集无数据，请先执行特征计算"}

        result = run_attribution(db, version, df, val_start, val_end,
                                 stop_loss=float(body.get('stop_loss', 0.08)),
                                 take_profit=float(body.get('take_profit', 0.15)),
                                 hold_days=int(body.get('hold_days', 10)))
        return result
    except HTTPException:
        raise
    except Exception as e:
        db.close(); raise HTTPException(500, str(e)[:200])


# ── 置换检验（评估器 v2 仪表：打乱预测构造噪声分布）──

_perm_tasks: dict = {}
_perm_lock = _threading.Lock()


class PermutationBody(BaseModel):
    n_perms: int = 20
    val_start: str = ""
    val_end: str = ""
    hold_days: int = 10
    stop_loss: float = 0.05
    take_profit: float = 0.15


def _run_perm(task_id, version, body):
    from scripts.pipeline import build_feature_wide_table, predict_for_version, run_permutation_test
    from app.db.connection import get_sync_db
    from loguru import logger as _log
    try:
        db = get_sync_db()
        row = db.execute(text("SELECT feature_list, config FROM model_versions WHERE version=:v"),
                         {"v": version}).fetchone()
        if not row:
            db.close(); return
        feature_names = row[0] if isinstance(row[0], list) else (json.loads(row[0]) if row[0] else [])
        cfg = row[1] if isinstance(row[1], dict) else (json.loads(row[1]) if row[1] else {})
        if not feature_names:
            feature_names = cfg.get('feature_names', [])
        df = build_feature_wide_table(db, feature_names, body['val_start'], body['val_end'], 'stock')
        if df.empty:
            db.close()
            with _perm_lock: _perm_tasks[task_id].update({'status': 'failed', 'error': '验证集无数据'})
            return
        horizon = 5 if body['hold_days'] <= 5 else (20 if body['hold_days'] > 10 else 10)
        pred, err = predict_for_version(db, version, df, body['val_start'], body['val_end'], horizon)
        db.close()
        if pred is None:
            with _perm_lock: _perm_tasks[task_id].update({'status': 'failed', 'error': err})
            return
        result = run_permutation_test(df, pred, body['val_start'], body['val_end'], body['hold_days'],
                                      body['stop_loss'], body['take_profit'], n_perms=body['n_perms'])
        result = {**result, 'params': body}  # 留档带窗口/参数，前端展示用
        # 留档（每次运行覆盖，历史在日志里）
        db = get_sync_db()
        db.execute(text("UPDATE model_versions SET perm_test=:p WHERE version=:v"),
                   {"p": json.dumps(result), "v": version})
        db.commit(); db.close()
        with _perm_lock:
            _perm_tasks[task_id].update({'status': 'completed', 'result': result})
    except Exception as e:
        _log.error(f"[perm] {version} 置换检验失败: {e}")
        with _perm_lock: _perm_tasks[task_id].update({'status': 'failed', 'error': str(e)[:300]})


@router.post("/v1/models/{version}/permutation")
def start_permutation(version: str, body: PermutationBody, user: str = Depends(get_current_user)):
    """启动置换检验。判据：真预测 sharpe 显著高于打乱后的噪声分布。"""
    db = get_sync_db()
    try:
        r = db.execute(text("SELECT status FROM model_versions WHERE version=:v"), {"v": version}).fetchone()
        db.close()
        if not r:
            raise HTTPException(404, "版本不存在")
        val_end = body.val_end or str(_dt.today())
        val_start = body.val_start or f"{_dt.today().year}-01-01"
        task_id = f"perm-{_uuid.uuid4().hex[:6]}"
        b = {'n_perms': body.n_perms, 'val_start': val_start, 'val_end': val_end,
             'hold_days': body.hold_days, 'stop_loss': body.stop_loss, 'take_profit': body.take_profit}
        with _perm_lock:
            _perm_tasks[task_id] = {'task_id': task_id, 'version': version, 'status': 'running',
                                    'params': b, 'started_at': _time.time()}
        thread = _threading.Thread(target=_run_perm, args=(task_id, version, b), daemon=True)
        thread.start()
        return {"ok": True, "task_id": task_id, "status": "started"}
    except HTTPException:
        raise
    except Exception as e:
        if not db.closed: db.close()
        raise HTTPException(500, str(e))


@router.get("/v1/models/{version}/permutation")
def get_permutation(version: str, task_id: str = Query(None), user: str = Depends(get_current_user)):
    """置换检验结果：带 task_id 轮询任务，否则返回最近一次留档。"""
    if task_id:
        with _perm_lock:
            t = _perm_tasks.get(task_id)
            if not t or t['version'] != version:
                raise HTTPException(404, "任务不存在")
            return dict(t)
    db = get_sync_db()
    try:
        r = db.execute(text("SELECT perm_test FROM model_versions WHERE version=:v"), {"v": version}).fetchone()
        db.close()
        pt = r[0] if isinstance(r[0], dict) else (json.loads(r[0]) if r[0] else None)
        return {"version": version, "perm_test": pt}
    except Exception as e:
        db.close(); raise HTTPException(500, str(e))


# ── Walk-Forward 多窗口稳定性检验 + 晋升门槛 ──

_wf_tasks: dict = {}
_wf_lock = _threading.Lock()


class WalkForwardBody(BaseModel):
    val_start: str = ""
    val_end: str = ""
    n_windows: int = Field(default=4, ge=2, le=8)
    hold_days: int = 10
    stop_loss: float = 0.05
    take_profit: float = 0.15
    baseline_version: Optional[str] = None  # 缺省取当前 ACTIVE（同 entity）


def _pick_baseline(db, version: str, entity: str):
    return db.execute(text(
        "SELECT version FROM model_versions WHERE status='ACTIVE' "
        "AND (config->>'entity') = :ent AND version != :v "
        "ORDER BY activated_at DESC NULLS LAST, created_at DESC LIMIT 1"
    ), {"ent": entity, "v": version}).scalar()


def _run_wf(task_id, version, body):
    from scripts.pipeline import run_walk_forward
    from app.db.connection import get_sync_db
    from loguru import logger as _log
    try:
        db = get_sync_db()
        result = run_walk_forward(db, version, body['baseline_version'], body['val_start'],
                                  body['val_end'], body['n_windows'], body['hold_days'],
                                  body['stop_loss'], body['take_profit'])
        db.close()
        # 落档 version_comparisons：version_a=候选，version_b=基线（无基线时也留档）
        db = get_sync_db()
        base = body['baseline_version'] or version
        sharpe_diff = (result.get('new_mean_sharpe') or 0)
        db.execute(text("""
            INSERT INTO version_comparisons (version_a, version_b, sharpe_diff, winrate_diff, drawdown_diff, report)
            VALUES (:a, :b, :sd, NULL, NULL, :rp)
        """), {"a": version, "b": base, "sd": sharpe_diff, "rp": json.dumps(result, ensure_ascii=False)})
        db.commit(); db.close()
        with _wf_lock:
            _wf_tasks[task_id].update({'status': 'completed', 'result': result})
    except Exception as e:
        _log.error(f"[wf] {version} walk-forward 失败: {e}")
        with _wf_lock: _wf_tasks[task_id].update({'status': 'failed', 'error': str(e)[:300]})


@router.post("/v1/models/{version}/walk-forward")
def start_walk_forward(version: str, body: WalkForwardBody, user: str = Depends(get_current_user)):
    """启动 walk-forward 多窗口检验：新模型 vs 当前 ACTIVE 的稳定性对比与晋升判定。"""
    db = get_sync_db()
    try:
        r = db.execute(text("SELECT status, config FROM model_versions WHERE version=:v"), {"v": version}).fetchone()
        if not r:
            raise HTTPException(404, "版本不存在")
        cfg = r[1] if isinstance(r[1], dict) else (json.loads(r[1]) if r[1] else {})
        entity = cfg.get('entity', 'stock')
        db.close()
        val_end = body.val_end or str(_dt.today())
        val_start = body.val_start or f"{_dt.today().year - 1}-01-01"
        baseline = body.baseline_version or _pick_baseline(get_sync_db(), version, entity)

        task_id = f"wf-{_uuid.uuid4().hex[:6]}"
        b = {'val_start': val_start, 'val_end': val_end, 'n_windows': body.n_windows,
             'hold_days': body.hold_days, 'stop_loss': body.stop_loss,
             'take_profit': body.take_profit, 'baseline_version': baseline}
        with _wf_lock:
            _wf_tasks[task_id] = {'task_id': task_id, 'version': version, 'status': 'running',
                                  'params': b, 'started_at': _time.time()}
        thread = _threading.Thread(target=_run_wf, args=(task_id, version, b), daemon=True)
        thread.start()
        return {"ok": True, "task_id": task_id, "baseline_version": baseline, "status": "started"}
    except HTTPException:
        raise
    except Exception as e:
        if not db.closed: db.close()
        raise HTTPException(500, str(e))


@router.get("/v1/models/{version}/walk-forward")
def get_walk_forward(version: str, task_id: str = Query(None), user: str = Depends(get_current_user)):
    """walk-forward 结果：带 task_id 轮询任务，否则返回最近一次落档对比。"""
    if task_id:
        with _wf_lock:
            t = _wf_tasks.get(task_id)
            if not t or t['version'] != version:
                raise HTTPException(404, "任务不存在")
            return dict(t)
    db = get_sync_db()
    try:
        r = db.execute(text("""
            SELECT report, created_at FROM version_comparisons
            WHERE version_a=:v ORDER BY created_at DESC LIMIT 1
        """), {"v": version}).fetchone()
        db.close()
        report = r[0] if isinstance(r[0], dict) else (json.loads(r[0]) if r[0] else None)
        return {"version": version, "result": report, "created_at": str(r[1]) if r and r[1] else None}
    except Exception as e:
        db.close(); raise HTTPException(500, str(e))
