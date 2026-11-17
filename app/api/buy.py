"""买点检查 API：单票四闸门 + 关注池候选扫描。"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Query

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
):
    db = get_sync_db()
    try:
        return buy_check(db, code, entry, stop, target, qty)
    finally:
        db.close()


@router.get("/candidates")
def get_buy_candidates(limit: int = Query(8, ge=1, le=20)):
    db = get_sync_db()
    try:
        return buy_candidates(db, limit)
    finally:
        db.close()
