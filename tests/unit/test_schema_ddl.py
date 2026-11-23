"""DDL 分段执行：带前置注释的语句不能被跳过（fresh 库 feature_values 分区）。"""
import unittest

from app.db.schema import (ALL_TABLES, CREATE_FEATURE_VALUES,
                           _split_statements, _strip_leading_comments)


class TestSchemaDdl(unittest.TestCase):
    def test_leading_comment_lines_removed(self):
        raw = "\n-- 注释一\n-- 注释二\nCREATE TABLE x (id INT)\n"
        self.assertEqual(_strip_leading_comments(raw), "CREATE TABLE x (id INT)")

    def test_comment_only_segment_becomes_empty(self):
        self.assertEqual(_strip_leading_comments("\n-- 只有注释\n"), "")

    def test_feature_partition_do_block_is_executable(self):
        stmts = [_strip_leading_comments(s)
                 for s in _split_statements(CREATE_FEATURE_VALUES.strip())]
        self.assertTrue(any(s.startswith("DO $$") for s in stmts),
                        "feature_values 分区 DO 块被注释前缀吞掉")
        self.assertTrue(any("feature_values_def" in s for s in stmts))

    def test_no_executable_statement_starts_with_comment(self):
        for name, ddl in ALL_TABLES:
            for stmt in _split_statements(ddl.strip()):
                s = _strip_leading_comments(stmt)
                if s:
                    self.assertFalse(s.startswith("--"), name)
