"""Function Calling 工具定义与注册。"""
import json
from dataclasses import dataclass, field
from typing import Callable, Any

from app.config import settings


@dataclass
class QueryResult:
    """查询函数统一返回结构。"""
    success: bool
    data: dict | None = None
    error: str = ""


@dataclass
class ToolDef:
    name: str
    description: str
    parameters: dict
    handler: Callable
    category: str  # 'query' | 'write' | 'admin'


# ── 工具注册表 ──

TOOL_REGISTRY: dict[str, ToolDef] = {}


def register(name: str, description: str, parameters: dict, category: str):
    """装饰器：注册 Function Calling 工具。"""
    def decorator(func):
        TOOL_REGISTRY[name] = ToolDef(
            name=name,
            description=description,
            parameters=parameters,
            handler=func,
            category=category,
        )
        return func
    return decorator


# ── 查询类工具 ──

@register("get_my_portfolio", "获取用户当前持仓列表和浮动盈亏", {
    "type": "object", "properties": {}, "required": []
}, "query")
async def get_my_portfolio(db=None) -> QueryResult:
    from sqlalchemy import text
    try:
        result = await db.execute(text("""
            SELECT p.stock_code, p.stock_name, p.quantity, p.cost_price,
                   COALESCE(
                       (SELECT dq.close_hfq FROM daily_quote dq
                        WHERE dq.stock_code = p.stock_code
                        ORDER BY dq.trade_date DESC LIMIT 1),
                       (SELECT dq.close FROM daily_quote dq
                        WHERE dq.stock_code = p.stock_code
                        ORDER BY dq.trade_date DESC LIMIT 1)
                   ) AS current_price
            FROM portfolio p WHERE p.is_active = true
        """))
        rows = result.fetchall()
        positions = []
        for r in rows:
            cost = float(r.cost_price)
            price = float(r.current_price) if r.current_price else cost
            pnl_pct = ((price - cost) / cost * 100) if cost > 0 else 0
            positions.append({
                "stock_code": r.stock_code,
                "stock_name": r.stock_name,
                "quantity": r.quantity,
                "cost_price": cost,
                "current_price": round(price, 2),
                "pnl_pct": round(pnl_pct, 1),
            })
        return QueryResult(success=True, data={"positions": positions, "count": len(positions)})
    except Exception as e:
        return QueryResult(success=False, error=str(e))


@register("get_stock_detail", "获取指定股票的最近策略分析信号", {
    "type": "object",
    "properties": {"code": {"type": "string", "description": "6位股票代码"}},
    "required": ["code"]
}, "query")
async def get_stock_detail(code: str, db=None) -> QueryResult:
    from sqlalchemy import text
    try:
        result = await db.execute(text("""
            SELECT stock_code, stock_name, signal_date, direction, strength,
                   strategy_name, reason, price, suggested_action,
                   combined_signal, source_strategies, preference
            FROM signal_history
            WHERE stock_code = :code
            ORDER BY signal_date DESC, combined_signal DESC
            LIMIT 10
        """), {"code": code})
        rows = result.fetchall()
        if not rows:
            return QueryResult(success=False, error=f"未找到股票 {code} 的策略信号")

        signals = []
        for r in rows:
            source = r.source_strategies
            if isinstance(source, str):
                try:
                    source = json.loads(source)
                except (json.JSONDecodeError, TypeError):
                    source = [source] if source else []
            signals.append({
                "signal_date": str(r.signal_date), "direction": r.direction,
                "strength": r.strength, "strategy_name": r.strategy_name,
                "reason": r.reason, "price": float(r.price) if r.price else 0,
                "suggested_action": r.suggested_action or "",
                "combined_signal": r.combined_signal,
                "source_strategies": source,
            })
        return QueryResult(success=True, data={
            "stock_code": rows[0].stock_code,
            "stock_name": rows[0].stock_name,
            "signals": signals,
        })
    except Exception as e:
        return QueryResult(success=False, error=str(e))


@register("get_buy_signals", "获取最近全市场买点扫描结果", {
    "type": "object",
    "properties": {"top_n": {"type": "integer", "description": "返回前N只", "default": 10}},
    "required": []
}, "query")
async def get_buy_signals(top_n: int = 10, db=None) -> QueryResult:
    from sqlalchemy import text
    try:
        result = await db.execute(text("""
            SELECT MAX(signal_date) FROM signal_history WHERE combined_signal = true
        """))
        latest = result.scalar()
        if not latest:
            return QueryResult(success=False, error="暂无买点扫描数据")

        result = await db.execute(text("""
            SELECT stock_code, stock_name, direction, strength, reason, price,
                   suggested_action, source_strategies
            FROM signal_history
            WHERE signal_date = :d AND direction = 'buy' AND combined_signal = true
            ORDER BY strength DESC LIMIT :n
        """), {"d": str(latest), "n": top_n})
        rows = result.fetchall()
        signals = []
        for r in rows:
            source = r.source_strategies
            if isinstance(source, str):
                try:
                    source = json.loads(source)
                except (json.JSONDecodeError, TypeError):
                    source = []
            signals.append({
                "stock_code": r.stock_code, "stock_name": r.stock_name,
                "strength": r.strength, "reason": r.reason,
                "price": float(r.price) if r.price else 0,
                "suggested_action": r.suggested_action or "",
                "source_strategies": source,
            })
        return QueryResult(success=True, data={"signal_date": str(latest), "signals": signals})
    except Exception as e:
        return QueryResult(success=False, error=str(e))


