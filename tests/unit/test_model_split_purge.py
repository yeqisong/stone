"""三重时间切分 purge：train/val 尾部标签不得伸进下一段。"""
import unittest
from datetime import date, timedelta

import pandas as pd

from scripts.pipeline import time_split_purged


class TestTimeSplitPurged(unittest.TestCase):
    def _df(self, n=200):
        start = date(2024, 1, 1)
        dates = [start + timedelta(days=i) for i in range(n)]
        return pd.DataFrame({"trade_date": [str(d) for d in dates]}), dates

    def test_gaps_and_no_overlap(self):
        df, dates = self._df()
        sp = time_split_purged(df, label_horizon=20)
        train_d = set(df[sp["train_mask"]]["trade_date"])
        val_d = set(df[sp["val_mask"]]["trade_date"])
        test_d = set(df[sp["test_mask"]]["trade_date"])
        self.assertFalse(train_d & val_d)
        self.assertFalse(val_d & test_d)
        self.assertFalse(train_d & test_d)
        self.assertGreaterEqual(min(val_d), str(dates[120]))
        self.assertGreaterEqual(min(test_d), str(dates[160]))
        # train 最后一天 +20 个交易日仍早于 val 起点；val 同理早于 test
        train_last = max(train_d)
        val_first = min(val_d)
        val_last = max(val_d)
        test_first = min(test_d)
        self.assertLess(dates.index(date.fromisoformat(train_last)) + 20,
                        dates.index(date.fromisoformat(val_first)))
        self.assertLess(dates.index(date.fromisoformat(val_last)) + 20,
                        dates.index(date.fromisoformat(test_first)))

    def test_short_frame_returns_empty_masks(self):
        df = pd.DataFrame({"trade_date": ["2026-01-05", "2026-01-06"]})
        sp = time_split_purged(df)
        self.assertFalse(sp["train_mask"].any())
        self.assertFalse(sp["val_mask"].any())
        self.assertFalse(sp["test_mask"].any())


if __name__ == "__main__":
    unittest.main()
