"""交割单解析/持仓重放纯逻辑单测（无 DB）。"""
import datetime

from app.trade_slip import (
    _apply_row,
    _normalize_code,
    _normalize_side,
    _pre_balance,
    _row_hash,
    _to_date,
    _to_float,
    parse_slip_bytes,
    replay_positions,
)


def _tsv(rows: list[str], header: str | None = None) -> bytes:
    head = header or (
        "成交日期\t成交时间\t证券代码\t证券名称\t买卖标志\t成交数量\t成交编号\t成交价格\t"
        "成交金额\t股份余额\t清算金额\t手续费\t印花税\t附加费\t资金本次余额\t业务名称\t资金帐号"
    )
    return (head + "\n" + "\n".join(rows) + "\n").encode("gb18030")


class TestFieldParsing:
    def test_date_formats(self):
        assert _to_date("20260105") == datetime.date(2026, 1, 5)
        assert _to_date("2026-01-05") == datetime.date(2026, 1, 5)
        assert _to_date("2026/01/05") == datetime.date(2026, 1, 5)
        assert _to_date("") is None

    def test_float_formats(self):
        assert _to_float("1,234.50") == 1234.5
        assert _to_float("--") is None
        assert _to_float("") is None
        assert _to_float("−8.5") == -8.5

    def test_stock_code_numeric_excel(self):
        assert _normalize_code(600000.0) == "600000"
        assert _normalize_code(1.0) == "000001"
        assert _normalize_code(1) == "000001"
        assert _normalize_code("600000.0") == "600000"
        assert _normalize_code("000001.0") == "000001"
        assert _normalize_code("000001") == "000001"
        assert _normalize_code("600000.SH") == "600000"
        assert _normalize_code("1.5") == ""
        assert _normalize_code(1234567.0) == ""
        assert _normalize_code(0) == "000000"

    def test_side(self):
        assert _normalize_side("证券买入", "")[0] == "buy"
        assert _normalize_side("证券卖出", "")[0] == "sell"
        assert _normalize_side("股息税", "股息红利税补缴") == ("other", "tax")
        assert _normalize_side("送股", "红股入账") == ("other", "bonus")
        assert _normalize_side("红利", "股息入账") == ("other", "dividend")


