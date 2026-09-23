"""雪球情绪轮询调度器（design/06 §4，P1 试点版）。

形态：单线程同步循环（P1 试点 100 只 × 15min ≈ 0.11 req/s，无并发需求）。
- 到期堆（heapq）+ 随机抖动：next_due_at = now + 档位周期 × U(0.8, 1.2)；
- 游标增量：第一页对比 last_status_id 算新增，整页全增才翻页（≤5 页保护）；
- 时段引擎：交易日 09:15–15:30 全速 / 15:30–23:00 半速 / 深夜停 / 非交易日 20%；
- 写缓冲：攒批 UPSERT（满 200 条或 30s）；
- 状态持久化：xueqiu_crawl_state（重启续跑）；P1 只记录 zero/full_streak，不自动调档。
"""
import datetime
import heapq
import random
import time
from collections import deque
from zoneinfo import ZoneInfo

from loguru import logger
from sqlalchemy import text

from .adapter import XueqiuAdapter, map_item, StatusRow
from .guard import RatingGuard

_TZ = ZoneInfo('Asia/Shanghai')

# P2 预算重估（design/06 §9.8）：干净会话天花板 ~0.5 req/s（洗白周期×会话预算），
# 全市场分档压在 ~0.45 req/s 均摊：T1 500×45min + T2 1500×3h + T3 3400×8h ≈ 3.8 万 req/天
TIER_PERIOD_S = {1: 45 * 60, 2: 3 * 3600, 3: 8 * 3600}
TIER1_TOP_N = 500      # 成交额 top N → T1
TIER2_TOP_N = 2000     # 成交额 top N 内非 T1 → T2；其余 T3
MAX_PAGES_PER_ROUND = 5          # 单轮翻页保护
FLUSH_ROWS = 200
FLUSH_INTERVAL_S = 30
STATE_PERSIST_S = 60             # crawl_state 批量回写间隔
HEARTBEAT_S = 30                 # 心跳最小间隔（休眠分支也跳，保证"活着就心跳"）


def speed_factor(now: datetime.datetime, is_trade_day: bool) -> float:
    """时段速率系数（0 = 停）。"""
    if not is_trade_day:
        return 0.2
    hm = now.hour * 60 + now.minute
    if 9 * 60 + 15 <= hm <= 15 * 60 + 30:
        return 1.0
    if 15 * 60 + 30 < hm < 23 * 60:
        return 0.5
    return 0.0   # 23:00–09:15 停（主调度休眠）


def _now() -> datetime.datetime:
    return datetime.datetime.now(_TZ)


