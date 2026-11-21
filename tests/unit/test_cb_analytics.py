"""可转债规则分析/采集映射单元测试。"""
import unittest
from unittest.mock import MagicMock, patch

from app.cb_analytics import (
    beta,
    bond_ytm,
    conversion_value,
    daily_returns,
    days_until,
    double_low,
    forced_redemption_days,
    linkage_label,
    linkage_metrics,
    pearson_corr,
    premium_rate,
    price_percentile,
    pure_bond_premium,
    pure_bond_value,
    rolling_corr,
    score_bond,
    stock_to_force_pct,
    strategy_keys,
    turnover_pct,
    period_return,
    watch_candidates,
    watch_triggers,
    build_strategy_pools,
)
from crawler.adapters.tushare_adapter import TuShareAdapter


class TestCVMetrics(unittest.TestCase):
    def test_conversion_value(self):
        self.assertEqual(conversion_value(10, 12), 120.0)
        self.assertIsNone(conversion_value(0, 12))
        self.assertIsNone(conversion_value(10, None))

    def test_premium_and_double_low(self):
        cv = conversion_value(10, 10)
        self.assertEqual(cv, 100)
        self.assertAlmostEqual(premium_rate(110, cv), 0.1)
        self.assertAlmostEqual(double_low(110, 0.1), 120.0)
        self.assertIsNone(premium_rate(110, None))

    def test_pure_bond_value(self):
        v = pure_bond_value(2.0, 6.0)
        self.assertIsNotNone(v)
        self.assertLess(v, 100)
        self.assertIsNone(pure_bond_value(None, 6.0))
        self.assertIsNone(pure_bond_value(2.0, None))
        # 剩余期限越短、越接近面值：债底必须按剩余期限折现，而非债券总期限
        self.assertGreater(pure_bond_value(2.0, 1.0), pure_bond_value(2.0, 6.0))

    def test_bond_ytm(self):
        # 111 元买入、票息 2.3%、剩余 1.1 年 → 持有到期年化约 -7%
        y = bond_ytm(111, 2.3, 1.1)
        self.assertIsNotNone(y)
        self.assertLess(y, 0)
        self.assertGreater(y, -0.15)
        # 平价买入近似票息
        self.assertAlmostEqual(bond_ytm(100, 2.3, 1.1), 0.023, places=2)
        self.assertIsNone(bond_ytm(None, 2.3, 1.1))
        self.assertIsNone(bond_ytm(111, 2.3, 0))

    def test_turnover(self):
        # amount 万元 → 元；remain 元
        self.assertAlmostEqual(turnover_pct(100, 1e8), 100 * 10000 / 1e8 * 100)
        self.assertIsNone(turnover_pct(0, 1e8))

    def test_days_until(self):
        self.assertEqual(days_until('2026-12-31', '2026-12-01'), 30)
        self.assertEqual(days_until('2026-11-30', '2026-12-01'), -1)
        self.assertIsNone(days_until(None, '2026-12-01'))

    def test_force_line(self):
        self.assertAlmostEqual(stock_to_force_pct(13.0, 10.0), 0.0)
        self.assertIsNone(stock_to_force_pct(None, 10))
        self.assertEqual(forced_redemption_days([13.0] * 20 + [10.0] * 10, 10), 20)

    def test_price_percentile(self):
        hist = [100, 100, 105, 105, 110, 110, 110, 120, 120, 130]
        self.assertEqual(price_percentile(110, hist), 70.0)
        self.assertIsNone(price_percentile(110, []))


