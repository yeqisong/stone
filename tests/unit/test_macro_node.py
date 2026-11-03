"""宏观采集节点纯函数测试（numpy 标量清洗）。"""
import unittest

import numpy as np

from scripts.pipeline import _fv


class TestFv(unittest.TestCase):
    def test_native_float(self):
        self.assertEqual(_fv(1.25), 1.25)

    def test_numpy_scalar(self):
        self.assertEqual(_fv(np.float64(1.2891)), 1.2891)
        self.assertEqual(_fv(np.int64(5)), 5.0)

    def test_none_and_nan(self):
        self.assertIsNone(_fv(None))
        self.assertIsNone(_fv(np.nan))
        self.assertIsNone(_fv(float('nan')))

    def test_bad_value(self):
        self.assertIsNone(_fv('abc'))
        self.assertIsNone(_fv({}))


if __name__ == '__main__':
    unittest.main()
