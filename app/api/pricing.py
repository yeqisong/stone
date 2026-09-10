"""后复权价 → 真实价换算层（口径隔离的唯一入口）。

模型/信号/纸面组合全链路使用后复权价（daily_quote.close_hfq）以保证收益曲线
不受除权除息干扰，撮合价与信号价因此也落在后复权空间并原样落库
（paper_trades.price / paper_positions.buy_price / signal_history.price）。
这些数字不是市价——累计复权因子可达数十倍（如爱尔眼科 66×），一旦被界面
当市价直接渲染，就会出现「556.46 元的爱尔眼科」这类数量级错误。

约定（新增界面/接口必须遵守）：
- 回给前端的展示价一律是**真实价**，字段名用 price / buy_price；
- 后复权原值保留在同名 *_hfq 字段，仅供比率与盈亏计算（比率必须同口径）；
- 换算一律经 hfq_to_raw_factors()，不要在接口里手写 close_hfq 相关逻辑，
  也不要新增只返回后复权价的字段（那正是本模块要杜绝的误读来源）。
"""
from typing import Dict, Iterable, Optional, Tuple

from sqlalchemy import text


def hfq_to_raw_factors(db, keys: Iterable[Tuple[str, object]]) -> Dict[Tuple[str, str], float]:
    """{(stock_code, 'YYYY-MM-DD'): 真实价/后复权价}，取自同日 daily_quote。

    用 close/close_hfq 的比值而非 adj_factor_hfq 列：后者恒为 1.0（DDL 默认值，
    每日 UPSERT 不写该列，只有存量回补 UPDATE 写过），拿它换算会原样返回后复权价。
    用比值缩放还能顺带保留撮合价里的滑点/冲击成本比例。

    缺行情的 (code, date) 不会出现在结果里——调用方应把价格置 None 让界面显示
    「—」，不要回退成后复权值。
    """
    pairs = {(str(code), str(d)[:10]) for code, d in keys if code and d}
    if not pairs:
        return {}
    codes = sorted({c for c, _ in pairs})
    dates = sorted({d for _, d in pairs})
    fac: Dict[Tuple[str, str], float] = {}
    rows = db.execute(text("""
        SELECT stock_code, trade_date, close, close_hfq
        FROM daily_quote
        WHERE stock_code = ANY(:c) AND trade_date = ANY(CAST(:d AS date[]))
          AND close IS NOT NULL AND close > 0
          AND close_hfq IS NOT NULL AND close_hfq > 0
    """), {"c": codes, "d": dates}).fetchall()
    for code, d, close, hfq in rows:
        fac[(code, str(d)[:10])] = float(close) / float(hfq)
    return fac


def raw_price(price_hfq: Optional[float], factor: Optional[float]) -> Optional[float]:
    """后复权价 → 真实价（保留原价的滑点比例）；无行情因子时回 None。

    绝不回退返回后复权原值：宁可在界面显示「—」，也不要再显示一个错的数量级。
    """
    if price_hfq is None or not factor:
        return None
    return round(float(price_hfq) * float(factor), 4)