class TestCBScore(unittest.TestCase):
    def _base_item(self, **over):
        item = {
            'price': 110, 'premium_rate': 0.1, 'double_low': 120,
            'remaining_size': 5e8, 'pure_bond_premium': 0.05,
            'turnover_pct': 1.0, 'stock_close': 12, 'stock_ma20': 11, 'stock_ma60': 10,
            'days_to_maturity': 1000, 'days_to_conv_stop': 1000, 'days_to_delist': None,
        }
        item.update(over)
        return item

    def test_strong_low_premium(self):
        s = score_bond(self._base_item())
        self.assertGreaterEqual(s['score'], 70)
        self.assertEqual(s['label'], '进攻')
        self.assertTrue(any('低溢价' in r or '溢价率极低' in r for r in s['reasons']))

    def test_high_price_high_premium(self):
        s = score_bond(self._base_item(price=180, premium_rate=0.9, double_low=260,
                                       pure_bond_premium=0.6, remaining_size=1e7,
                                       stock_close=10, stock_ma20=11, stock_ma60=12,
                                       days_to_maturity=60))
        self.assertLess(s['score'], 20)
        self.assertEqual(s['label'], '回避')
        self.assertIn('高价位', s['risk_flags'])
        self.assertIn('高溢价', s['risk_flags'])
        self.assertIn('临近到期', s['risk_flags'])

    def test_strategies(self):
        self.assertIn('low_premium', strategy_keys(self._base_item()))
        self.assertIn('double_low', strategy_keys(self._base_item(premium_rate=0.3, double_low=140)))
        self.assertIn('risk', strategy_keys(self._base_item(days_to_maturity=30)))
        pools = build_strategy_pools([self._base_item(), self._base_item(price=180, premium_rate=0.9)])
        self.assertGreaterEqual(len(pools['low_premium']), 1)
        self.assertGreaterEqual(len(pools['risk']), 1)


class TestCBWatch(unittest.TestCase):
    def _base_item(self, **over):
        item = {
            'bond_code': '113052', 'bond_name': '兴业转债',
            'price': 110, 'premium_rate': 0.1, 'double_low': 120,
            'remaining_size': 5e8, 'pure_bond_premium': 0.05,
            'turnover_pct': 1.0, 'stock_close': 12, 'stock_ma20': 11, 'stock_ma60': 10,
            'days_to_maturity': 1000, 'days_to_conv_stop': 1000, 'days_to_delist': None,
        }
        item.update(over)
        item['strategies'] = strategy_keys(item)
        item.update(score_bond(item))
        return item

    def test_watch_triggers_include_premium_and_momentum(self):
        self.assertTrue(any('溢价升破' in x for x in watch_triggers(self._base_item())))
        t = watch_triggers(self._base_item(stock_close=10, stock_ma20=11))
        self.assertTrue(any('MA20' in x for x in t))

    def test_watch_candidates_focus_and_risk(self):
        good = self._base_item()
        bad = self._base_item(bond_code='111012', price=180, premium_rate=0.9,
                              double_low=260, remaining_size=1e7,
                              stock_close=10, stock_ma20=11, stock_ma60=12,
                              days_to_maturity=60)
        r = watch_candidates([good, bad], limit=8)
        self.assertEqual([x['bond_code'] for x in r['focus']], ['113052'])
        self.assertTrue(any(x['bond_code'] == '111012' for x in r['risk']))
        self.assertTrue(all(x.get('watch_triggers') is not None for x in r['focus'] + r['risk']))


class TestCBLinkage(unittest.TestCase):
    def test_daily_returns(self):
        r = daily_returns([100, 110, None, 99])
        self.assertIsNone(r[0])
        self.assertAlmostEqual(r[1], 0.1)
        self.assertIsNone(r[2])
        self.assertIsNone(r[3])
        self.assertIsNone(daily_returns([100])[0])

    def test_period_return(self):
        self.assertAlmostEqual(period_return([100, 100, 110, 120], 3), 0.2)
        self.assertIsNone(period_return([100, 110], 3))

    def test_pearson_and_beta(self):
        xs = [0.01, 0.02, -0.01, 0.03, 0.00]
        ys = [0.02, 0.04, -0.02, 0.06, 0.00]
        self.assertAlmostEqual(pearson_corr(xs, ys), 1.0)
        self.assertAlmostEqual(beta(xs, ys), 2.0)
        self.assertIsNone(pearson_corr([0.1], [0.1]))
        self.assertIsNone(pearson_corr([0.1, 0.1, 0.1], [0.2, 0.2, 0.2]))

    def test_rolling_corr(self):
        xs = [0.01, 0.02, 0.03, 0.04, 0.05]
        ys = [0.02, 0.04, 0.06, 0.08, 0.10]
        r = rolling_corr(xs, ys, 3)
        self.assertIsNone(r[0])
        self.assertIsNone(r[1])
        self.assertAlmostEqual(r[2], 1.0)
        self.assertAlmostEqual(r[3], 1.0)

    def test_linkage_metrics_and_label(self):
        xs = [0.01, 0.02, -0.01, 0.03, 0.00]
        ys = [0.02, 0.04, -0.02, 0.06, 0.00]
        m = linkage_metrics(xs, ys)
        self.assertAlmostEqual(m['corr_full'], 1.0)
        self.assertAlmostEqual(m['beta'], 2.0)
        self.assertIn('强联动', linkage_label(m['corr_full']))
        self.assertIn('跑输', linkage_label(m['corr_full'], -0.03))


