"""盘中雷达 data_date：快照数据真实所属交易日（盘前/休市日腾讯回退上一交易日）。"""
from app.api.stock import radar_data_date


class TestRadarDataDate:
    def test_picks_latest_trade_date(self):
        quotes = {
            'sh600000': {'code': '600000', 'trade_date': '2026-11-27 15:00:03'},
            'sz000001': {'code': '000001', 'trade_date': '2026-11-27 15:00:07'},
            'sh600519': {'code': '600519', 'trade_date': '2026-11-27 15:00:01'},
        }
        assert radar_data_date(quotes) == '2026-11-27 15:00:07'

    def test_empty_or_missing_dates(self):
        assert radar_data_date({}) is None
        assert radar_data_date({'sh600000': {'code': '600000'}}) is None
        assert radar_data_date(None) is None

    def test_ignores_none_dates(self):
        quotes = {
            'sh600000': {'code': '600000', 'trade_date': None},
            'sz000001': {'code': '000001', 'trade_date': '2026-11-27'},
        }
        assert radar_data_date(quotes) == '2026-11-27'
