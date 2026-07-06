"""KEPL 解析器 API（v2.0 重构 迭代 2.1）。"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/kepl", tags=["kepl"])


class ParseRequest(BaseModel):
    formula: str
    entity: str = "stock"  # stock/etf/index/global


@router.post("/parse")
def parse_formula(body: ParseRequest):
    """解析 KEPL 公式，返回 AST 和错误列表。"""
    from app.kepl.parser import parse_kepl
    return parse_kepl(body.formula, body.entity)
