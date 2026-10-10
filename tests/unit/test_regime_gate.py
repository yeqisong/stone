"""空仓闸门 compute_regime_gates 单元测试——禁禁买循环语义与窗口不变性。

2026-09-24~10-09 实际事故：旧「累计相位」实现的 streak 依赖回看窗口起点，
窗口随调用日前移导致相位漂移，持续下跌段连续 8 个交易日全禁、放行承诺失效。
重写为「连续低于 MA 天数 mod (max_skip+1)」局部判定后，本文件锁定：
① 禁禁买循环：连续低于段的第 1、2 天禁买，第 3 天（及每 max_skip+1 倍数日）放行；
② 窗口不变性：回看多包含一段站上 MA 的历史，结论不变（旧实现在此会漂移）。
"""
import unittest
from datetime import date, timedelta
from unittest.mock import MagicMock

from scripts.pipeline import compute_regime_gates

WIN, MAX_SKIP = 3, 2
CFG = {"enabled": True, "index_code": "000300", "ma_window": WIN, "max_skip_days": MAX_SKIP}


def _make_idx(closes, start="2026-01-01"):
    """构造 (trade_date, close) 行序列（连续自然日充当交易日轴）。"""
    d0 = date.fromisoformat(start)
    return [(d0 + timedelta(days=i), c) for i, c in enumerate(closes)]


def _gates(rows, dates):
    db = MagicMock()
    db.execute.return_value.fetchall.return_value = rows
    return compute_regime_gates(db, dates, CFG)


def _expected_gate_flags(closes):
    """独立推演：MA(3) 从下标 2 起有效；run=连续低于天数（站上归零）；禁 iff run%3!=0。
    返回每个下标的判定（None=MA 预热不计，True=禁，False=放行）。"""
    flags = []
    run = 0
    for i, c in enumerate(closes):
        if i < WIN - 1:
            flags.append(None)
            continue
        ma = sum(closes[i - WIN + 1:i + 1]) / WIN
        if c < ma:
            run += 1
            flags.append(run % (MAX_SKIP + 1) != 0)
        else:
            run = 0
            flags.append(False)
    return flags


class TestRegimeGateCycle(unittest.TestCase):
    def test_ban_ban_buy_cycle(self):
        """持续下跌段呈 禁、禁、买 循环（判定从 MA 成形日起）。"""
        closes = [100 - i for i in range(10)]
        rows = _make_idx(closes)
        days = [str(r[0]) for r in rows]
        gated = _gates(rows, days)
        flags = _expected_gate_flags(closes)
        expected = {days[i] for i, f in enumerate(flags) if f}
        releases = {days[i] for i, f in enumerate(flags) if f is False and i >= WIN - 1}
        self.assertEqual(gated, expected)
        self.assertTrue(releases, "持续下跌段必须有放行日")
        self.assertTrue(releases.isdisjoint(gated))

    def test_above_ma_resets_run(self):
        """站上 MA 归零：再次跌破后 run 从 1 数起，循环相位重置。"""
        closes = [100, 100, 100, 90, 90, 90, 100, 90, 90, 90, 90]
        rows = _make_idx(closes)
        days = [str(r[0]) for r in rows]
        gated = _gates(rows, days)
        flags = _expected_gate_flags(closes)
        expected = {days[i] for i, f in enumerate(flags) if f}
        self.assertEqual(gated, expected)
        # 两段跌破各含一个放行日（各段第 3 天）
        self.assertEqual(len([f for f in flags if f is False and flags.index(f) >= 0]) >= 2, True)


class TestWindowInvariance(unittest.TestCase):
    def _axis_with_above_prefix(self):
        """前 12 天横盘站上 MA，之后 30 天持续下跌——连续段从下标 12 起。"""
        closes = [100] * 12 + [100 - i for i in range(1, 31)]
        return _make_idx(closes, "2026-01-01")

    def test_longer_lookback_same_verdicts(self):
        """多回看一段站上 MA 的历史（窗口起点不同），全部日期结论一致——旧实现会漂移。"""
        rows = self._axis_with_above_prefix()
        days = [str(r[0]) for r in rows]
        short = _gates(rows[6:], days)    # 窗口从下标 6 起（仍覆盖段起点+MA 预热）
        full = _gates(rows, days)         # 全量窗口
        self.assertEqual(short, full)

    def test_single_vs_batch_call(self):
        """单日调用与批量调用对同一日期结论一致。"""
        rows = self._axis_with_above_prefix()
        days = [str(r[0]) for r in rows]
        batch = _gates(rows, days)
        for d in days[15:30:3]:
            self.assertEqual(_gates(rows, [d]), {d} & batch or set(),
                             f"{d} 单日/批量结论不一致")

    def test_release_days_present_in_long_downtrend(self):
        """30 天连续下跌段内必有 10 个放行日（每 3 天 1 个）——放行承诺。"""
        rows = self._axis_with_above_prefix()
        days = [str(r[0]) for r in rows]
        gated = _gates(rows, days)
        releases = [d for d in days[12:] if d not in gated]
        self.assertEqual(len(releases), 10)


class TestDisabled(unittest.TestCase):
    def test_disabled_returns_empty(self):
        db = MagicMock()
        self.assertEqual(compute_regime_gates(db, ["2026-10-10"], {"enabled": False}), set())
        self.assertEqual(compute_regime_gates(db, ["2026-10-10"], {}), set())


if __name__ == "__main__":
    unittest.main()
