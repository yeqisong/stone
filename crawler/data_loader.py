"""策略引擎数据加载器。从数据库读取股票行情数据。"""
import pandas as pd
from sqlalchemy import text
from datetime import date
from typing import List, Optional

from app.db.connection import get_sync_db


class StrategyDataLoader:
    """从数据库加载行情数据供策略引擎使用。"""

    def __init__(self):
        self.db = get_sync_db()

    def close(self):
        self.db.close()

    def get_all_codes(self) -> List[str]:
        """获取需要计算的所有股票代码。排除ST/退市，包含有行情数据的股票。"""
        result = self.db.execute(text("""
            SELECT sm.stock_code FROM stock_master sm
            WHERE sm.status = 'N'
              AND EXISTS (
                  SELECT 1 FROM daily_quote dq
                  WHERE dq.stock_code = sm.stock_code
              )
        """))
        return [r[0] for r in result.fetchall()]

    def load_stock_data(self, stock_code: str) -> Optional[pd.DataFrame]:
        """加载单只股票的全部历史日线数据（按 trade_date 升序）。"""
        result = self.db.execute(text("""
            SELECT dq.trade_date, dq.open, dq.high, dq.low,
                   COALESCE(dq.close_hfq, dq.close) as close,
                   dq.volume, dq.stock_code,
                   COALESCE(NULLIF(dq.stock_name,''), sm.stock_name, dq.stock_code) as stock_name
            FROM daily_quote dq
            LEFT JOIN stock_master sm ON sm.stock_code = dq.stock_code
            WHERE dq.stock_code = :c
            ORDER BY dq.trade_date ASC
        """), {"c": stock_code})
        rows = result.fetchall()
        if not rows:
            return None

        return pd.DataFrame(rows, columns=[
            'trade_date', 'open', 'high', 'low', 'close',
            'volume', 'stock_code', 'stock_name'
        ])

    def load_all_stocks_data(self, min_trade_date: str = None) -> dict:
        """加载全市场行情数据，返回 {stock_code: DataFrame}。

        min_trade_date: 只加载此日期之后的数据（策略最多需要 200 个交易日历史）。
        """
        sql = """
            SELECT trade_date, stock_code, stock_name, open, high, low,
                   COALESCE(close_hfq, close) as close, volume
            FROM daily_quote
            WHERE stock_code IN (SELECT stock_code FROM stock_master WHERE status = 'N')
        """
        params = {}
        if min_trade_date:
            sql += " AND trade_date >= :min_date"
            params["min_date"] = min_trade_date
        sql += " ORDER BY trade_date ASC"
        result = self.db.execute(text(sql), params)
        rows = result.fetchall()

        # 按股票分组
        data_by_stock = {}
        for r in rows:
            code = r.stock_code
            if code not in data_by_stock:
                data_by_stock[code] = []
            data_by_stock[code].append(r)

        # 转为 DataFrame
        result_dict = {}
        for code, rows_list in data_by_stock.items():
            result_dict[code] = pd.DataFrame(rows_list, columns=[
                'trade_date', 'stock_code', 'stock_name', 'open', 'high',
                'low', 'close', 'volume'
            ])
        return result_dict
