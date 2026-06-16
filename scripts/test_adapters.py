"""适配器验证测试 — 测试 4 类数据 x 2 个数据源的可用性。"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import time
from crawler.adapters.baostock_adapter import BaostockAdapter
from crawler.adapters.akshare_adapter import AKShareAdapter

DATE = '2026-06-15'
TEST_STOCK = '000001'
TEST_INDEX = '000001'
TEST_ETF = '510050'

results = []


def test(source_name, adapter, method, args, label):
    try:
        start = time.time()
        data = getattr(adapter, method)(*args)
        elapsed = round(time.time() - start, 2)
        count = len(data) if data else 0
        sample = ''
        if count > 0:
            r = data[0]
            if hasattr(r, 'close') and hasattr(r, 'volume'):
                sample = f'close={r.close} vol={r.volume} hfq={getattr(r, "close_hfq", "")}'
            elif hasattr(r, 'pe_ttm'):
                sample = f'pe={r.pe_ttm} pb={r.pb_mrq} mc={r.market_cap}'
        status = 'OK' if count > 0 else 'EMPTY'
        results.append((source_name, label, status, count, elapsed, sample))
        print(f'  {label}: {status} ({count} rows, {elapsed}s)')
    except Exception as e:
        results.append((source_name, label, 'FAIL', 0, 0, str(e)[:80]))
        print(f'  {label}: FAIL - {str(e)[:80]}')


print(f'=== Baostock (date={DATE}) ===')
bs = BaostockAdapter()
test('baostock', bs, 'fetch_stock_kline', [[TEST_STOCK], DATE, DATE], 'stock_kline')
test('baostock', bs, 'fetch_index_kline', [[TEST_INDEX], DATE, DATE], 'index_kline')
test('baostock', bs, 'fetch_etf_kline', [[TEST_ETF], DATE, DATE], 'etf_kline')
test('baostock', bs, 'fetch_fundamentals', [[TEST_STOCK]], 'fundamentals')

print(f'\n=== AKShare (date={DATE}) ===')
ak = AKShareAdapter()
test('akshare', ak, 'fetch_stock_kline', [[TEST_STOCK], DATE, DATE], 'stock_kline')
test('akshare', ak, 'fetch_index_kline', [[TEST_INDEX], DATE, DATE], 'index_kline')
test('akshare', ak, 'fetch_etf_kline', [[TEST_ETF], DATE, DATE], 'etf_kline')
test('akshare', ak, 'fetch_fundamentals', [[TEST_STOCK]], 'fundamentals')

print('\n' + '=' * 72)
print(f'  DATA SOURCE AVAILABILITY MATRIX  (test date: {DATE})')
print('=' * 72)
print(f'{"Source":<12}{"DataType":<16}{"Status":<8}{"Rows":<6}{"Time":<8}{"Sample Data"}')
print('-' * 72)
for src, label, status, count, elapsed, sample in results:
    icon = '[+]' if status == 'OK' else '[-]' if status == 'EMPTY' else '[X]'
    print(f'{src:<12}{label:<16}{icon:<8}{count:<6}{elapsed:<8}{sample}')

print('\n--- Legend: [+]=available  [-]=no data  [X]=error ---')
ok_count = sum(1 for r in results if r[2] == 'OK')
print(f'\nTotal: {ok_count}/8 combinations available')
