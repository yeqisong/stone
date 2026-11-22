"""交割单导入 / 明细 / 交易复盘 / 纪律闸门 API。

上传走原始字节流（不依赖 python-multipart），与 agent 附件上传保持一致。
"""
from __future__ import annotations

from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from app.auth.auth import optional_auth, Principal
from app.db.connection import get_sync_db
from app.trade_review import (
    build_review,
    get_discipline,
    save_discipline,
    today_gate,
)
from app.execution_quality import report as execution_report
from app.reduction_plan import build_reduction_plan
from app.trade_slip import (
    TradeParseError,
    apply_positions,
    build_positions,
    delete_batch,
    import_slip,
    list_batches,
    list_trades,
)
from app.trade_intraday import (
    check as intraday_check,
    delete_action as intraday_delete,
    journal_get,
    journal_save,
    quote as intraday_quote,
    reconcile as intraday_reconcile,
    record as intraday_record,
    set_cash as intraday_set_cash,
    state as intraday_state,
)

router = APIRouter(tags=["trades"])
MAX_UPLOAD = 20 * 1024 * 1024


class TradeCheckBody(BaseModel):
    stock_code: str
    side: str = "buy"
    quantity: float = 0
    price: float = 0
    stop_price: Optional[float] = None
    reason: str = ""
    emotion: Optional[str] = None
    cash: Optional[float] = None


class CashBody(BaseModel):
    cash: float


class JournalBody(BaseModel):
    pre_plan: Optional[str] = None
    post_review: Optional[str] = None


class DisciplineBody(BaseModel):
    max_new_buys_per_day: Optional[int] = None
    max_positions: Optional[int] = None
    max_single_weight: Optional[float] = None
    stop_loss_pct: Optional[float] = None
    no_rebuy_days: Optional[int] = None
    no_same_day_roundtrip: Optional[bool] = None
    max_daily_loss_pct: Optional[float] = None
    consecutive_loss_trigger: Optional[int] = None
    max_single_risk_pct: Optional[float] = None
    target_position_ratio: Optional[float] = None
    max_position_ratio: Optional[float] = None
    cash_floor: Optional[float] = None
    max_daily_buy_pct: Optional[float] = None
    risk_per_trade: Optional[float] = None


def _invalidate_watch():
    """记录/删除/现金修正后，让盘中 watcher 下一轮立即重载持仓（best effort）。"""
    try:
        from app.intraday_watch import invalidate_context
        invalidate_context()
    except Exception:
        pass


def _do_import(data: bytes, filename: str, do_apply: bool, user_id: int) -> dict:
    db = get_sync_db()
    try:
        result = import_slip(db, filename, data, user_id)
        try:
            result["reconciled_intraday"] = intraday_reconcile(db, user_id)
        except Exception as e:
            from loguru import logger
            logger.warning(f"[trade-slip] 临时记录对账失败: {e}")
        preview = build_positions(db, user_id)
        if do_apply:
            result["applied"] = apply_positions(db, user_id, preview)
            preview = build_positions(db, user_id)
        result["preview"] = preview
        return result
    finally:
        db.close()


@router.post("/portfolio/trades/import")
async def import_trades(
    request: Request,
    filename: str = Query("交割单.xls", max_length=200),
    apply_positions_now: bool = Query(False, alias="apply"),
    principal: Principal = Depends(optional_auth),
):
    """上传交割单到当前用户（原始字节）。自动去重；默认只入库不改进持仓。"""
    data = await request.body()
    if not data:
        raise HTTPException(400, "文件为空")
    if len(data) > MAX_UPLOAD:
        raise HTTPException(413, "文件超过 20MB，请拆分后再导入")
    try:
        return await run_in_threadpool(_do_import, data, filename,
                                       apply_positions_now, principal.data_user_id)
    except TradeParseError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        from loguru import logger
        logger.exception(f"[trade-slip] 导入失败: {e}")
        raise HTTPException(500, f"导入失败：{str(e)[:160]}")


@router.post("/portfolio/trades/apply-positions")
def sync_positions(principal: Principal = Depends(optional_auth)):
    """按当前用户交割单重建并同步持仓（用户确认后调用）。"""
    uid = principal.data_user_id
    db = get_sync_db()
    try:
        preview = build_positions(db, uid)
        if not preview.get("positions") and not preview.get("diff"):
            raise HTTPException(400, "尚无交割单数据")
        applied = apply_positions(db, uid, preview)
        return {"ok": True, "applied": applied, "preview": build_positions(db, uid)}
    finally:
        db.close()


