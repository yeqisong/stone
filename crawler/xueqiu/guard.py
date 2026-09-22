"""雪球行为评分预算管理（XueqiuGuard，design/06 §4.4 / §9.3 P0 修正版）。

P0 实测：WAF 频控是行为评分制——60s 时间窗内 30~60 个连续请求即触发（HTTP 200 + 挑战
HTML）；触发后 10 分钟零自愈，但重过首页挑战即时洗白（评分绑 WAF 会话 cookie）。

策略（形态 A：脉冲+洗白）：
- 主动预算：60s 滑窗内请求数 < BURST_LIMIT（默认 20，保守于实测触发点 28）；
- 窗满即主动洗白（不等被动触发，省 10 分钟死等）；
- 被动触发（响应为挑战 HTML）→ 立即洗白并计入触发计数（健康观察指标）；
- 登录 token 模式（形态 C）下只统计不洗白（配额由账号承担，上限待实测后定）。
"""
import time
from collections import deque
from dataclasses import dataclass, field

from loguru import logger

from . import washer
from .adapter import XueqiuAdapter, load_login_cookies

BURST_LIMIT = 20          # 60s 窗口主动预算（实测触发点 28，留余量）
WINDOW_S = 60.0
MAX_WASH_PER_HOUR = 90    # 洗白熔断：P2 全市场预算 ~0.45 req/s 需 ~50-60 次/h，90 留余量
                           # （超线 = 被针对性风控，停机人工介入）
WASH_MIN_INTERVAL_S = 60  # 两次洗白最小间隔——"洗白-打满-再洗白"高频循环本身是异常行为
                           # （2026-09-21 实测教训：无间隔时均摊 3-4 req/s 持续流触发 IP 级 405 软封禁）
RISK_FAIL_STREAK = 3      # 连续非挑战失败（405 等）→ 熔断停机，绝不重试风暴


@dataclass
class GuardStats:
    requests: int = 0
    ok: int = 0
    challenged: int = 0   # 被动触发次数（观察指标：稳态应接近 0）
    washes: int = 0       # 洗白次数（主动 + 被动）


class RatingGuard:
    """评分预算 + 洗白调度，串行调用（P1 单线程）。"""

    def __init__(self, adapter: XueqiuAdapter):
        self.adapter = adapter
        self._window: deque[float] = deque()
        self._wash_times: deque[float] = deque()
        self._last_wash = 0.0
        self._risk_streak = 0
        self.stats = GuardStats()

    # ── 预算 ──

    def _prune(self):
        now = time.time()
        while self._window and now - self._window[0] > WINDOW_S:
            self._window.popleft()
        while self._wash_times and now - self._wash_times[0] > 3600:
            self._wash_times.popleft()

    def _budget_available(self) -> bool:
        self._prune()
        return len(self._window) < BURST_LIMIT

    # ── 洗白 ──

    def _wash(self, reason: str):
        if len(self._wash_times) >= MAX_WASH_PER_HOUR:
            raise RuntimeError(
                f'雪球洗白熔断：近 1h 已 {len(self._wash_times)} 次（{reason}），疑似针对性风控，暂停采集')
        # 洗白间隔下限：高频洗白是异常行为画像，必须节流
        wait = self._last_wash + WASH_MIN_INTERVAL_S - time.time()
        if wait > 0:
            logger.info(f'雪球洗白节流等待 {wait:.0f}s（{reason}）')
            time.sleep(wait)
        cookies = washer.wash()
        # 登录模式下洗白仅刷新 WAF cookie，登录 token 从交接源（.env）回填，不依赖运行态 cookie
        if self.adapter.login_mode:
            cookies = {**cookies, **(load_login_cookies() or {})}
        self.adapter.set_cookies(cookies)
        self._wash_times.append(time.time())
        self._last_wash = time.time()
        self.stats.washes += 1
        self._window.clear()  # 评分清零，窗口重置
        self._risk_streak = 0
        logger.info(f'雪球会话洗白完成（{reason}），累计 {self.stats.washes} 次')

    # ── 对外主入口 ──

    def ensure_ready(self):
        """启动/会话失效时获取 cookie。"""
        if not self.adapter.ready():
            self._wash('initial')

    def fetch(self, code: str, page: int = 1):
        """带预算管理的单次拉取：预算满 → 主动洗白；被动挑战 → 洗白后重试一次；
        连续非挑战失败（405 软封禁等）→ 熔断停机（绝不重试风暴）。"""
        if not self.adapter.ready():
            self.ensure_ready()
        if not self._budget_available():
            self._wash('budget exhausted')

        self._window.append(time.time())
        self.stats.requests += 1
        res = self.adapter.fetch_page(code, page)
        if res.ok:
            self.stats.ok += 1
            self._risk_streak = 0
            return res

        if res.challenged:
            self.stats.challenged += 1
            logger.warning(f'雪球评分触发（被动）: {code} p{page}，第 {self.stats.challenged} 次')
            self._wash('challenged')
            self._window.append(time.time())
            self.stats.requests += 1
            res = self.adapter.fetch_page(code, page)
            if res.ok:
                self.stats.ok += 1
            return res

        # 非挑战失败：405 等风控信号，连续出现即熔断（2026-09-21 教训：
        # 短退避重试风暴 60s 打 ~460 请求，直接把 IP 打成 405 软封禁）
        self._risk_streak += 1
        logger.warning(f'雪球非挑战失败连续 {self._risk_streak} 次: {res.error[:60]}')
        if self._risk_streak >= RISK_FAIL_STREAK:
            raise RuntimeError(
                f'雪球连续非挑战失败 {self._risk_streak} 次（疑似 IP 软封禁），熔断停机等冷却')
        return res
