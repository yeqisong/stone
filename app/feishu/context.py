"""动态系统上下文构建器。每次飞书消息到达时，从数据库实时抓取系统快照。"""
import json
from datetime import datetime
from sqlalchemy import text


async def build_system_context(db) -> str:
    """
    从数据库实时抓取系统快照，组装为 DeepSeek System Prompt 上下文。

    上下文内容：
    1. 角色定义（固定）
    2. 当前持仓列表（含盈亏）
    3. 今日策略信号摘要（Top 5）
    4. 系统数据状态
    5. 可用操作清单
    6. 当前时间
    """
    sections = []

    # 1. 角色定义
    sections.append(
        "你是一个股票策略分析助手，运行在用户私有服务器上。\n"
        "你有以下能力：查询行情/策略信号/持仓盈亏，以及引导用户完成持仓管理和策略配置。\n"
        "所有数据来自用户自己维护的数据库，你必须通过函数调用获取，不能编造。\n"
        "回复末尾始终加上风险提示。"
    )

    # 2. 当前持仓
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
        if rows:
            lines = ["## 用户当前持仓"]
            total_value = 0
            for r in rows:
                cost = float(r.cost_price)
                price = float(r.current_price) if r.current_price else cost
                pnl_pct = ((price - cost) / cost * 100) if cost > 0 else 0
                value = price * r.quantity
                total_value += value
                lines.append(
                    f"- {r.stock_name} {r.stock_code}  {r.quantity}股 "
                    f"成本{cost:.2f} 现价{price:.2f} 盈亏{pnl_pct:+.1f}%"
                )
            lines.append(f"总市值: ¥{total_value:,.0f}")
            sections.append("\n".join(lines))
        else:
            sections.append("## 用户当前持仓\n（空仓）")
    except Exception:
        sections.append("## 用户当前持仓\n（查询失败）")

    # 3. 今日信号摘要
    try:
        result = await db.execute(text("""
            SELECT signal_date FROM signal_history
            WHERE combined_signal = true
            ORDER BY signal_date DESC LIMIT 1
        """))
        latest = result.scalar()
        if latest:
            result = await db.execute(text("""
                SELECT stock_code, stock_name, direction, strength, reason
                FROM signal_history
                WHERE signal_date = :d AND combined_signal = true
                ORDER BY strength DESC LIMIT 5
            """), {"d": str(latest)})
            rows = result.fetchall()
            if rows:
                lines = ["## 今日策略信号摘要"]
                for r in rows:
                    emoji = {"buy": "🔴", "sell": "🟢", "neutral": "⚪"}.get(r.direction, "⚪")
                    dir_name = {"buy": "买入", "sell": "卖出", "neutral": "中性"}.get(r.direction, r.direction)
                    lines.append(
                        f"- {emoji} {r.stock_name} {r.stock_code} "
                        f"{dir_name} ★×{r.strength} [{r.reason[:40]}]"
                    )
                sections.append("\n".join(lines))
    except Exception:
        pass

    # 4. 系统状态
    try:
        result = await db.execute(text("SELECT MAX(trade_date) FROM daily_quote"))
        latest_date = result.scalar()
        from datetime import date
        today = date.today()
        result = await db.execute(text(
            "SELECT is_trade_day FROM trade_calendar WHERE cal_date = :d LIMIT 1"
        ), {"d": today})
        row = result.fetchone()
        is_trade = bool(row[0]) if row else None

        result = await db.execute(text(
            "SELECT params FROM strategy_config WHERE strategy_name = 'global_preference'"
        ))
        pref_row = result.fetchone()
        pref_mode = "balanced"
        if pref_row:
            try:
                pref_mode = json.loads(pref_row.params).get("mode", "balanced")
            except (json.JSONDecodeError, TypeError):
                pass

        result = await db.execute(text(
            "SELECT display_name FROM strategy_config WHERE enabled = true AND strategy_name != 'global_preference'"
        ))
        enabled_strategies = [r[0] for r in result.fetchall()]

        sections.append(
            f"## 系统状态\n"
            f"- 最新数据日期: {latest_date}\n"
            f"- 今日是否交易日: {'是' if is_trade else '否' if is_trade is False else '未知'}\n"
            f"- 当前交易偏好: {pref_mode}\n"
            f"- 启用策略: {', '.join(enabled_strategies) if enabled_strategies else '全部'}"
        )
    except Exception:
        sections.append("## 系统状态\n（查询失败）")

    # 5. 可用操作
    sections.append(
        "## 可用操作\n"
        "查询: 查个股详情(需6位代码)、查看持仓、买点扫描、数据状态\n"
        "写入: 建仓/加仓/减仓/清仓 → 系统会先生成确认卡片，用户确认后才执行\n"
        "设置: 调整策略参数（如布林线周期）、切换交易偏好(左侧/右侧/均衡)\n"
        f"\n当前时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}"
    )

    return "\n\n".join(sections)