class TestParse:
    def test_xlsx_numeric_codes(self):
        """Excel 数值单元格不能把 000001 解析成 000010 / 600000 解析成 700 万。"""
        import io
        from openpyxl import Workbook
        wb = Workbook(); ws = wb.active
        ws.append(["成交日期", "成交时间", "证券代码", "证券名称", "买卖标志",
                   "成交数量", "成交编号", "成交价格", "成交金额", "股份余额",
                   "清算金额", "手续费", "印花税", "附加费", "资金本次余额",
                   "业务名称", "资金帐号"])
        ws.append([20260105, "09:30:00", 1.0, "平安银行", "买入", 100, "B1",
                   10.0, 1000.0, 100, -1005.0, 5.0, 0.0, 0.0, 8995.0, "证券买入", "A1"])
        ws.append([20260105, "09:31:00", 600000.0, "浦发银行", "买入", 100, "B2",
                   10.0, 1000.0, 100, -1005.0, 5.0, 0.0, 0.0, 7990.0, "证券买入", "A1"])
        buf = io.BytesIO(); wb.save(buf)
        r = parse_slip_bytes(buf.getvalue())
        assert [x["stock_code"] for x in r["rows"]] == ["000001", "600000"]

    def test_gbk_tsv(self):
        data = _tsv([
            "20260105\t09:30:00\t600000\t浦发银行\t买入\t100\tB1\t10.00\t1000\t100\t-1005\t5\t0\t0\t8995\t证券买入\tA1",
            "20260105\t10:00:00\t600000\t浦发银行\t卖出\t100\tB2\t11.00\t1100\t0\t1094\t5\t1\t0\t10088\t证券卖出\tA1",
            "20260106\t17:00:00\t600000\t浦发银行\t红利\t0\t0\t0\t0\t100\t50\t0\t0\t0\t10138\t股息入账\tA1",
        ])
        r = parse_slip_bytes(data)
        assert r["meta"]["rows_total"] == 3
        assert r["meta"]["accounts"] == ["A1"]
        assert [x["side"] for x in r["rows"]] == ["buy", "sell", "other"]
        assert r["rows"][0]["stock_code"] == "600000"
        assert r["rows"][0]["order_no"] == "B1"
        # 红利行成交编号为 0 → 不能进唯一键
        assert r["rows"][2]["order_no"] == ""
        # 红利行时间 17:00 保留
        assert r["rows"][2]["trade_time"] == "17:00:00"

    def test_header_synonyms(self):
        header = "交易日期\t交易时间\t股票代码\t证券名称\t买卖方向\t成交股数\t成交价格\t成交金额\t发生金额\t手续费\t资金余额"
        data = _tsv([
            "20260105\t09:30:00\t600000\t浦发银行\t买入\t100\t10.00\t1000\t-1005\t5\t8995",
        ], header=header)
        r = parse_slip_bytes(data)
        assert r["rows"][0]["side"] == "buy"
        assert r["rows"][0]["price"] == 10.0
        assert r["rows"][0]["clean_amount"] == -1005.0

    def test_skips_summary_rows(self):
        data = _tsv([
            "合计\t\t\t\t\t100\t\t\t1000\t\t\t\t\t\t\t\t",
            "20260105\t09:30:00\t600000\t浦发银行\t买入\t100\tB1\t10.00\t1000\t100\t-1005\t5\t0\t0\t8995\t证券买入\tA1",
        ])
        r = parse_slip_bytes(data)
        assert r["meta"]["rows_total"] == 1
        assert r["meta"]["rows_skipped"] == 1

    def test_zero_time_other_moves_to_end_of_day(self):
        data = _tsv([
            "20260105\t09:30:00\t600000\t浦发银行\t买入\t100\tB1\t10.00\t1000\t100\t-1005\t5\t0\t0\t8995\t证券买入\tA1",
            "20260106\t0\t600000\t浦发银行\t股息税\t100\t0\t10.00\t1\t0\t-1\t0\t0\t0\t9994\t股息红利税补缴\tA1",
        ])
        r = parse_slip_bytes(data)
        assert r["rows"][1]["trade_time"] == "23:59:59"

    def test_row_hash_stable_and_distinct(self):
        a = dict(account_no="A1", trade_date="2026-01-05", trade_time="09:30:00",
                 stock_code="600000", side="buy", quantity=100, price=10, amount=1000,
                 business_name="证券买入", clean_amount=-1005)
        b = dict(a)
        assert _row_hash(a) == _row_hash(b)
        c = dict(a, trade_time="09:31:00")
        assert _row_hash(a) != _row_hash(c)
        # 红利/税行的 hash 不含时间（时间常被写成 0，归一化不应造成重复导入）
        d = dict(a, side="other", business_name="股息红利税补缴", trade_time="0")
        e = dict(d, trade_time="23:59:59")
        assert _row_hash(d) == _row_hash(e)


class _NoCloseDB:
    def execute(self, *_a, **_k):
        class R:
            def fetchall(self):
                return []
        return R()