@register("get_data_status", "获取系统数据采集状态", {
    "type": "object", "properties": {}, "required": []
}, "query")
async def get_data_status(db=None) -> QueryResult:
    from sqlalchemy import text
    from datetime import date
    try:
        result = await db.execute(text("SELECT MAX(trade_date) FROM daily_quote"))
        latest = result.scalar()
        today = date.today()
        result = await db.execute(text(
            "SELECT is_trade_day FROM trade_calendar WHERE cal_date = :d LIMIT 1"
        ), {"d": today})
        row = result.fetchone()
        is_trade_day = bool(row[0]) if row else None
        return QueryResult(success=True, data={
            "latest_trade_date": str(latest) if latest else None,
            "today": str(today),
            "is_trade_day": is_trade_day,
        })
    except Exception as e:
        return QueryResult(success=False, error=str(e))


@register("get_strategy_config", "查看当前策略配置（启用状态和参数）", {
    "type": "object", "properties": {}, "required": []
}, "query")
async def get_strategy_config(db=None) -> QueryResult:
    from sqlalchemy import text
    try:
        result = await db.execute(text(
            "SELECT strategy_name, display_name, enabled, params FROM strategy_config"
        ))
        rows = result.fetchall()
        configs = []
        for r in rows:
            params = r.params
            if isinstance(params, str):
                try:
                    params = json.loads(params)
                except (json.JSONDecodeError, TypeError):
                    params = {}
            configs.append({
                "strategy_name": r.strategy_name,
                "display_name": r.display_name,
                "enabled": r.enabled,
                "params": params,
            })
        return QueryResult(success=True, data={"strategies": configs})
    except Exception as e:
        return QueryResult(success=False, error=str(e))


# ── 写入类工具（返回确认卡片数据，调用方负责发送飞书卡片）──

@register("confirm_add_portfolio", "用户表达了建仓意图，提取信息生成确认卡片", {
    "type": "object",
    "properties": {
        "stock_code": {"type": "string", "description": "6位股票代码"},
        "stock_name": {"type": "string", "description": "股票名称"},
        "quantity": {"type": "integer", "description": "数量（股）"},
        "cost_price": {"type": "number", "description": "成本价"},
    },
    "required": ["stock_code", "quantity", "cost_price"]
}, "write")
async def confirm_add_portfolio(stock_code: str, quantity: int, cost_price: float,
                                 stock_name: str = "", db=None) -> QueryResult:
    return QueryResult(success=True, data={
        "action": "confirm_card",
        "card_type": "add_portfolio",
        "stock_code": stock_code,
        "stock_name": stock_name or stock_code,
        "quantity": quantity,
        "cost_price": cost_price,
        "total_cost": round(quantity * cost_price, 2),
    })


@register("confirm_set_preference", "切换全局交易偏好", {
    "type": "object",
    "properties": {
        "mode": {"type": "string", "enum": ["left", "right", "balanced"],
                 "description": "交易偏好: left=左侧, right=右侧, balanced=均衡"}
    },
    "required": ["mode"]
}, "write")
async def confirm_set_preference(mode: str, db=None) -> QueryResult:
    mode_names = {"left": "左侧交易", "right": "右侧交易", "balanced": "均衡"}
    return QueryResult(success=True, data={
        "action": "confirm_card",
        "card_type": "set_preference",
        "mode": mode,
        "mode_name": mode_names.get(mode, mode),
    })


# ── 管理类工具 ──

@register("trigger_recalculation", "手动触发全市场策略重算（使用已有行情数据）", {
    "type": "object",
    "properties": {"scope": {"type": "string", "default": "all"}},
    "required": []
}, "admin")
async def trigger_recalculation(scope: str = "all", db=None) -> QueryResult:
    return QueryResult(success=True, data={
        "action": "recalculate",
        "message": "策略重算已触发，请稍候查看结果",
        "scope": scope,
    })


def get_tools_for_deepseek() -> list:
    """生成 DeepSeek API 格式的工具列表。"""
    tools = []
    for name, td in TOOL_REGISTRY.items():
        tools.append({
            "type": "function",
            "function": {
                "name": name,
                "description": td.description,
                "parameters": td.parameters,
            }
        })
    return tools