class TestCBAdapterMapping(unittest.TestCase):
    def _adapter(self):
        with patch('tushare.pro_api'):
            a = TuShareAdapter.__new__(TuShareAdapter)
            a._pro = MagicMock()
            a.quota = MagicMock()
            return a

    def test_fetch_cb_basic_normalizes_codes(self):
        a = self._adapter()
        import pandas as pd
        a._pro.cb_basic.return_value = pd.DataFrame([{
            'ts_code': '113052.SH', 'bond_short_name': '兴业转债',
            'stk_code': '601166.SH', 'exchange': 'SH',
            'list_date': '20200101', 'conv_price': 20.3,
        }])
        rows = a.fetch_cb_basic()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['bond_code'], '113052')
        self.assertEqual(rows[0]['stk_code'], '601166')
        self.assertEqual(rows[0]['list_date'], '2020-01-01')

    def test_fetch_cb_basic_empty_stock_is_null(self):
        a = self._adapter()
        import pandas as pd
        a._pro.cb_basic.return_value = pd.DataFrame([{
            'ts_code': '121001.SZ', 'bond_short_name': '旧债', 'stk_code': '',
        }])
        rows = a.fetch_cb_basic()
        self.assertIsNone(rows[0]['stk_code'])

    def test_fetch_cb_daily_normalizes(self):
        a = self._adapter()
        import pandas as pd
        a._pro.cb_daily.return_value = pd.DataFrame([{
            'ts_code': '113052.SH', 'trade_date': '20261120', 'close': 111.0,
            'vol': 100, 'amount': 1000,
        }])
        rows = a.fetch_cb_daily('2026-11-20')
        self.assertEqual(rows[0]['bond_code'], '113052')
        self.assertEqual(rows[0]['trade_date'], '2026-11-20')

    def test_fetch_cb_daily_drops_zero_close(self):
        a = self._adapter()
        import pandas as pd
        a._pro.cb_daily.return_value = pd.DataFrame([
            {'ts_code': '113052.SH', 'trade_date': '20261120', 'close': 0, 'vol': 0, 'amount': 0},
            {'ts_code': '127001.SZ', 'trade_date': '20261120', 'close': 120, 'vol': 10, 'amount': 1200},
        ])
        rows = a.fetch_cb_daily('2026-11-20')
        self.assertEqual([r['bond_code'] for r in rows], ['127001'])

    def test_fetch_cb_share_announcement_rows(self):
        a = self._adapter()
        import pandas as pd
        a._pro.cb_share.return_value = pd.DataFrame([
            {'ts_code': '113052.SH', 'publish_date': '20261119', 'end_date': '20261118',
             'convert_price': 20.3, 'remain_size': 1e9},
            {'ts_code': '113052.SH', 'publish_date': '20261120', 'end_date': '20261119',
             'convert_price': 20.3, 'remain_size': 9e8},
            {'ts_code': '127001.SZ', 'end_date': '20261120', 'convert_price': 8, 'remain_size': 1e8},
        ])
        rows = a.fetch_cb_share('2026-11-20')
        self.assertEqual([r['bond_code'] for r in rows], ['113052', '113052', '127001'])
        # 快照日优先用公告日 publish_date；缺失时才回退 end_date
        self.assertEqual(rows[0]['asof_date'], '2026-11-19')
        self.assertEqual(rows[1]['asof_date'], '2026-11-20')
        self.assertEqual(rows[2]['asof_date'], '2026-11-20')

    def test_fetch_cb_share_history_infers_bond_exchange(self):
        a = self._adapter()
        import pandas as pd
        a._pro.cb_share.return_value = pd.DataFrame([
            {'ts_code': '113052.SH', 'publish_date': '20200101', 'convert_price': 20.3},
            {'ts_code': '113052.SH', 'publish_date': '20210101', 'convert_price': 18.0},
        ])
        rows = a.fetch_cb_share_history('113052')
        a._pro.cb_share.assert_called_with(ts_code='113052.SH')
        self.assertEqual(len(rows), 2)


if __name__ == '__main__':
    unittest.main()