class TestPositionReplay:
    def test_buy_sell_moving_average(self):
        st = {}
        _apply_row(st, dict(stock_code="600000", side="buy", quantity=100, price=10,
                            amount=1000, commission=5, stamp_tax=0, transfer_fee=0,
                            extra_fee=0, subtype="", balance_qty=100))
        assert st["600000"]["qty"] == 100
        assert abs(st["600000"]["avg"] - 10.05) < 1e-9
        _apply_row(st, dict(stock_code="600000", side="sell", quantity=100, price=11,
                            amount=1100, commission=5, stamp_tax=1, transfer_fee=0,
                            extra_fee=0, subtype="", balance_qty=0))
        assert st["600000"]["qty"] == 0
        assert abs(st["600000"]["realized"] - ((11 - 10.05) * 100 - 6)) < 1e-9

    def test_dividend_tax_balance_must_not_zero_position(self):
        # 券商的股息税行：成交数量=计税股数、股份余额=0，绝不能当成持仓
        st = {"600000": {"qty": 100, "avg": 10, "realized": 0, "estimated": False}}
        _apply_row(st, dict(stock_code="600000", side="other", quantity=100, price=10,
                            amount=1, commission=0, stamp_tax=0, transfer_fee=0,
                            extra_fee=0, subtype="tax", balance_qty=0))
        assert st["600000"]["qty"] == 100

    def test_bonus_shares_dilute_cost(self):
        st = {"600000": {"qty": 100, "avg": 10, "realized": 0, "estimated": False}}
        _apply_row(st, dict(stock_code="600000", side="other", quantity=40, price=0,
                            amount=0, commission=0, stamp_tax=0, transfer_fee=0,
                            extra_fee=0, subtype="bonus", balance_qty=140))
        assert st["600000"]["qty"] == 140
        assert abs(st["600000"]["avg"] - 10 * 100 / 140) < 1e-9

    def test_pre_balance(self):
        assert _pre_balance(dict(side="buy", quantity=100, balance_qty=300)) == 200
        assert _pre_balance(dict(side="sell", quantity=100, balance_qty=200)) == 300

    def test_multi_account_replays_separately_then_merges(self):
        d1, d2 = datetime.date(2026, 1, 5), datetime.date(2026, 1, 6)
        rows = [
            dict(account_no="A", trade_date=d1, stock_code="600000", side="buy",
                 quantity=100, price=10, amount=1000, commission=0, stamp_tax=0,
                 transfer_fee=0, extra_fee=0, balance_qty=100, cash_balance=9000),
            dict(account_no="B", trade_date=d1, stock_code="600000", side="buy",
                 quantity=200, price=20, amount=4000, commission=0, stamp_tax=0,
                 transfer_fee=0, extra_fee=0, balance_qty=200, cash_balance=16000),
            dict(account_no="A", trade_date=d2, stock_code="600000", side="sell",
                 quantity=50, price=12, amount=600, commission=0, stamp_tax=0,
                 transfer_fee=0, extra_fee=0, balance_qty=50, cash_balance=9600),
        ]
        state, cash = replay_positions(_NoCloseDB(), rows)
        st = state["600000"]
        # A 账户 sell 后的余额 50 不能覆盖 B 账户的 200 股
        assert st["qty"] == 250
        assert abs(st["avg"] - 18.0) < 1e-9
        assert abs(st["realized"] - 100.0) < 1e-9
        assert cash == 25600

    def test_until_keeps_seeded_initial_positions(self):
        """区间起点前的持仓必须保留：until 过滤不能让期初持仓整体消失。

        回归：复盘用 replay_positions(until=区间首日) 取期初状态时，先按日期
        过滤再重放会丢掉「首行股份余额反推出来的期初持仓」，导致期初权益
        少一大截、卖出变成无成本利润。
        """
        d1, d2 = datetime.date(2026, 1, 5), datetime.date(2026, 1, 6)
        rows = [
            dict(account_no="A", trade_date=d1, stock_code="600000", side="sell",
                 quantity=100, price=12, amount=1200, clean_amount=1200,
                 commission=0, stamp_tax=0, transfer_fee=0, extra_fee=0,
                 balance_qty=200, cash_balance=10000),
            dict(account_no="A", trade_date=d2, stock_code="600000", side="sell",
                 quantity=100, price=11, amount=1100, clean_amount=1100,
                 commission=0, stamp_tax=0, transfer_fee=0, extra_fee=0,
                 balance_qty=100, cash_balance=11100),
        ]
        # until=首日：应用 0 行，但期初 300 股（200+100）和期初现金必须保留
        state, cash = replay_positions(_NoCloseDB(), rows, until=d1)
        assert state["600000"]["qty"] == 300
        assert state["600000"]["estimated"] is True
        assert cash == 8800  # 首行前现金 = 10000 - 1200

        # until=次日：应用首日卖出，剩 200 股，现金变为首日后的 10000
        state, cash = replay_positions(_NoCloseDB(), rows, until=d2)
        assert state["600000"]["qty"] == 200
        assert cash == 10000

    def test_estimated_flag_clears_when_seed_shares_exhausted(self):
        st = {"600000": {"qty": 100, "avg": 10, "realized": 0,
                         "estimated": True, "seed_qty": 100}}
        # 部分卖出：还剩估算股 → 仍然是估算
        _apply_row(st, dict(stock_code="600000", side="sell", quantity=40, price=12,
                            amount=480, commission=5, stamp_tax=0, transfer_fee=0,
                            extra_fee=0, subtype="", balance_qty=60))
        assert st["600000"]["seed_qty"] == 60
        assert st["600000"]["estimated"] is True
        # 清掉剩余估算股 → 复位；之后重新买入的成本是精确的
        _apply_row(st, dict(stock_code="600000", side="sell", quantity=60, price=8,
                            amount=480, commission=5, stamp_tax=0, transfer_fee=0,
                            extra_fee=0, subtype="", balance_qty=0))
        assert st["600000"]["seed_qty"] == 0
        assert st["600000"]["estimated"] is False
        _apply_row(st, dict(stock_code="600000", side="buy", quantity=100, price=20,
                            amount=2000, commission=5, stamp_tax=0, transfer_fee=0,
                            extra_fee=0, subtype="", balance_qty=100))
        assert st["600000"]["estimated"] is False
        assert abs(st["600000"]["avg"] - 20.05) < 1e-9
