"""买点检查 API：单票四闸门 + 关注池候选扫描。"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Query, Depends

from app.auth.auth import Principal, get_principal
from app.buy_check import candidates as buy_candidates
from app.buy_check import check as buy_check
from app.db.connection import get_sync_db

router = APIRouter(prefix="/buy", tags=["buy"])


@router.get("/check")
def get_buy_check(
    code: str = Query(..., description="6 位证券代码"),
    entry: Optional[float] = Query(None, description="买点/现价；不传用实时价或结构确认价"),
    stop: Optional[float] = Query(None, description="止损价；不传用结构建议止损"),
    target: Optional[float] = Query(None, description="目标价；不传用结构建议目标"),
    qty: Optional[float] = Query(None, description="计划数量；不传按上限计算"),
    principal: Principal = Depends(get_principal),
):
    db = get_sync_db()
    try:
        # 支持模式（借另一用户身份查看）不落交易计划：GET 不得对被查看用户产生写入
        return buy_check(db, code, entry, stop, target, qty,
                         user_id=principal.data_user_id,
                         persist=not principal.support_mode)
    finally:
        db.close()


@router.get("/candidates")
def get_buy_candidates(limit: int = Query(8, ge=1, le=20),
                       principal: Principal = Depends(get_principal)):
    db = get_sync_db()
    try:
        # 同上：支持模式 GET 只读（与 /api/stock/radar 的 persist 口径一致）
        return buy_candidates(db, limit, user_id=principal.data_user_id,
                              persist=not principal.support_mode)
    finally:
        db.close()
