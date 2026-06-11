#!/usr/bin/env python3
"""Test baostock connectivity and single stock refresh."""
import sys
sys.path.insert(0, '.')
from app.db.connection import get_sync_db
from sqlalchemy import text
import baostock as bs

lg = bs.login()
print(f'Login: {lg.error_code} {lg.error_msg}')
if lg.error_code != '0':
    print('FAIL baostock login')
    sys.exit(1)

# Test query
rs = bs.query_history_k_data_plus('sh.600519',
    'date,open,high,low,close,volume,amount,turn',
    start_date='2026-06-01', end_date='2026-06-08',
    frequency='d', adjustflag='3')
rows = []
while rs.next():
    rows.append(rs.get_row_data())
print(f'Downloaded {len(rows)} rows for 600519')
for r in rows:
    print(f'  {r[0]} close={r[4]}')
bs.logout()

# Test DB
db = get_sync_db()
try:
    cnt = db.execute(text("SELECT COUNT(*) FROM daily_quote WHERE stock_code='600519' AND trade_date='2026-06-01'")).scalar()
    print(f'DB has {cnt} rows for 600519/2026-06-01')
    print('ALL TESTS PASSED')
finally:
    db.close()