class Poller:
    def __init__(self, db):
        self.db = db
        self.adapter = XueqiuAdapter()
        self.guard = RatingGuard(self.adapter)
        # 三档独立堆 + 加权轮询（2026-09-23 修复）：单一堆是纯 FIFO，吞吐瓶颈
        # （~0.34 req/s）下档位周期形同虚设——实际全市场 ~6h 一轮，热门股无优先权，
        # 积压队列导致活跃股隔日才被轮到（"今日 0 帖"根因）。
        self._heaps: dict[int, list] = {1: [], 2: [], 3: []}
        self._seq = 0
        self._rr = 0
        self._buffer: list[StatusRow] = []
        self._states: dict[str, dict] = {}              # code -> state dict（内存缓存）
        self._dirty: set[str] = set()
        self._last_flush = time.time()
        self._last_persist = time.time()
        self._new_today = 0
        self._last_post_at = None
        self._started_at = None
        self._last_heartbeat = 0.0
        self._last_report = time.time()

    # ── 启动 ──

    def load_states(self):
        rows = self.db.execute(text(
            'SELECT code, tier, last_status_id, zero_streak, full_streak, fail_streak '
            'FROM xueqiu_crawl_state')).fetchall()
        now = time.time()
        for r in rows:
            self._states[r[0]] = {
                'tier': r[1], 'last_status_id': r[2] or 0,
                'zero_streak': r[3] or 0, 'full_streak': r[4] or 0, 'fail_streak': r[5] or 0,
            }
            # 冷启动：已有游标直接续跑；无游标先建立基线（首刷只记游标不落历史）
            due = now if not (r[2] or 0) else now + random.uniform(0, TIER_PERIOD_S.get(r[1], 3600))
            self._push(r[0], due)
        logger.info(f'雪球轮询器装载 {len(self._states)} 只股票状态'
                    f'（各档 { {t: len(h) for t, h in self._heaps.items()} }）')

    def _push(self, code: str, due_ts: float):
        self._seq += 1
        heapq.heappush(self._heaps[self._states[code]['tier']], (due_ts, self._seq, code))

    # ── 主循环 ──

    def run(self):
        from crawler.trade_calendar import is_trade_day
        self.guard.ensure_ready()
        self._started_at = _now()
        self._last_heartbeat = 0
        self._heartbeat()
        logger.info(f'雪球轮询器启动（login_mode={self.adapter.login_mode}），'
                    f'股票 {len(self._states)} 只')
        rr_order = [1, 1, 1, 1, 1, 2, 2, 2, 3, 3]   # 加权轮询序列：预算 5:3:2 分配
        while True:
            now_dt = _now()
            factor = speed_factor(now_dt, is_trade_day(now_dt.date()))
            if factor == 0.0:
                # 深夜休眠：每 5 分钟醒一次看时段（心跳照跳——"活着就心跳"）
                self._flush(force=True)
                self._persist()
                self._heartbeat()
                time.sleep(300)
                continue

            # 加权轮询选档：序列位置起找第一个"有到期任务"的档；全未到期则睡到最近到期
            now_ts = time.time()
            picked_tier = None
            for k in range(len(rr_order)):
                tier = rr_order[(self._rr + k) % len(rr_order)]
                h = self._heaps[tier]
                if h and h[0][0] <= now_ts:
                    picked_tier = tier
                    self._rr = (self._rr + k + 1) % len(rr_order)
                    break
            if picked_tier is None:
                nexts = [h[0][0] for h in self._heaps.values() if h]
                self._flush()
                self._persist()
                self._heartbeat()
                time.sleep(min(max(min(nexts) - time.time(), 0.05), 1.0))
                continue

            due_ts, _, code = heapq.heappop(self._heaps[picked_tier])
            self._process_one(code, factor)
            self._flush()
            self._persist()
            self._heartbeat()
            self._report()

    # ── 单股一轮 ──

    def _process_one(self, code: str, factor: float):
        st = self._states[code]
        tier = st['tier']
        period = TIER_PERIOD_S.get(tier, 3600) / factor   # 低速时段等效拉长周期
        try:
            res = self.guard.fetch(code, page=1)
        except RuntimeError as e:
            # 风控熔断（405 软封禁/洗白熔断）：冷却恢复而非退出——
            # 2026-09-22 教训：SystemExit 后无人重启，停摆 14h 才被指示灯发现
            self._cooldown_recover(e)
            self._push(code, time.time() + 60)
            return

        if not res.ok:
            st['fail_streak'] = st.get('fail_streak', 0) + 1
            st['today_status'] = 'degraded'
            self._dirty.add(code)
            # 长退避（300s 起翻倍）——短退避风暴曾 60s 打 460 请求触发 IP 软封禁（2026-09-21）
            self._push(code, time.time() + 300 * min(2 ** (st['fail_streak'] - 1), 8))
            return

        st['fail_streak'] = 0
        st['last_ok_at'] = _now()
        cursor = st['last_status_id']
        items = res.items

        # 冷启动（D6）：只建游标基线，不落历史
        if cursor == 0:
            if items:
                st['last_status_id'] = max(int(it['id']) for it in items)
            self._dirty.add(code)
            self._push(code, time.time() + period * random.uniform(0.8, 1.2))
            return

        # 游标增量：倒序列表中 id > cursor 的才是新增
        new_items = [it for it in items if int(it['id']) > cursor]
        full_page = len(items) >= 20 and len(new_items) == len(items)

        # 整页全增 → 翻页回补直至覆盖游标或达保护上限
        page = 1
        while full_page and page < MAX_PAGES_PER_ROUND:
            page += 1
            res2 = self.guard.fetch(code, page=page)
            if not res2.ok:
                break
            more_new = [it for it in res2.items if int(it['id']) > cursor]
            new_items.extend(more_new)
            full_page = len(res2.items) >= 20 and len(more_new) == len(res2.items)
            if not more_new:
                break

        # 批内按 id 去重：活跃股翻页时列表窗口滑动，跨页可能出现同帖
        uniq: dict[int, dict] = {}
        for it in new_items:
            uniq[int(it['id'])] = it
        new_items = list(uniq.values())

        if new_items:
            rows = [map_item(it, code) for it in new_items]
            self._buffer.extend(rows)
            self._new_today += len(rows)
            latest = max(r.created_at for r in rows)
            if self._last_post_at is None or latest > self._last_post_at:
                self._last_post_at = latest
            st['last_status_id'] = max(int(it['id']) for it in new_items)
            st['zero_streak'] = 0
            st['full_streak'] = st.get('full_streak', 0) + 1 if full_page else 0
            st['today_status'] = 'ok'
        else:
            st['zero_streak'] = st.get('zero_streak', 0) + 1
            st['full_streak'] = 0
            st['today_status'] = 'ok'

        self._dirty.add(code)
        jitter = random.uniform(0.8, 1.2)
        self._push(code, time.time() + period * jitter)

    # ── 冷却恢复（风控熔断后自动探测，不退出进程）──

    def _cooldown_recover(self, reason: Exception):
        """405 实测 ~76min 自愈：每 10min 探测一次（wash+单发），4h 仍封才退出交人工。

        退出前心跳已停 >4h，指示灯必红——人工介入有充分信号。"""
        logger.error(f'雪球进入冷却恢复: {reason}')
        for i in range(1, 25):
            time.sleep(600)
            try:
                self.guard.ensure_ready()          # 重新 wash（重置风控计数）
                res = self.guard.fetch('600519')   # 探测
                if res.ok:
                    logger.info(f'雪球冷却恢复成功（第 {i} 次探测），恢复采集')
                    return
                logger.warning(f'冷却探测第 {i} 次仍未解封: {res.error[:50]}')
            except Exception as e:
                logger.warning(f'冷却探测第 {i} 次失败: {str(e)[:80]}')
        raise SystemExit(2)

    # ── 写缓冲 ──

    def _flush(self, force: bool = False):
        if not self._buffer:
            return
        if not force and len(self._buffer) < FLUSH_ROWS and time.time() - self._last_flush < FLUSH_INTERVAL_S:
            return
        rows, self._buffer = self._buffer, []
        self._last_flush = time.time()
        try:
            # 保险去重：ON CONFLICT DO UPDATE 不允许同批出现重复约束值
            uniq: dict[tuple, StatusRow] = {(r.status_id, r.created_at): r for r in rows}
            rows = list(uniq.values())
            vals = []
            params: dict = {}
            for i, r in enumerate(rows):
                p = {f's{i}': r.status_id, f'c{i}': r.code, f'u{i}': r.user_id,
                     f't{i}': r.created_at, f'ti{i}': r.title, f'rw{i}': r.text_raw,
                     f'cl{i}': r.text_clean, f'm{i}': r.mark, f'v{i}': r.view_count,
                     f'l{i}': r.like_count, f'rt{i}': r.retweet_count,
                     f'rp{i}': r.reply_count, f'f{i}': r.fav_count,
                     f's{i}_src': r.source, f'y{i}': r.status_type}
                params.update(p)
                vals.append(f'(:s{i}, :c{i}, :u{i}, :t{i}, :ti{i}, :rw{i}, :cl{i}, :m{i}, '
                            f':v{i}, :l{i}, :rt{i}, :rp{i}, :f{i}, :s{i}_src, :y{i})')
            sql = (f"INSERT INTO xueqiu_status (status_id, code, user_id, created_at, title, "
                   f"text_raw, text_clean, mark, view_count, like_count, retweet_count, "
                   f"reply_count, fav_count, source, status_type) VALUES "
                   f"{','.join(vals)} "
                   f"ON CONFLICT (status_id, created_at) DO UPDATE SET "
                   f"view_count=EXCLUDED.view_count, like_count=EXCLUDED.like_count, "
                   f"retweet_count=EXCLUDED.retweet_count, reply_count=EXCLUDED.reply_count, "
                   f"fav_count=EXCLUDED.fav_count, fetched_at=CURRENT_TIMESTAMP")
            self.db.execute(text(sql), params)
            self.db.commit()
            logger.debug(f'雪球写缓冲 flush {len(rows)} 行')
        except Exception as e:
            self.db.rollback()
            logger.error(f'雪球写缓冲失败（丢 {len(rows)} 行，兜底由下轮游标覆盖）: {e}')

    # ── 状态回写 ──

    def _persist(self):
        if not self._dirty or time.time() - self._last_persist < STATE_PERSIST_S:
            return
        self._last_persist = time.time()
        codes = list(self._dirty)
        self._dirty.clear()
        try:
            for code in codes:
                st = self._states[code]
                self.db.execute(text(
                    "UPDATE xueqiu_crawl_state SET tier=:t, last_status_id=:l, "
                    "zero_streak=:z, full_streak=:fs, fail_streak=:fx, today_status=:ts, "
                    "last_ok_at=:lo, updated_at=CURRENT_TIMESTAMP WHERE code=:c"),
                    {'t': st['tier'], 'l': st['last_status_id'], 'z': st['zero_streak'],
                     'fs': st['full_streak'], 'fx': st['fail_streak'],
                     'ts': st.get('today_status', 'ok'), 'lo': st.get('last_ok_at'), 'c': code})
            self.db.commit()
        except Exception as e:
            self.db.rollback()
            logger.error(f'雪球状态回写失败: {e}')

    # ── 周期汇总日志 ──

    def _report(self):
        if time.time() - self._last_report < 300:
            return
        self._last_report = time.time()
        g = self.guard.stats
        logger.info(f'雪球轮询 5min 汇总: 今日新增 {self._new_today} 帖 | 请求 {g.requests} '
                    f'(ok {g.ok}) | 洗白 {g.washes} | 被动触发 {g.challenged} | '
                    f'缓冲 {len(self._buffer)} 行')

    # ── 心跳（xueqiu_runtime 单行 upsert，状态页指示灯数据源）──

    def _heartbeat(self):
        if time.time() - self._last_heartbeat < HEARTBEAT_S:
            return
        self._last_heartbeat = time.time()
        g = self.guard.stats
        try:
            self.db.execute(text(
                "INSERT INTO xueqiu_runtime (id, heartbeat_at, started_at, login_mode, pool_size, "
                "requests_total, ok_total, washes_total, challenged_total, posts_today, "
                "last_post_at, updated_at) "
                "VALUES (1, CURRENT_TIMESTAMP, :st, :lm, :pool, :req, :ok, :w, :ch, :pt, :lpa, "
                "CURRENT_TIMESTAMP) "
                "ON CONFLICT (id) DO UPDATE SET heartbeat_at=CURRENT_TIMESTAMP, "
                "started_at=EXCLUDED.started_at, login_mode=EXCLUDED.login_mode, "
                "pool_size=EXCLUDED.pool_size, requests_total=EXCLUDED.requests_total, "
                "ok_total=EXCLUDED.ok_total, washes_total=EXCLUDED.washes_total, "
                "challenged_total=EXCLUDED.challenged_total, posts_today=EXCLUDED.posts_today, "
                "last_post_at=EXCLUDED.last_post_at, updated_at=CURRENT_TIMESTAMP"),
                {'st': self._started_at, 'lm': self.adapter.login_mode,
                 'pool': len(self._states), 'req': g.requests, 'ok': g.ok,
                 'w': g.washes, 'ch': g.challenged, 'pt': self._new_today,
                 'lpa': self._last_post_at})
            self.db.commit()
        except Exception as e:
            self.db.rollback()
            logger.debug(f'雪球心跳写失败: {e}')