@router.get("/portfolio/trades")
def get_trades(
    start: Optional[date] = None,
    end: Optional[date] = None,
    code: str = "",
    side: str = "",
    q: str = "",
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=1, le=500),
    principal: Principal = Depends(optional_auth),
):
    """当前用户交割单明细（分页 + 筛选）。"""
    db = get_sync_db()
    try:
        return list_trades(db, principal.data_user_id, start=start, end=end, code=code,
                           side=side, keyword=q, page=page, page_size=page_size)
    finally:
        db.close()


@router.get("/portfolio/trades/intraday/state")
def intraday_guard_state(principal: Principal = Depends(optional_auth)):
    """当前用户实时仓位护栏状态：交割单 + 今日临时记录 + 实时价。"""
    db = get_sync_db()
    try:
        return {"ok": True, "state": intraday_state(db, principal.data_user_id)}
    finally:
        db.close()


@router.get("/portfolio/trades/intraday/quote")
def intraday_guard_quote(code: str, user: str = Depends(optional_auth)):
    db = get_sync_db()
    try:
        return {"ok": True, **intraday_quote(db, code)}
    finally:
        db.close()


@router.post("/portfolio/trades/intraday/check")
def intraday_guard_check(body: TradeCheckBody, principal: Principal = Depends(optional_auth)):
    """下单前检查：返回 ALLOW/BLOCK、最大可买股数、买完后的仓位与风险。"""
    db = get_sync_db()
    try:
        return {"ok": True, "result": intraday_check(db, principal.data_user_id, body.model_dump())}
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        from loguru import logger
        logger.exception(f"[intraday] 检查失败: {e}")
        raise HTTPException(500, f"检查失败：{str(e)[:160]}")
    finally:
        db.close()


@router.post("/portfolio/trades/intraday/record")
def intraday_guard_record(body: TradeCheckBody, principal: Principal = Depends(optional_auth)):
    """记录今天已发生的操作；即使检查会拦截也照实入库，并返回违规提示。"""
    db = get_sync_db()
    try:
        result = intraday_record(db, principal.data_user_id, body.model_dump())
        _invalidate_watch()
        return result
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        from loguru import logger
        logger.exception(f"[intraday] 记录失败: {e}")
        raise HTTPException(500, f"记录失败：{str(e)[:160]}")
    finally:
        db.close()


@router.put("/portfolio/trades/intraday/cash")
def intraday_guard_cash(body: CashBody, principal: Principal = Depends(optional_auth)):
    """修正当前用户券商可用资金（出入金/未记录交易导致的偏差）。"""
    db = get_sync_db()
    try:
        state = intraday_set_cash(db, principal.data_user_id, body.cash)
        _invalidate_watch()
        return {"ok": True, "state": state}
    finally:
        db.close()


@router.delete("/portfolio/trades/intraday/{action_id}")
def intraday_guard_delete(action_id: int, principal: Principal = Depends(optional_auth)):
    db = get_sync_db()
    try:
        state = intraday_delete(db, principal.data_user_id, action_id)
        _invalidate_watch()
        return {"ok": True, "state": state}
    except ValueError as e:
        raise HTTPException(404, str(e))
    finally:
        db.close()


@router.get("/portfolio/trades/journal")
def trade_journal(principal: Principal = Depends(optional_auth)):
    db = get_sync_db()
    try:
        return {"ok": True, **journal_get(db, principal.data_user_id)}
    finally:
        db.close()


@router.put("/portfolio/trades/journal")
def save_trade_journal(body: JournalBody, principal: Principal = Depends(optional_auth)):
    db = get_sync_db()
    try:
        return {"ok": True, **journal_save(db, principal.data_user_id,
                                           body.pre_plan, body.post_review)}
    finally:
        db.close()


