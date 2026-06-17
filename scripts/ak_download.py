import sys, time
sys.path.insert(0, '.')
from sqlalchemy import text
from app.db.connection import get_sync_db
from crawler.adapters.akshare_adapter import AKShareAdapter
from crawler.writers import batch_upsert_kline

START, END = '2021-06-16', '2026-06-17'
db = get_sync_db()
adapter = AKShareAdapter()
codes = [r[0] for r in db.execute(text("SELECT stock_code FROM stock_master WHERE stock_type='stock' AND status='N'")).fetchall()]
print(f'total: {len(codes)} stocks')
for i, code in enumerate(codes):
    try:
        rows = adapter.fetch_stock_kline([code], START, END)
        if rows:
            saved = batch_upsert_kline(db, rows)
            if (i+1) % 100 == 0: print(f'[{i+1}/{len(codes)}] {code} +{saved}')
    except Exception as e: print(f'ERR {code}: {e}'); time.sleep(2)
db.close()
print('DONE')
