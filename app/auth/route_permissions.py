"""路由 → 权限码 的统一注册表。

目标：不为 200+ 个端点逐个加装饰器；用集中规则覆盖所有现有路由，
启动时做覆盖自检，未知路由默认拒绝。
"""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class RouteRule:
    method: str          # GET/POST/... 或 *
    pattern: str         # 正则，^...$
    permission: str | None
    auth_only: bool = False
    public: bool = False

    def matches(self, method: str, path: str) -> bool:
        if self.method != "*" and self.method != method.upper():
            return False
        return re.fullmatch(self.pattern, path) is not None


def _r(method: str, pattern: str, permission: str | None, *, auth_only: bool = False,
       public: bool = False) -> RouteRule:
    return RouteRule(method, pattern, permission, auth_only=auth_only, public=public)


# 顺序：public → auth-only → 具体路径 → 前缀兜底
ROUTE_RULES: list[RouteRule] = [
    # ── public ──
    _r("POST", r"/api/login", None, public=True),
    _r("GET", r"/health", None, public=True),
    _r("GET", r"/api/version", None, public=True),
    _r("POST", r"/webhook/feishu", None, public=True),

    # ── 当前用户（只要求登录）──
    _r("GET", r"/api/me", None, auth_only=True),
    _r("POST", r"/api/me/password", None, auth_only=True),
    _r("POST", r"/api/me/reauth", None, auth_only=True),
    _r("GET", r"/api/me/settings", None, auth_only=True),
    _r("PUT", r"/api/me/settings", None, auth_only=True),
    _r("GET", r"/api/me/support-access", None, auth_only=True),
    _r("POST", r"/api/me/feishu/bind-code", None, auth_only=True),
    _r("DELETE", r"/api/me/feishu/bind", None, auth_only=True),

    # ── 后台管理 ──
    _r("*", r"/api/admin/users(?:/.*)?", "system.user.manage"),
    _r("*", r"/api/admin/roles(?:/.*)?", "system.role.manage"),
    _r("GET", r"/api/admin/permissions", "system.role.manage"),
    _r("GET", r"/api/admin/audit", "system.audit.view"),
    _r("*", r"/api/admin/support/sessions(?:/.*)?", "admin.support.view"),

    # ── 持仓 / 交易 ──
    _r("GET", r"/api/portfolio/watch-window", "portfolio.position.view"),
    _r("POST", r"/api/portfolio/watch/prepare", "portfolio.watch_window.launch"),
    _r("POST", r"/api/portfolio/watch/launch", "portfolio.watch_window.launch"),
    _r("GET", r"/api/portfolio/risk-heat", "portfolio.position.view"),
    _r("GET", r"/api/portfolio", "portfolio.position.view"),
    _r("POST", r"/api/portfolio", "portfolio.position.edit"),
    _r("PUT", r"/api/portfolio/[^/]+", "portfolio.position.edit"),
    _r("DELETE", r"/api/portfolio/[^/]+", "portfolio.position.edit"),
    _r("GET", r"/api/portfolio/[^/]+/history", "portfolio.history.view"),
    _r("POST", r"/api/portfolio/trades/import", "portfolio.trades.import"),
    _r("POST", r"/api/portfolio/trades/apply-positions", "portfolio.trades.import"),
    _r("DELETE", r"/api/portfolio/trades/batches/[^/]+", "portfolio.trades.import"),
    _r("POST", r"/api/portfolio/trades/intraday/check", "portfolio.trades.intraday"),
    _r("POST", r"/api/portfolio/trades/intraday/record", "portfolio.trades.intraday"),
    _r("PUT", r"/api/portfolio/trades/intraday/cash", "portfolio.trades.intraday"),
    _r("DELETE", r"/api/portfolio/trades/intraday/[^/]+", "portfolio.trades.intraday"),
    _r("PUT", r"/api/portfolio/trades/journal", "portfolio.trades.journal.edit"),
    _r("GET", r"/api/portfolio/trades/discipline/rules", "portfolio.discipline.view"),
    _r("PUT", r"/api/portfolio/trades/discipline/rules", "portfolio.discipline.edit"),
    _r("GET", r"/api/portfolio/trades/discipline/today", "portfolio.discipline.view"),
    _r("GET", r"/api/portfolio/trades(?:/.*)?", "portfolio.trades.view"),

    # ── 自选 ──
    _r("GET", r"/api/watch/.*", "watch.group.view"),
    _r("POST", r"/api/watch/groups", "watch.group.edit"),
    _r("DELETE", r"/api/watch/groups/[^/]+", "watch.group.edit"),
    _r("PUT", r"/api/watch/stock-groups", "watch.group.edit"),

    # ── 风控 ──
    _r("GET", r"/api/risk/rules", "risk.rules.view"),
    _r("POST", r"/api/risk/rules", "risk.rules.edit"),
    _r("GET", r"/api/risk/intraday-rules", "risk.rules.view"),
    _r("POST", r"/api/risk/intraday-rules", "risk.rules.edit"),
    _r("POST", r"/api/risk/evaluate", "model.risk.evaluate"),
    _r("GET", r"/api/risk/signal-stats", "research.signal.view"),
    _r("GET", r"/api/risk/signal-outcomes", "research.signal.view"),
    _r("GET", r"/api/risk/alerts", "risk.alerts.view"),
    _r("POST", r"/api/risk/alerts/ack", "risk.alerts.ack"),

    # ── 共享研究 ──
    _r("GET", r"/api/treemap_data", "research.treemap.view"),
    _r("GET", r"/api/treemap_intraday", "research.treemap.view"),
    _r("GET", r"/api/treemap_latest_date", "research.treemap.view"),
    _r("GET", r"/api/market_sentiment", "research.treemap.view"),
    _r("GET", r"/api/sector_flow_history", "research.treemap.view"),
    _r("GET", r"/api/market/.*", "research.market.view"),
    _r("GET", r"/api/stocks(?:/.*)?", "research.stock.view"),
    _r("GET", r"/api/stock/radar", "research.radar.view"),
    _r("GET", r"/api/buy/check", "research.radar.view"),
    _r("GET", r"/api/buy/candidates", "research.radar.view"),
    _r("GET", r"/api/stock/[^/]+/convertible-bonds", "research.cb.view"),
    _r("GET", r"/api/stock/[^/]+/cb-linkage", "research.cb.view"),
    _r("GET", r"/api/stock/[^/]+/news", "research.news.view"),
    _r("GET", r"/api/stock/.*", "research.stock.view"),
    _r("GET", r"/api/buy_signals", "research.signal.view"),
    _r("GET", r"/api/signal/stats", "research.signal.view"),
    _r("GET", r"/api/signals/personal", "signal.personal.view"),
    _r("GET", r"/api/cb/.*", "research.cb.view"),
    _r("GET", r"/api/news(?:/.*)?", "research.news.view"),
    _r("GET", r"/api/kepl/functions", "research.kepl.view"),
    _r("POST", r"/api/kepl/parse", "research.kepl.view"),
    _r("GET", r"/api/lineage(?:/.*)?", "research.lineage.view"),

    # ── 模型 ──
    _r("GET", r"/api/v1/models.*", "model.view"),
    _r("POST", r"/api/v1/models", "model.config.edit"),
    _r("PUT", r"/api/v1/models/[^/]+/config", "model.config.edit"),
    _r("DELETE", r"/api/v1/models/[^/]+", "model.archive"),
    _r("POST", r"/api/v1/models/[^/]+/train", "model.train"),
    _r("POST", r"/api/v1/models/[^/]+/retrain", "model.train"),
    _r("POST", r"/api/v1/models/[^/]+/stop", "model.train"),
    _r("POST", r"/api/v1/models/[^/]+/approve", "model.approve"),
    _r("POST", r"/api/v1/models/[^/]+/reject", "model.approve"),
    _r("POST", r"/api/v1/models/[^/]+/promote", "model.approve"),
    _r("POST", r"/api/v1/models/[^/]+/archive", "model.archive"),
    _r("POST", r"/api/v1/models/[^/]+/regenerate-day", "model.regenerate"),
    _r("POST", r"/api/v1/models/[^/]+/attribution", "model.evaluate"),
    _r("POST", r"/api/v1/models/[^/]+/permutation", "model.evaluate"),
    _r("POST", r"/api/v1/models/[^/]+/walk-forward", "model.evaluate"),
    _r("POST", r"/api/v1/models/[^/]+/strategy-scan", "model.evaluate"),
    _r("POST", r"/api/v1/models/[^/]+/strategy-scan/[^/]+/apply", "model.scan.apply"),

    # ── 特征 ──
    _r("GET", r"/api/features.*", "feature.view"),
    _r("POST", r"/api/features", "feature.edit"),
    _r("POST", r"/api/features/validate", "feature.compute"),
    _r("POST", r"/api/features/check-stats-integrity", "feature.compute"),
    _r("POST", r"/api/features/[^/]+/(?:stats|compute-range|recompute-stats|ic)", "feature.compute"),
    _r("PUT", r"/api/features/.*", "feature.edit"),
    _r("PATCH", r"/api/features/.*", "feature.edit"),
    _r("DELETE", r"/api/features/.*", "feature.edit"),

    # ── 函数 ──
    _r("GET", r"/api/functions.*", "function.view"),
    _r("POST", r"/api/functions/ai-chat", "function.ai_chat"),
    _r("POST", r"/api/functions/test-run-temp", "function.test"),
    _r("POST", r"/api/functions/[^/]+/test-run", "function.test"),
    _r("POST", r"/api/functions.*", "function.edit"),
    _r("PUT", r"/api/functions.*", "function.edit"),
    _r("DELETE", r"/api/functions.*", "function.edit"),

    # ── DAG ──
    _r("GET", r"/api/dag/flows.*", "dag.view"),
    _r("GET", r"/api/dag/logs", "dag.view"),
    _r("GET", r"/api/dag/node-types.*", "dag.view"),
    _r("GET", r"/api/dag_status", "dag.view"),
    _r("GET", r"/api/dag_logs", "dag.view"),
    _r("GET", r"/api/dag_config", "dag.view"),
    _r("POST", r"/api/dag/flows/[^/]+/publish", "dag.publish"),
    _r("POST", r"/api/dag/flows/[^/]+/unpublish", "dag.publish"),
    _r("POST", r"/api/dag/flows/[^/]+/execute", "dag.execute"),
    _r("POST", r"/api/dag_trigger", "dag.execute"),
    _r("POST", r"/api/dag_terminate", "dag.terminate"),
    _r("POST", r"/api/dag/flows/validate", "dag.edit"),
    _r("POST", r"/api/dag/flows", "dag.edit"),
    _r("PUT", r"/api/dag/flows/.*", "dag.edit"),
    _r("DELETE", r"/api/dag/flows/.*", "dag.edit"),

    # ── 系统 ──
    _r("POST", r"/api/data_status/backfill", "system.backfill.execute"),
    _r("POST", r"/api/data_status/backfill/[^/]+/cancel", "system.backfill.execute"),
    _r("GET", r"/api/data_status/backfill/.*", "system.backfill.view"),
    _r("POST", r"/api/data_status/sync_date", "system.sync_date"),
    _r("POST", r"/api/refresh_stats", "system.stats.refresh"),
    _r("GET", r"/api/data_status.*", "system.status.view"),
    _r("GET", r"/api/trade_calendar", "system.status.view"),
    _r("GET", r"/api/data-sources/health", "system.status.view"),
    _r("GET", r"/api/tushare_quota", "system.status.view"),
    _r("GET", r"/api/minute-data", "system.status.view"),
    _r("GET", r"/api/xueqiu", "system.status.view"),
    _r("GET", r"/api/system/metrics", "system.status.view"),
    _r("GET", r"/api/stock_fund_list", "system.status.view"),
    _r("GET", r"/api/settings.*", "system.settings.view"),
    _r("POST", r"/api/settings.*", "system.settings.edit"),
    _r("GET", r"/api/llm_config.*", "system.settings.view"),
    _r("POST", r"/api/llm_config.*", "system.settings.edit"),
    _r("GET", r"/api/dbex/.*", "system.dbexplorer.view"),

    # ── Agent ──
    _r("GET", r"/api/agent/feedback", "agent.feedback.view"),
    # 文件下载：有效 sig 由端点自行校验；无 sig 时端点要求 JWT
    _r("GET", r"/api/agent/sessions/[^/]+/files/.*", None, public=True),
    _r("POST", r"/api/agent/skills/import/.*", "agent.skill.import"),
    _r("POST", r"/api/agent/skills", "agent.skill.edit"),
    _r("DELETE", r"/api/agent/skills/.*", "agent.skill.edit"),
    _r("GET", r"/api/agent/.*", "agent.use"),
    _r("POST", r"/api/agent/.*", "agent.use"),
    _r("PATCH", r"/api/agent/.*", "agent.use"),
    _r("DELETE", r"/api/agent/.*", "agent.use"),

    # ── WebSocket ──
    _r("*", r"/api/ws/dag", None, auth_only=True),
]


def resolve_rule(method: str, path: str) -> RouteRule | None:
    for rule in ROUTE_RULES:
        if rule.matches(method, path):
            return rule
    return None