@router.get("/portfolio/trades/reduction-plan")
def reduction_plan(target: Optional[float] = Query(None, ge=0.1, le=1.0),
                   max_single: Optional[float] = Query(None, ge=0.05, le=1.0),
                   principal: Principal = Depends(optional_auth)):
    """精确减仓清单：同时满足目标仓位与单票上限，按100股整手。"""
    db = get_sync_db()
    try:
        st = intraday_state(db, principal.data_user_id)
        disc = get_discipline(db, principal.data_user_id)
        t = target if target is not None else float(disc.get("target_position_ratio") or 0.70)
        cap = max_single if max_single is not None else float(disc.get("max_single_weight") or 0.20)
        positions = [{
            "code": p["stock_code"], "name": p["stock_name"],
            "qty": p["quantity"], "price": p["price"],
        } for p in (st.get("positions") or [])]
        return build_reduction_plan(positions, st.get("cash") or 0.0, t, cap)
    finally:
        db.close()


@router.get("/portfolio/trades/execution-quality")
def execution_quality(days: int = Query(30, ge=1, le=180),
                      principal: Principal = Depends(optional_auth)):
    """当前用户卖出执行质量 + 提醒执行率/等待成本。"""
    db = get_sync_db()
    try:
        return execution_report(db, principal.data_user_id, days)
    finally:
        db.close()


@router.get("/portfolio/trades/account-summary")
def account_summary(principal: Principal = Depends(optional_auth)):
    """当前用户账户级累计盈亏（含已清仓、剔除入金）。持仓页统计条使用。"""
    db = get_sync_db()
    try:
        r = build_review(db, principal.data_user_id)
        if not r.get("ok"):
            return {"ok": False, "error": r.get("error") or "尚无交割单数据"}
        s = r["summary"]
        return {
            "ok": True,
            "period": r["period"],
            "trading_pnl": s.get("pnl"),
            "realized_pnl": s.get("realized_pnl"),
            "net_deposit": s.get("net_deposit"),
            "return_pct": s.get("return_pct"),
            "max_drawdown": s.get("max_drawdown"),
            "equity_start": s.get("equity_start"),
            "equity_end": s.get("equity_end"),
            "benchmark_return": s.get("benchmark_return"),
        }
    finally:
        db.close()


@router.get("/portfolio/trades/review")
def review(
    start: Optional[date] = None,
    end: Optional[date] = None,
    benchmark: str = Query("000300", max_length=12),
    principal: Principal = Depends(optional_auth),
):
    """当前用户交易复盘：收益/回撤/行为画像/纪律评分/建议。"""
    db = get_sync_db()
    try:
        r = build_review(db, principal.data_user_id, start=start, end=end,
                         benchmark=benchmark)
        if not r.get("ok"):
            raise HTTPException(400, r.get("error") or "复盘失败")
        return r
    finally:
        db.close()


@router.get("/portfolio/trades/batches")
def batches(limit: int = Query(50, ge=1, le=200),
            principal: Principal = Depends(optional_auth)):
    db = get_sync_db()
    try:
        return {"items": list_batches(db, principal.data_user_id, limit=limit)}
    finally:
        db.close()


@router.delete("/portfolio/trades/batches/{batch_id}")
def remove_batch(batch_id: int, principal: Principal = Depends(optional_auth)):
    """撤销当前用户的一次导入（删除该批次成交；持仓需重新同步）。"""
    db = get_sync_db()
    try:
        r = delete_batch(db, batch_id, principal.data_user_id)
        return {"ok": True, **r, "hint": "成交已删除；如已同步过持仓，请重新导入或手动调整持仓"}
    finally:
        db.close()


@router.get("/portfolio/trades/discipline/rules")
def discipline_rules(principal: Principal = Depends(optional_auth)):
    db = get_sync_db()
    try:
        return {"rules": get_discipline(db, principal.data_user_id)}
    finally:
        db.close()


@router.put("/portfolio/trades/discipline/rules")
def update_discipline_rules(body: DisciplineBody,
                            principal: Principal = Depends(optional_auth)):
    db = get_sync_db()
    try:
        params = {k: v for k, v in body.model_dump().items() if v is not None}
        return {"ok": True, "rules": save_discipline(db, principal.data_user_id, params)}
    finally:
        db.close()


@router.get("/portfolio/trades/discipline/today")
def discipline_today(principal: Principal = Depends(optional_auth)):
    """当前用户今日纪律闸门：开仓数/当日回转/回补/连亏冷静期/集中度。"""
    db = get_sync_db()
    try:
        return today_gate(db, principal.data_user_id)
    finally:
        db.close()
