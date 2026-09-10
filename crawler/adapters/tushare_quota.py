"""tushare 配额计数器（内存单例 + tushare_quota 表落库）。

tushare 免费额度：50 次/分钟，8000 次/天（北京时间自然日）。
- consume(): 每次 API 调用前调用（限流 + 计数 + 阈值检查）
- 日配额 ≥90% 标记预警；达到上限抛 QuotaExhausted（上层任务失败并提示）
- snapshot(): 供 API/前端展示剩余配额
- persist()/load(): 与 tushare_quota 表同步（进程重启不丢当日计数）
"""
import threading
import time
from datetime import datetime, date, timedelta

CALLS_LIMIT_DAY = 8000
CALLS_LIMIT_MINUTE = 50
WARN_PCT = 0.90          # 90% 预警
HARD_STOP_RESERVE = 20   # 剩余 < 20 次时停止新任务（防止任务中途耗尽）

_TZ = timedelta(hours=8)  # Asia/Shanghai


def _bj_now() -> datetime:
    """北京时间（无 zoneinfo 依赖的简单实现）。"""
    return datetime.utcnow() + _TZ


def _bj_date() -> date:
    return _bj_now().date()


class QuotaExhausted(Exception):
    """tushare 当日配额已用尽。"""


class TushareQuota:
    _inst: "TushareQuota" = None
    _lock = threading.Lock()

    def __init__(self):
        self.calls_today = 0
        self.calls_limit = CALLS_LIMIT_DAY
        self.minute_calls = 0
        self.minute_limit = CALLS_LIMIT_MINUTE
        self.minute_started = _bj_now()
        self.exhausted = False
        self.last_call_at = None
        self._day = _bj_date()
        self._last_call_ts = 0.0
        self._rate_limit = 1.3  # 秒（50/min = 1.2s，留余量）

    @classmethod
    def get(cls) -> "TushareQuota":
        if cls._inst is None:
            with cls._lock:
                if cls._inst is None:
                    cls._inst = cls()
        return cls._inst

    def _rollover_if_needed(self):
        """跨自然日（北京时间）重置当日计数。"""
        today = _bj_date()
        if today != self._day:
            self._day = today
            self.calls_today = 0
            self.exhausted = False

    def _rollover_minute(self):
        now = _bj_now()
        if (now - self.minute_started).total_seconds() >= 60:
            self.minute_calls = 0
            self.minute_started = now

    def consume(self) -> None:
        """限流 + 计数。配额耗尽抛 QuotaExhausted。"""
        with self._lock:
            self._rollover_if_needed()
            self._rollover_minute()
            if self.exhausted or self.calls_today >= self.calls_limit:
                raise QuotaExhausted(
                    f"tushare 当日配额已用尽（{self.calls_today}/{self.calls_limit}），请明日续跑")
            # 限流（1.3s 间隔）
            elapsed = time.time() - self._last_call_ts
            if elapsed < self._rate_limit:
                time.sleep(self._rate_limit - elapsed)
            self._last_call_ts = time.time()
            self.calls_today += 1
            self.minute_calls += 1
            self.last_call_at = _bj_now()
            if self.calls_today >= self.calls_limit:
                self.exhausted = True

    def remaining(self) -> int:
        with self._lock:
            self._rollover_if_needed()
            return max(self.calls_limit - self.calls_today, 0)

    def can_start_task(self) -> bool:
        """新任务预算检查：剩余次数过少时不允许启动长任务。"""
        return self.remaining() >= HARD_STOP_RESERVE and not self.exhausted

    def snapshot(self) -> dict:
        with self._lock:
            self._rollover_if_needed()
            used_pct = round(self.calls_today / self.calls_limit * 100, 1) if self.calls_limit else 0
            if self.exhausted or used_pct >= 100:
                risk = "exhausted"
            elif used_pct >= WARN_PCT * 100:
                risk = "high"
            elif used_pct >= 60:
                risk = "medium"
            else:
                risk = "low"
            return {
                "calls_today": self.calls_today,
                "calls_limit": self.calls_limit,
                "remaining": max(self.calls_limit - self.calls_today, 0),
                "used_pct": used_pct,
                "minute_calls": self.minute_calls,
                "minute_limit": self.minute_limit,
                "risk_level": risk,
                "exhausted": self.exhausted,
                "last_call_at": self.last_call_at.strftime("%Y-%m-%d %H:%M:%S") if self.last_call_at else None,
                "date": str(self._day),
            }

    def persist(self, db) -> None:
        """写入 tushare_quota 表（供状态页跨进程查看）。"""
        try:
            from sqlalchemy import text
            s = self.snapshot()
            db.execute(text("""
                INSERT INTO tushare_quota (trade_date, calls_today, calls_limit, minute_calls,
                    minute_limit, minute_started, quota_exhausted, last_call_at, updated_at)
                VALUES (:d, :ct, :cl, :mc, :ml, :ms, :ex, :lc, CURRENT_TIMESTAMP)
                ON CONFLICT (trade_date) DO UPDATE SET
                    calls_today = EXCLUDED.calls_today,
                    minute_calls = EXCLUDED.minute_calls,
                    minute_started = EXCLUDED.minute_started,
                    quota_exhausted = EXCLUDED.quota_exhausted,
                    last_call_at = EXCLUDED.last_call_at,
                    updated_at = CURRENT_TIMESTAMP
            """), {
                "d": self._day, "ct": self.calls_today, "cl": self.calls_limit,
                "mc": self.minute_calls, "ml": self.minute_limit,
                "ms": self.minute_started, "ex": self.exhausted, "lc": self.last_call_at,
            })
            db.commit()
        except Exception:
            pass  # 落库失败不影响采集

    def load(self, db) -> None:
        """启动时从表恢复当日计数（进程重启不丢）。"""
        try:
            from sqlalchemy import text
            row = db.execute(text(
                "SELECT calls_today, calls_limit, quota_exhausted, last_call_at "
                "FROM tushare_quota WHERE trade_date = :d"
            ), {"d": _bj_date()}).fetchone()
            if row:
                self.calls_today = row[0] or 0
                self.calls_limit = row[1] or CALLS_LIMIT_DAY
                self.exhausted = bool(row[2])
                self.last_call_at = row[3]  # 状态页「最后调用」展示（重启后不显示为空）
        except Exception:
            pass
