"""共享表白名单：DBExplorer/Agent SQL 不得看到个人与敏感表。"""
import unittest

from app.data_scope import SHARED_TABLES, is_shared_table


class TestDataScope(unittest.TestCase):
    def test_personal_and_secret_tables_denied(self):
        denied = [
            "users", "roles", "permissions", "role_permissions", "user_roles",
            "user_permission_overrides", "support_sessions", "audit_log",
            "user_config", "user_signal_history", "portfolio", "portfolio_history",
            "watch_groups", "watch_group_items", "trade_slip", "trade_import_batch",
            "trade_intraday", "trade_plan", "trade_journal", "risk_alerts",
            "agent_sessions", "agent_messages", "agent_feedback", "strategy_config",
            "_bak_mu_portfolio",
        ]
        for t in denied:
            self.assertFalse(is_shared_table(t), t)
            self.assertNotIn(t, SHARED_TABLES, t)

    def test_core_shared_tables_allowed(self):
        for t in ("daily_quote", "stock_master", "news_item", "feature_values",
                  "model_versions", "dag_flows", "cb_daily", "market_sentiment_daily"):
            self.assertTrue(is_shared_table(t), t)

    def test_empty_or_unknown_denied(self):
        self.assertFalse(is_shared_table(""))
        self.assertFalse(is_shared_table("no_such_table"))


if __name__ == "__main__":
    unittest.main()
