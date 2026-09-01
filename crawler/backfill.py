"""
历史补数后台执行引擎。

BackfillManager 单例管理补数任务的创建、执行、取消、进度查询。
复用适配器层原子功能（adapter.fetch_xxx + writers.batch_upsert_xxx）。

特性:
  - 单日/跨日期差异化跳过策略
  - 停牌标记行写入 (is_suspended=true)
  - 后台线程执行，WS 进度推送
  - 全局单任务锁（baostock 非线程安全）
"""
import gc
import threading
import time
from datetime import date, datetime
from typing import Optional, Dict, List, Set
from dataclasses import dataclass, field
from loguru import logger
from sqlalchemy import text

from app.db.connection import get_sync_db
from app import signal as app_signal
from crawler.adapters import get_data_source_manager
from crawler.adapters.base import KlineRow, IndexKlineRow, FundamentalRow
import crawler.writers as writers

BATCH_SIZE = 20  # 每批 20 只，控制内存峰值，防止 OOM（服务器仅 1.8G 内存）


def _mem_avail_mb() -> int:
    """读取系统可用内存 (MB)。Linux 读 /proc/meminfo，其他平台返回 9999。"""
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) // 1024
    except Exception:
        pass
    return 9999  # 非 Linux 环境不限制


def _mem_used_pct() -> float:
    """系统内存使用百分比（基于 MemTotal / MemAvailable）。"""
    try:
        total = avail = 0
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    total = int(line.split()[1])
                elif line.startswith("MemAvailable:"):
                    avail = int(line.split()[1])
            if total > 0:
                return round((total - avail) / total * 100, 1)
    except Exception:
        pass
    return 0.0

LABEL_MAP = {
    "kline": "个股日K线",
    "index": "指数日K线",
    "etf": "ETF日K线",
    "fund": "基本面",
    "indicator": "基础指标加工",
    "calendar": "日历统计",
    "stock_master": "更新股票列表",
}


@dataclass
class BackfillTask:
    """补数任务状态。"""
    task_id: str
    task_type: str
    task_label: str
    status: str = "pending"
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    force: bool = False

    # 进度
    current_batch: int = 0
    total_batches: int = 0
    stocks_done: int = 0
    stocks_total: int = 0
    rows: int = 0
    errors: int = 0
    failed_codes: List[str] = field(default_factory=list)

    # 时间
    started_at: Optional[str] = None
    updated_at: Optional[str] = None
    completed_at: Optional[str] = None
    error_message: Optional[str] = None

    # 内部控制
    _stop_requested: bool = False
    _batch_size: int = 20

    def to_dict(self) -> dict:
        elapsed = 0
        if self.started_at:
            try:
                started = datetime.fromisoformat(self.started_at)
                if self.completed_at:
                    ended = datetime.fromisoformat(self.completed_at)
                    elapsed = (ended - started).total_seconds()
                else:
                    elapsed = (datetime.now() - started).total_seconds()
            except Exception:
                pass

        eta = 0
        if self.status == "running" and self.stocks_done > 0 and self.stocks_total > 0 and elapsed > 0:
            eta = (elapsed / self.stocks_done) * (self.stocks_total - self.stocks_done)

        return {
            "task_id": self.task_id,
            "task_type": self.task_type,
            "task_label": self.task_label,
            "status": self.status,
            "start_date": self.start_date,
            "end_date": self.end_date,
            "force": self.force,
            "progress": {
                "current_batch": self.current_batch,
                "total_batches": self.total_batches,
                "stocks_done": self.stocks_done,
                "stocks_total": self.stocks_total,
                "rows": self.rows,
                "errors": self.errors,
                "failed_codes": self.failed_codes[:10],
            },
            "started_at": self.started_at,
            "updated_at": self.updated_at,
            "completed_at": self.completed_at,
            "elapsed_seconds": int(elapsed),
            "eta_seconds": int(eta),
            "error_message": self.error_message,
        }


class BusyError(Exception):
    """已有任务运行中。"""
    def __init__(self, message: str, current_task: dict):
        super().__init__(message)
        self.current_task = current_task


class BackfillManager:
    """补数任务管理器（单例）。

    全局同一时刻只允许一个补数任务运行（baostock 非线程安全约束）。
    """
    _instance: Optional["BackfillManager"] = None

    def __init__(self):
        self._lock = threading.Lock()
        self._active_task: Optional[BackfillTask] = None
        self._last_completed_task: Optional[dict] = None
        self._history: List[BackfillTask] = []
        # 启动时恢复：将上次异常终止的 running 任务标记为 failed
        self._recover_orphaned_tasks()

    @classmethod
    def get_instance(cls) -> "BackfillManager":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    # ── 公开接口 ──

    def start(self, task_type: str, start_date: str = None,
              end_date: str = None, force: bool = False, batch_size: int = 20) -> str:
        """启动补数任务。返回 task_id。已有运行中任务时抛出 BusyError。"""
        with self._lock:
            if self._active_task and self._active_task.status == "running":
                raise BusyError(
                    "已有补数任务运行中，请等待完成或取消后再试",
                    self._active_task.to_dict(),
                )

            # 默认日期
            today = date.today().isoformat()
            if task_type != "fund":
                if not start_date:
                    start_date = f"{date.today().year - 5}-{today[5:]}"
                if not end_date:
                    end_date = today

            task_id = f"bf_{task_type}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            task = BackfillTask(
                task_id=task_id,
                task_type=task_type,
                task_label=LABEL_MAP.get(task_type, task_type),
                start_date=start_date,
                end_date=end_date,
                force=force,
                _batch_size=max(1, min(batch_size, 500)),
            )
            self._active_task = task
            self._persist_task(task)  # 落库：pending 状态

            thread = threading.Thread(target=self._run_task, args=(task,), daemon=True)
            thread.start()
            return task_id

    def cancel(self, task_id: str) -> dict:
        """取消运行中的任务。当前批次完成后停止，同时释放内存。

        Returns:
            {"ok": True, "message": "...", "current_batch": N, "total_batches": N, "eta_seconds": N}
        """
        with self._lock:
            if not self._active_task or self._active_task.task_id != task_id:
                raise ValueError("任务不存在或已完成")
            if self._active_task.status != "running":
                raise ValueError("任务不在运行中，无法取消")
            self._active_task._stop_requested = True
            self._update_task_db(self._active_task, status="cancelling")
            task = self._active_task
        gc.collect()

        # 估算当前批次剩余时间（单批约 2-3 分钟）
        eta = 0
        if task.stocks_total > 0 and task.stocks_done > 0 and task.started_at:
            try:
                elapsed = (datetime.now() - datetime.fromisoformat(task.started_at)).total_seconds()
                eta_per_batch = elapsed / max(task.current_batch, 1)
                eta = max(int(eta_per_batch * 0.8), 10)  # 保守估计 80% 单批时间，最少 10 秒
            except Exception:
                pass
        if eta == 0:
            eta = 120  # 默认 2 分钟

        return {
            "ok": True,
            "message": f"终止信号已发送，当前批次完成后停止（预计 {eta // 60} 分 {eta % 60} 秒）",
            "current_batch": task.current_batch,
            "total_batches": task.total_batches,
            "eta_seconds": eta,
        }

    def get_active_task(self) -> Optional[dict]:
        """获取当前运行中任务的进度（供 WS 广播使用）。"""
        if self._active_task:
            return self._active_task.to_dict()
        # 内存中没有，查 DB（容器重启后恢复场景）
        return self._load_active_from_db()

    def get_history(self, limit: int = 20) -> List[dict]:
        """获取历史任务列表（最近 N 条）。"""
        return self._query_tasks_db(limit=limit, exclude_running=True)

    def get_logs(self, page: int = 1, page_size: int = 20) -> dict:
        """获取分页补数日志。从 DB 查询，按 created_at 倒序。"""
        return self._query_tasks_db(page=page, page_size=page_size)

    # ── 内部 ──

    def _run_task(self, task: BackfillTask):
        """后台线程入口。"""
        task.status = "running"
        task.started_at = datetime.now().isoformat()
        task.updated_at = task.started_at
        self._persist_task(task)  # 落库：running 状态
        self._wake_ws()

        try:
            if task.task_type in ("kline", "index", "etf"):
                self._run_kline_backfill(task)
            elif task.task_type == "fund":
                self._run_fund_backfill(task)
            elif task.task_type == "fund_history":
                self._run_fund_history_backfill(task)
            elif task.task_type == "indicator":
                self._run_indicator_backfill(task)
            elif task.task_type == "calendar":
                self._run_calendar_backfill(task)
            elif task.task_type == "stock_master":
                self._run_stock_master(task)
        except Exception as e:
            import traceback
            tb = traceback.format_exc()[-800:]
            logger.error(f"[Backfill] 任务 {task.task_id} 异常: {e}\n{tb}")
            task.status = "failed"
            task.error_message = f"{type(e).__name__}: {e}"[:500]
        finally:
            if task.status == "running":
                task.status = "completed"  # 正常结束
            task.completed_at = datetime.now().isoformat()
            task.updated_at = task.completed_at
            # 确保持久化：写入后从 DB 读回验证
            from sqlalchemy import text
            for attempt in range(3):
                self._persist_task(task)
                try:
                    vdb = get_sync_db()
                    r = vdb.execute(text(
                        "SELECT status FROM backfill_tasks WHERE task_id=:tid"
                    ), {"tid": task.task_id}).scalar()
                    vdb.close()
                    if r == task.status:
                        break
                except Exception:
                    pass
                time.sleep(0.5)
            self._wake_ws()
            time.sleep(0.3)

            with self._lock:
                self._last_completed_task = task.to_dict()
                if self._active_task is task:
                    self._active_task = None
            self._wake_ws()

            def _clear():
                import time as _t2
                _t2.sleep(5)
                with self._lock:
                    if self._last_completed_task and self._last_completed_task.get('task_id') == task.task_id:
                        self._last_completed_task = None
            import threading as _th
            _th.Thread(target=_clear, daemon=True).start()

    # ═══════════════════════════════════════════════
    #  K线补数（个股/指数/ETF 通用）
    # ═══════════════════════════════════════════════

    def _run_kline_backfill(self, task: BackfillTask):
        """K线补数 — v3.2 按交易日逐日全市场拉取（tushare 主源）。

        - 按日期拉天然覆盖退市股历史、停牌股自然缺失（行业标准，无需停牌标记行）
        - 断点续传按"日期完整度"判定（当日行数 >= 活跃数 80% 视为已完成）
        - 配额预算：剩余调用 < 10 次自动收尾，断点留待明日续跑（不会中途耗尽报错）
        """
        from crawler.adapters import get_data_source_manager
        from crawler.adapters.tushare_quota import QuotaExhausted

        manager = get_data_source_manager()
        try:
            adapter = manager.get_source()
        except RuntimeError as e:
            task.status = "failed"
            task.error_message = f"主数据源不可用: {e}"
            return

        stock_type_map = {"kline": "stock", "index": "index", "etf": "etf"}
        stock_type = stock_type_map[task.task_type]

        # 确定表与写入函数
        if task.task_type == "index":
            table = "index_daily_quote"
            fetch_fn = adapter.fetch_index_kline
            write_fn = writers.batch_upsert_index_kline
        else:
            table = "daily_quote"
            fetch_fn = adapter.fetch_etf_kline if task.task_type == "etf" else adapter.fetch_stock_kline
            write_fn = writers.batch_upsert_kline

        # 区间内交易日（倒序 = 最新优先）
        tds = adapter._trade_days(task.start_date, task.end_date)
        if not tds:
            task.status = "failed"
            task.error_message = f"区间 {task.start_date}~{task.end_date} 内无交易日（检查交易日历）"
            return

        db = get_sync_db()
        try:
            # 断点续传：force 全补；否则跳过"当日行数 >= 当日历史基线 80%"的日期。
            # 历史年份市场容量远小于当前：基线 = 截至当日已上市(含退市, ipo_date<=当日)的该类型数量；
            # 若按当前活跃数(≈4171)判定，2000-2015 老日期永远达不到会被反复重拉、纯耗配额。
            active = db.execute(text(
                "SELECT COUNT(*) FROM stock_master WHERE status='N' AND stock_type=:t"
            ), {"t": stock_type}).scalar() or 0
            remaining_days = tds
            skipped = 0
            if not task.force:
                kept = []
                # 一次聚合取区间内各交易日已有行数（ETF 与个股同存 daily_quote，
                # 必须 JOIN stock_master 过滤类型，否则全表行数远超 ETF 基线导致历史全被跳过）
                if stock_type == "etf":
                    rows = db.execute(text(
                        "SELECT q.trade_date, COUNT(*) FROM daily_quote q "
                        "JOIN stock_master s ON s.stock_code=q.stock_code AND s.stock_type='etf' "
                        "WHERE q.trade_date BETWEEN :s AND :e GROUP BY q.trade_date"
                    ), {"s": task.start_date, "e": task.end_date}).fetchall()
                else:
                    rows = db.execute(text(
                        f"SELECT trade_date, COUNT(*) FROM {table} "
                        f"WHERE trade_date BETWEEN :s AND :e GROUP BY trade_date"
                    ), {"s": task.start_date, "e": task.end_date}).fetchall()
                cnt_map = {str(r[0]): r[1] for r in rows}
                # tushare fund_daily 场内 ETF 数据统一自 2012 年起（510050 首行 2012-03-07），
                # 更早日期 tushare/baostock 均无场内基金数据 → 以现有数据起点为界，
                # 起点之前 cnt==0 的日期直接跳过，防止断点判定缺失导致无限重拉耗配额
                etf_data_start = None
                if stock_type == "etf":
                    r0 = db.execute(text(
                        "SELECT MIN(q.trade_date) FROM daily_quote q "
                        "JOIN stock_master s ON s.stock_code=q.stock_code AND s.stock_type='etf'"
                    )).scalar()
                    etf_data_start = str(r0) if r0 else None
                for td in tds:
                    cnt = cnt_map.get(str(td), 0)
                    listed = db.execute(text(
                        "SELECT COUNT(*) FROM stock_master WHERE stock_type=:t AND ipo_date <= :d"
                    ), {"t": stock_type, "d": td}).scalar() or 0
                    thr = max(int(listed * 0.8), 1)
                    # 个股/指数按 80% 基线判定；ETF 数据源覆盖不全（fund_daily 2012 年起，
                    # 早期仅部分标的），当日有任一 ETF 行即视为完整，防止永远达不到基线反复重拉
                    complete = cnt >= thr if stock_type != "etf" else cnt > 0
                    # 已达当日基线 → 完成；该类型当日尚无上市标的 → 跳过；数据源覆盖起点之前无数据 → 跳过
                    if complete or listed == 0 or (etf_data_start and cnt == 0 and str(td) < etf_data_start):
                        skipped += 1
                    else:
                        kept.append(td)
                remaining_days = kept

            task.total_batches = len(remaining_days)
            task.stocks_total = active
            done = 0
            for i, td in enumerate(remaining_days):
                if getattr(task, "_stop_requested", False):
                    task.status = "cancelled"
                    task.error_message = f"已取消（已完成 {done}/{len(remaining_days)} 天）"
                    return
                # 配额预算：剩余 < 10 次则收尾（每天约 3 次调用）
                if adapter.quota.remaining() < 10:
                    if task.task_type == "etf":
                        self._supplement_etf_hfq(db, manager, task, task.start_date, task.end_date)
                    task.status = "completed"
                    task.error_message = (f"tushare 配额将尽（剩余 {adapter.quota.remaining()} 次），"
                                          f"已补 {done}/{len(remaining_days)} 天，剩余 {len(remaining_days) - done} 天可明日续跑")
                    return
                td_str = td.strftime("%Y-%m-%d")
                try:
                    rows = fetch_fn([], td_str, td_str)
                    saved = write_fn(db, rows)
                except QuotaExhausted as e:
                    if task.task_type == "etf":
                        self._supplement_etf_hfq(db, manager, task, task.start_date, task.end_date)
                    task.status = "completed"
                    task.error_message = (f"tushare 当日配额已用尽，已补 {done}/{len(remaining_days)} 天，"
                                          f"剩余 {len(remaining_days) - done} 天明日续跑")
                    return
                done += 1
                task.rows += saved
                task.stocks_done = done
                task.current_batch = done
                task.updated_at = datetime.now().isoformat()
                self._update_task_db(task)
                self._wake_ws()
                del rows
                gc.collect()

            if task.task_type == "etf":
                self._supplement_etf_hfq(db, manager, task, task.start_date, task.end_date)
            task.status = "completed"
            task.error_message = (f"补数完成 {done} 天（跳过已完成 {skipped} 天），共 {task.rows} 行"
                                  if skipped else f"补数完成 {done} 天，共 {task.rows} 行")
        except Exception as e:
            db.rollback()
            task.status = "failed"
            task.error_message = str(e)[:500]
        finally:
            try: adapter._logout()
            except Exception: pass
            db.close()

    def _supplement_etf_hfq(self, db, manager, task, start_date, end_date):
        """任务级 ETF 后复权补充：按代码全区间一次性查询（baostock），而非逐日逐只。

        - 断点：只补 stock_master 中"尚无任何 close_hfq 行"的 ETF，已补过则秒过
        - 每只 ETF 一次 query_history_k_data_plus 返回整个区间，避免 O(天数×只数) 爆炸
        - baostock 不可用/失败仅告警：close_hfq 保持 close（回退口径与 kline 一致）
        """
        try:
            if not manager.supplement_healthy():
                return
            sups = manager.get_supplement()
            rows = db.execute(text(
                "SELECT DISTINCT q.stock_code FROM daily_quote q "
                "JOIN stock_master s ON s.stock_code=q.stock_code AND s.stock_type='etf' "
                "WHERE q.trade_date BETWEEN :s AND :e AND q.close_hfq IS NULL"
            ), {"s": start_date, "e": end_date}).fetchall()
            codes = [r[0] for r in rows]
            if not codes:
                return
            total = len(codes)
            logger.info(f"[backfill] ETF 复权补充启动: {total} 只（区间 {start_date}~{end_date}）")
            for bi in range(0, total, 200):
                if getattr(task, "_stop_requested", False):
                    return
                b = codes[bi:bi + 200]
                hfq_map = sups.fetch_etf_hfq(b, start_date, end_date)
                if not hfq_map:
                    continue
                # 按 2 万行一批 VALUES 批量更新（200 只 × 25 年 ≈ 120 万行，单条 UPDATE 过大）
                items = list(hfq_map.items())
                for ii in range(0, len(items), 20000):
                    chunk = items[ii:ii + 20000]
                    vals = ",".join(
                        f"('{c.replace(chr(39), chr(39)*2)}', '{d}', {v})" for (c, d), v in chunk
                    )
                    db.execute(text(
                        "UPDATE daily_quote q SET close_hfq=v.close_hfq "
                        f"FROM (VALUES {vals}) AS v(code, date, close_hfq) "
                        "WHERE q.stock_code=v.code AND q.trade_date=v.date"
                    ))
                db.commit()
                task.error_message = f"ETF 复权补充 {min(bi + 200, total)}/{total} 只"
                task.updated_at = datetime.now().isoformat()
                self._update_task_db(task)
                self._wake_ws()
            logger.info(f"[backfill] ETF 复权补充完成: {total} 只")
        except Exception as ex:
            db.rollback()
            logger.warning(f"[backfill] ETF 复权补充失败（close_hfq 保持 close）: {ex}")

    # ═══════════════════════════════════════════════
    #  基本面 PE 历史回填（fund_history）
    # ═══════════════════════════════════════════════

    def _run_fund_history_backfill(self, task: BackfillTask):
        """PE 历史回填：逐交易日拉 daily_basic 写 stock_fundamentals_history（每股票每日 PE/PB）。

        - 断点续传：某交易日 history 已有行数 >= 活跃数 80% 视为完成
        - 配额预算：tushare 剩余 < 10 次自动收尾（每天 1 次调用）
        - 支持取消
        """
        from crawler.adapters import get_data_source_manager
        from crawler.adapters.tushare_quota import QuotaExhausted
        from crawler.writers import append_fundamentals_history

        manager = get_data_source_manager()
        try:
            adapter = manager.get_source()
        except RuntimeError as e:
            task.status = "failed"
            task.error_message = f"主数据源不可用: {e}"
            return

        tds = adapter._trade_days(task.start_date, task.end_date)
        if not tds:
            task.status = "failed"
            task.error_message = f"区间 {task.start_date}~{task.end_date} 无交易日"
            return

        db = get_sync_db()
        try:
            # 断点续传：当日 history 行数 >= 当日已上市股票数 80% 视为已完成。
            # 基线必须按"当日上市数"逐日取（与 _run_kline_backfill 同模式）：
            # 若用当前活跃数(~5200)，2000-2015 老日期(仅数百至数千行)永远达不到
            # 80% 阈值，force=False 重复触发会整段重拉、纯耗配额。
            # 一次聚合取 ipo_date 分布，Python 累计得到每日基线，避免逐日 COUNT。
            ipo_rows = db.execute(text(
                "SELECT ipo_date, COUNT(*) FROM stock_master "
                "WHERE stock_type='stock' AND ipo_date IS NOT NULL "
                "GROUP BY ipo_date ORDER BY ipo_date"
            )).fetchall()
            import bisect
            ipo_dates = [str(r[0]) for r in ipo_rows]
            ipo_cum = []
            acc = 0
            for r in ipo_rows:
                acc += r[1] or 0
                ipo_cum.append(acc)

            def baseline_of(td_str: str) -> int:
                i = bisect.bisect_right(ipo_dates, td_str)
                return ipo_cum[i - 1] if i > 0 else 0

            remaining = tds
            skipped = 0
            if not task.force:
                # 断点判断用新列完整度（turnover_rate 是 daily_basic 的代表列）：
                # 补列场景下 COUNT(*) 恒 ≥ 基线（PE/PB 行已存在），会错误地全部跳过。
                # 一次聚合取全区间（逐日 COUNT 在 1700 万行表上无索引会拖死预检查）。
                cnt_rows = db.execute(text(
                    "SELECT report_date, COUNT(turnover_rate) FROM stock_fundamentals_history "
                    "WHERE report_date BETWEEN :s AND :e GROUP BY report_date"
                ), {"s": str(tds[-1])[:10], "e": str(tds[0])[:10]}).fetchall()  # _trade_days 倒序：[-1]=最早 [0]=最新
                cnt_map = {str(r[0])[:10]: r[1] for r in cnt_rows}
                kept = []
                for td in tds:
                    td_str = td.strftime("%Y-%m-%d") if hasattr(td, 'strftime') else str(td)[:10]
                    cnt = cnt_map.get(td_str, 0)
                    thr = max(int(baseline_of(td_str) * 0.8), 1)
                    if cnt >= thr:
                        skipped += 1
                    else:
                        kept.append(td)
                remaining = kept
                logger.info(f"[backfill] fund_history 预检查: 待补 {len(kept)} 天（新列已完整跳过 {skipped} 天）")
            task.total_batches = len(remaining)
            done = 0
            total_rows = 0
            for i, td in enumerate(remaining):
                if getattr(task, "_stop_requested", False):
                    task.status = "cancelled"
                    task.error_message = f"已取消（完成 {done}/{len(remaining)} 天，共 {total_rows} 行）"
                    return
                if adapter.quota.remaining() < 10:
                    task.status = "completed"
                    task.error_message = (f"tushare 配额将尽（剩余 {adapter.quota.remaining()} 次），"
                                          f"已回填 {done}/{len(remaining)} 天，剩余可明日续跑")
                    return
                td_str = td.strftime("%Y-%m-%d")
                try:
                    rows = adapter.fetch_fundamentals([], trade_date=td_str)
                    saved = append_fundamentals_history(db, rows)
                except QuotaExhausted as e:
                    task.status = "completed"
                    task.error_message = f"tushare 当日配额用尽，已回填 {done} 天，明日续跑"
                    return
                done += 1
                total_rows += saved
                task.rows = total_rows
                task.stocks_done = done
                task.current_batch = done
                task.updated_at = datetime.now().isoformat()
                self._update_task_db(task)
                self._wake_ws()
                del rows
                gc.collect()

            task.status = "completed"
            task.error_message = (f"PE 历史回填完成 {done} 天（跳过已完成 {skipped} 天），共 {total_rows} 行"
                                  if skipped else f"PE 历史回填完成 {done} 天，共 {total_rows} 行")
        except Exception as e:
            db.rollback()
            task.status = "failed"
            task.error_message = str(e)[:500]
        finally:
            db.close()

    # ═══════════════════════════════════════════════
    #  基本面补数
    # ═══════════════════════════════════════════════

    def _run_indicator_backfill(self, task):
        """基础指标加工补数 — 已废弃（v2.6），KEPL feature_compute 替代。"""
        task.status = "failed"
        task.error_message = "指标补数已废弃（v2.6），请使用特征管理 → 特征补数 (/api/features/{id}/compute-range)"
        db.close()
        return
        """基础指标加工补数。依赖 daily_quote 已有数据。"""
    # ═══════════════════════════════════════════════

    def _run_calendar_backfill(self, task: BackfillTask):
        """日历完整度补数：遍历日期区间，计算每日各表行数并写入 daily_completeness。"""
        from datetime import date as _dt_date, timedelta
        db = get_sync_db()

        # 获取日期范围内的交易日
        tc_rows = db.execute(text(
            "SELECT cal_date FROM trade_calendar WHERE cal_date BETWEEN :s AND :e AND is_trade_day=true ORDER BY cal_date"
        ), {"s": task.start_date, "e": task.end_date}).fetchall()
        trade_dates = [str(r[0]) for r in tc_rows]

        if not trade_dates:
            task.status = "completed"
            task.error_message = "日期范围内无交易日"
            db.close()
            return

        task.stocks_total = len(trade_dates)
        task.total_batches = len(trade_dates)

        for idx, td in enumerate(trade_dates):
            if task._stop_requested:
                task.status = "cancelled"
                db.close()
                return

            task.current_batch = idx + 1

            try:
                # 个股
                stock = db.execute(text("SELECT COUNT(*) FROM daily_quote WHERE trade_date=:d AND NOT (LEFT(stock_code,2)='15' OR LEFT(stock_code,1)='5')"), {"d": td}).scalar() or 0
                # 指数
                idx_rows = db.execute(text("SELECT COUNT(*) FROM index_daily_quote WHERE trade_date=:d"), {"d": td}).scalar() or 0
                # ETF
                etf   = db.execute(text("SELECT COUNT(*) FROM daily_quote WHERE trade_date=:d AND (LEFT(stock_code,2)='15' OR LEFT(stock_code,1)='5')"), {"d": td}).scalar() or 0
                # 基本面（当日所在季度有财报的股票数）
                fund = db.execute(text("SELECT COUNT(*) FROM stock_fundamentals sf JOIN stock_master sm ON sm.stock_code=sf.stock_code AND sm.stock_type='stock' AND sm.status='N'")).scalar() or 0  # 快照：活跃A股有基本面数

                # 分母：当日已上市的各类型总数
                stock_bl = db.execute(text("SELECT COUNT(*) FROM stock_master WHERE stock_type='stock' AND status='N' AND ipo_date <= :d"), {"d": td}).scalar() or 0
                index_bl = db.execute(text("SELECT COUNT(*) FROM stock_master WHERE stock_type='index' AND ipo_date <= :d"), {"d": td}).scalar() or 0
                etf_bl   = db.execute(text("SELECT COUNT(*) FROM stock_master WHERE stock_type='etf' AND ipo_date <= :d"), {"d": td}).scalar() or 0
                fund_bl  = stock_bl  # 与个股分母相同

                db.execute(text("""
                    INSERT INTO daily_completeness (trade_date, stock_rows, index_rows, etf_rows, fund_rows,
                        stock_baseline, index_baseline, etf_baseline, fund_baseline)
                    VALUES (:d, :sr, :ir, :er, :fr, :sb, :ib, :eb, :fb)
                    ON CONFLICT (trade_date) DO UPDATE SET
                        stock_rows=EXCLUDED.stock_rows, index_rows=EXCLUDED.index_rows,
                        etf_rows=EXCLUDED.etf_rows, fund_rows=EXCLUDED.fund_rows,
                        stock_baseline=EXCLUDED.stock_baseline, index_baseline=EXCLUDED.index_baseline,
                        etf_baseline=EXCLUDED.etf_baseline, fund_baseline=EXCLUDED.fund_baseline,
                        updated_at=CURRENT_TIMESTAMP
                """), {"d": td, "sr": stock, "ir": idx_rows, "er": etf, "fr": fund,
                       "sb": stock_bl, "ib": index_bl, "eb": etf_bl, "fb": fund_bl})
                db.commit()

                task.rows += stock + idx_rows + etf
                task.stocks_done = idx + 1

            except Exception as e:
                logger.warning(f"[Backfill] calendar {td} 失败: {e}")
                task.errors += 1
                try: db.rollback()
                except: pass

            if idx % 30 == 0:
                task.updated_at = datetime.now().isoformat()
                self._update_task_db(task)
                self._wake_ws()
                gc.collect()

        task.status = "completed"
        task.updated_at = datetime.now().isoformat()
        task.stocks_done = len(trade_dates)
        self._update_task_db(task)
        db.close()

    # ═══════════════════════════════════════════════
    #  stock_master 更新
    # ═══════════════════════════════════════════════

    def _run_stock_master(self, task):
        """更新 stock_master：拉全量股票/指数/ETF 列表，UPSERT 名称/IPO/交易所，标记退市。"""
        from crawler.adapters import get_data_source_manager
        from sqlalchemy import text as _t
        from datetime import datetime
        from loguru import logger

        manager = get_data_source_manager()
        # 清除健康缓存 + 重试（数据源偶发连接失败）
        adapter = None
        for retry_i in range(3):
            manager._health_cache.clear()
            try:
                adapter = manager.get_source()
                break
            except RuntimeError:
                if retry_i < 2:
                    import time
                    time.sleep(5)
                else:
                    raise
        if adapter is None:
            task.status = "failed"
            task.error_message = "数据源获取失败，重试3次均失败"
            return
        adapter._ensure_login()

        type_labels = {"stock": "个股", "index": "指数", "etf": "ETF"}
        all_updated = 0
        all_delisted = 0
        db = get_sync_db()
        try:
            for stype in ("stock", "index", "etf"):
                stock_list = adapter.get_stock_list(stype)
                # 数据源不支持 index/ETF 列表 → 从已有数据反推
                if not stock_list and stype != "stock":
                    if stype == "index":
                        rows = db.execute(_t("SELECT DISTINCT index_code FROM index_daily_quote")).fetchall()
                        stock_list = [type("_", (), {"stock_code": r[0], "stock_name": "", "ipo_date": None})() for r in rows]
                    elif stype == "etf":
                        rows = db.execute(_t("SELECT DISTINCT stock_code FROM daily_quote WHERE LEFT(stock_code,2)='15' OR LEFT(stock_code,1)='5'")).fetchall()
                        stock_list = [type("_", (), {"stock_code": r[0], "stock_name": "", "ipo_date": None})() for r in rows]
                updated = delisted = 0
                current_codes = set()
                total = len(stock_list)
                for i, s in enumerate(stock_list):
                    code = getattr(s, "stock_code", "") or ""
                    current_codes.add(code)
                    ipo = s.ipo_date if hasattr(s, "ipo_date") and s.ipo_date else None
                    name = getattr(s, "stock_name", code) or code
                    # 交易所推断：6→SSE, 0/2/3→SZSE, 4/8/9→BSE（000/001/002/003 均为深交所）
                    from crawler.adapters.base import code_to_exchange
                    ex = code_to_exchange(code)
                    db.execute(_t("""
                        INSERT INTO stock_master (stock_code, stock_type, stock_name, exchange, ipo_date, status, delist_date, is_hs, act_name, area, industry, reg_capital, employees, main_business)
                        VALUES (:c, :t, :n, :ex, :ipo, 'N', :dlist, :hs, :act, :area, :ind, :rc, :emp, :biz)
                        ON CONFLICT (stock_code, stock_type) DO UPDATE SET
                            stock_name = COALESCE(EXCLUDED.stock_name, stock_master.stock_name),
                            exchange = COALESCE(EXCLUDED.exchange, stock_master.exchange),
                            ipo_date = COALESCE(EXCLUDED.ipo_date, stock_master.ipo_date),
                            delist_date = EXCLUDED.delist_date,
                            is_hs = EXCLUDED.is_hs,
                            act_name = COALESCE(EXCLUDED.act_name, stock_master.act_name),
                            area = COALESCE(EXCLUDED.area, stock_master.area),
                            industry = COALESCE(EXCLUDED.industry, stock_master.industry),
                            reg_capital = COALESCE(EXCLUDED.reg_capital, stock_master.reg_capital),
                            employees = COALESCE(EXCLUDED.employees, stock_master.employees),
                            main_business = COALESCE(EXCLUDED.main_business, stock_master.main_business),
                            status = CASE WHEN stock_master.status = 'D' THEN stock_master.status ELSE 'N' END
                    """), {"c": code, "t": stype, "n": name, "ex": ex, "ipo": ipo, "dlist": getattr(s,"delist_date",None), "hs": getattr(s,"is_hs",None), "act": getattr(s,"act_name",None), "area": getattr(s,"area",None), "ind": getattr(s,"industry",None), "rc": getattr(s,"reg_capital",None), "emp": getattr(s,"employees",None), "biz": getattr(s,"main_business",None)})
                    updated += 1
                    all_updated += 1
                    if i % 300 == 0:
                        task.stocks_done = all_updated
                        task.updated_at = datetime.now().isoformat()
                        self._persist_task(task)
                        self._wake_ws()
                # 标记退市
                all_known = db.execute(_t(
                    "SELECT stock_code FROM stock_master WHERE stock_type=:t"
                ), {"t": stype}).fetchall()
                for r in all_known:
                    if r[0] not in current_codes:
                        db.execute(_t("UPDATE stock_master SET status='D' WHERE stock_code=:c AND stock_type=:t"),
                                  {"c": r[0], "t": stype})
                        delisted += 1
                        all_delisted += 1
                logger.info(f"[stock_master] {type_labels[stype]}: 更新 {updated}, 退市 {delisted}")
            db.commit()
            # 申万行业层级（industry_l1/l2，index_member_all 分页拉全，~2-3 次调用）
            try:
                sw = adapter.fetch_sw_industry()
                if sw:
                    import pandas as _pd
                    codes = list(sw.keys())
                    l1s = [sw[c]['l1'] for c in codes]
                    l2s = [sw[c]['l2'] for c in codes]
                    db.execute(_t("""
                        UPDATE stock_master sm SET
                            industry_l1 = v.l1, industry_l2 = v.l2
                        FROM (SELECT unnest(:codes) AS stock_code,
                                     unnest(:l1s) AS l1, unnest(:l2s) AS l2) v
                        WHERE sm.stock_code = v.stock_code AND sm.stock_type = 'stock'
                    """), {"codes": codes, "l1s": l1s, "l2s": l2s})
                    db.commit()
                    task.error_message = f"更新 {all_updated} 只 (退市 {all_delisted}), 申万行业 {len(sw)} 只"
                    logger.info(f"[stock_master] 申万行业更新 {len(sw)} 只")
            except Exception as e:
                db.rollback()
                logger.warning(f"[stock_master] 申万行业更新失败（可稍后重试）: {e}")

            # 公司信息补充（reg_capital/employees/main_business，stock_company 逐只）
            # 增量：只补缺失字段；分批 200 只 + 进度 + 可取消；配额将尽自动收尾
            # dag 场景限制单次量（task._company_limit 由调用方控制），避免阻塞主流程
            comp_limit = getattr(task, "_company_limit", None)
            try:
                missing = db.execute(_t("""
                    SELECT stock_code FROM stock_master
                    WHERE stock_type='stock' AND status='N'
                      AND (reg_capital IS NULL OR employees IS NULL OR main_business IS NULL)
                    ORDER BY stock_code LIMIT :cl
                """), {"cl": comp_limit or 2000}).fetchall()
                if missing:
                    codes = [r[0] for r in missing]
                    logger.info(f"[stock_master] 补充公司信息（缺失 {len(codes)} 只，分批）")
                    filled = 0
                    for bi in range(0, len(codes), 200):
                        if getattr(task, "_stop_requested", False):
                            task.error_message = f"已取消（公司信息补充 {filled}/{len(codes)}，可下次续跑）"
                            break
                        if adapter.quota.remaining() < 5:
                            task.error_message = f"公司信息补充 {filled}/{len(codes)}（tushare 配额将尽，可明日续跑）"
                            break
                        batch = codes[bi:bi + 200]
                        comp = adapter.fetch_company_batch(batch)
                        if comp:
                            cc = list(comp.keys())
                            db.execute(_t("""
                                UPDATE stock_master sm SET
                                    reg_capital = COALESCE(sm.reg_capital, v.rc),
                                    employees = COALESCE(sm.employees, v.emp),
                                    main_business = COALESCE(sm.main_business, v.biz)
                                FROM (SELECT unnest(:codes) AS stock_code,
                                             unnest(:rcs) AS rc,
                                             unnest(:emps) AS emp,
                                             unnest(:bizs) AS biz) v
                                WHERE sm.stock_code = v.stock_code AND sm.stock_type = 'stock'
                            """), {
                                "codes": cc,
                                "rcs": [comp[c]['reg_capital'] for c in cc],
                                "emps": [comp[c]['employees'] for c in cc],
                                "bizs": [comp[c]['main_business'] for c in cc],
                            })
                            db.commit()
                            filled += len(comp)
                        task.updated_at = datetime.now().isoformat()
                        task.error_message = f"公司信息补充 {filled}/{len(codes)} 只"
                        self._update_task_db(task)
                        self._wake_ws()
                    logger.info(f"[stock_master] 公司信息补充 {filled} 只")
            except Exception as e:
                db.rollback()
                logger.warning(f"[stock_master] 公司信息补充失败（可稍后重试）: {e}")
            task.rows = all_updated
            task.stocks_done = all_updated
            task.stocks_total = all_updated
            task.error_message = f"更新 {all_updated} 只 (退市 {all_delisted})"
        except Exception as e:
            db.rollback()
            task.status = "failed"
            task.error_message = str(e)[:500]
        finally:
            try: adapter._logout()
            except: pass
            db.close()

    def _run_fund_backfill(self, task):
        """基本面补数：tushare 主字段 → writers UPSERT → baostock 补 ROE 等 → 行业回写。

        配额耗尽（QuotaExhausted）时任务收尾提示，不断言失败。
        """
        from crawler.adapters import get_data_source_manager
        from crawler.adapters.tushare_quota import QuotaExhausted
        from sqlalchemy import text as _t
        from datetime import datetime
        from crawler.writers import batch_upsert_fundamentals, supplement_fundamentals_extra

        manager = get_data_source_manager()
        try:
            adapter = manager.get_source()
        except RuntimeError as e:
            task.status = "failed"
            task.error_message = f"主数据源不可用: {e}"
            return

        db = get_sync_db()
        try:
            # 拉取全市场基本面（tushare daily_basic 最近交易日）
            rows = adapter.fetch_fundamentals(None)
            if not rows:
                task.rows = 0
                task.stocks_done = 0
                task.error_message = "无基本面数据（数据源可能暂无当日数据）"
                return

            # 统一 writers 批量 UPSERT（含 market_cap 补全 + 逐字段 COALESCE）
            upserted = batch_upsert_fundamentals(db, rows)
            # 日度 PE/PB 追加 history（PE 历史走势图数据源）
            from crawler.writers import append_fundamentals_history
            hist = append_fundamentals_history(db, rows)
            task.rows = upserted
            task.stocks_done = upserted
            task.updated_at = datetime.now().isoformat()
            self._persist_task(task)
            self._wake_ws()

            # baostock 补充 ROE/营收/净利（不可用时主字段已入库，缺口留待下次补数）
            # 增量语义：只补缺失股票（roe/revenue_yoy/profit_yoy 全空），单任务上限 1500 只；
            # 分批（100 只/批）提交进度，支持取消，剩余留待下次补数续跑
            sup = manager.get_supplement()
            if sup is not None and manager.supplement_healthy():
                try:
                    latest_td = db.execute(_t(
                        "SELECT MAX(trade_date) FROM stock_fundamentals"
                    )).scalar()
                    missing = db.execute(_t("""
                        SELECT stock_code FROM stock_fundamentals
                        WHERE trade_date = :d AND roe IS NULL
                          AND revenue_yoy IS NULL AND profit_yoy IS NULL
                        ORDER BY stock_code LIMIT 1500
                    """), {"d": latest_td}).fetchall()
                    if missing:
                        codes = [r[0] for r in missing]
                        logger.info(f"[fund_backfill] baostock 补充缺失 ROE 股票 {len(codes)} 只（分批补充）")
                        total_fixed = 0
                        for i in range(0, len(codes), 100):
                            if getattr(task, "_stop_requested", False):
                                task.status = "cancelled"
                                task.error_message = f"已取消（ROE 已补充 {total_fixed} 只，剩余下次续跑）"
                                return
                            batch = codes[i:i + 100]
                            extras = sup.fetch_fundamentals_extra(batch)
                            fixed = supplement_fundamentals_extra(db, extras)
                            total_fixed += fixed
                            task.rows += fixed
                            task.error_message = f"ROE 补充 {total_fixed}/{len(codes)} 只"
                            task.updated_at = datetime.now().isoformat()
                            self._update_task_db(task)
                            self._wake_ws()
                        if total_fixed:
                            logger.info(f"[fund_backfill] baostock 补充 ROE/营收/净利 {total_fixed} 只")
                    else:
                        logger.info("[fund_backfill] ROE 等字段已完整，无需补充")
                except Exception as e:
                    logger.warning(f"[fund_backfill] baostock 补充失败（ROE 等留待下次补数）: {e}")

            # 注：行业数据由 stock_master 维护（申万 industry_l1/l2，见 stock_master 补数），
            # stock_fundamentals 已无 industry 列，不再做跨表回写

            task.stocks_total = upserted
            task.error_message = f"基本面更新 {upserted} 只"
            logger.info(f"[fund_backfill] 完成: {upserted} 行")
        except QuotaExhausted as e:
            task.status = "completed"
            task.error_message = "tushare 配额用尽，基本面未更新，明日续跑"
        except Exception as e:
            db.rollback()
            task.status = "failed"
            task.error_message = str(e)[:500]
        finally:
            try: adapter._logout()
            except: pass
            db.close()

    # ═══════════════════════════════════════════════
    #  工具函数
    @staticmethod
    def _get_db():
        """获取同步 DB session（连接失败返回 None，由调用方降级）。"""
        try:
            from app.db.connection import get_sync_db
            return get_sync_db()
        except Exception:
            return None

    def _persist_task(self, task: BackfillTask):
        """写入/更新 backfill_tasks 表。"""
        db = self._get_db()
        if db is None:
            return
        try:
            db.execute(text("""
                INSERT INTO backfill_tasks (task_id, task_type, task_label, status,
                    start_date, end_date, force, total_batches, current_batch,
                    stocks_total, stocks_done, rows, errors, error_message,
                    started_at, completed_at)
                VALUES (:tid, :tt, :tl, :st, :sd, :ed, :f, :tb, :cb,
                    :sto, :sdo, :r, :e, :em, :sa, :ca)
                ON CONFLICT (task_id) DO UPDATE SET
                    status=EXCLUDED.status, total_batches=EXCLUDED.total_batches,
                    current_batch=EXCLUDED.current_batch, stocks_total=EXCLUDED.stocks_total,
                    stocks_done=EXCLUDED.stocks_done, rows=EXCLUDED.rows,
                    errors=EXCLUDED.errors, error_message=EXCLUDED.error_message,
                    started_at=EXCLUDED.started_at, completed_at=EXCLUDED.completed_at
            """), {
                "tid": task.task_id, "tt": task.task_type, "tl": task.task_label,
                "st": task.status, "sd": task.start_date, "ed": task.end_date,
                "f": task.force, "tb": task.total_batches, "cb": task.current_batch,
                "sto": task.stocks_total, "sdo": task.stocks_done,
                "r": task.rows, "e": task.errors, "em": task.error_message,
                "sa": task.started_at, "ca": task.completed_at,
            })
            db.commit()
        except Exception as e:
            logger.warning(f"[Backfill] 落库失败 {task.task_id}: {e}")
        finally:
            try:
                db.close()
            except Exception:
                pass

    def _update_task_db(self, task: BackfillTask, status: str = None):
        """更新进度字段（轻量 UPDATE，批次间频繁调用）。"""
        db = self._get_db()
        if db is None:
            return
        try:
            sets = ["current_batch=:cb", "stocks_done=:sdo", "rows=:r", "errors=:e", "stocks_total=:st", "total_batches=:tb"]
            params = {"tid": task.task_id, "cb": task.current_batch,
                      "sdo": task.stocks_done, "r": task.rows, "e": task.errors,
                      "st": task.stocks_total, "tb": task.total_batches}
            if status:
                sets.append("status=:st")
                params["st"] = status
            db.execute(text(f"UPDATE backfill_tasks SET {', '.join(sets)} WHERE task_id=:tid"), params)
            db.commit()
        except Exception as e:
            logger.warning(f"[Backfill] 更新进度失败 {task.task_id}: {e}")
        finally:
            try:
                db.close()
            except Exception:
                pass

    def _query_tasks_db(self, page: int = 1, page_size: int = 20,
                         limit: int = 0, exclude_running: bool = False) -> dict | list:
        """从 DB 查询补数任务。

        Returns:
            dict (分页) 或 list (limit 模式)
        """
        db = self._get_db()
        if db is None:
            return {"items": [], "total": 0, "page": page, "page_size": page_size, "total_pages": 1}

        try:
            where = "WHERE status != 'running'" if exclude_running else ""
            count_r = db.execute(text(f"SELECT COUNT(*) FROM backfill_tasks {where}")).fetchone()
            total = count_r[0] if count_r else 0

            if limit > 0:
                rows = db.execute(text(
                    f"SELECT * FROM backfill_tasks {where} ORDER BY created_at DESC LIMIT :lim"
                ), {"lim": limit}).fetchall()
                return [_row_to_dict(r) for r in rows]

            total_pages = max((total + page_size - 1) // page_size, 1)
            offset = (page - 1) * page_size
            rows = db.execute(text(
                f"SELECT * FROM backfill_tasks {where} ORDER BY created_at DESC LIMIT :lim OFFSET :off"
            ), {"lim": page_size, "off": offset}).fetchall()

            items = [_row_to_dict(r) for r in rows]
            # 如果内存中有运行中的任务，插入到列表头部
            if self._active_task and self._active_task.status == "running":
                active_dict = self._active_task.to_dict()
                if not any(i.get("task_id") == active_dict["task_id"] for i in items):
                    items.insert(0, active_dict)
                    total += 1

            return {
                "items": items, "total": total,
                "page": page, "page_size": page_size,
                "total_pages": max((total + page_size - 1) // page_size, 1),
            }
        except Exception as e:
            logger.warning(f"[Backfill] 查询日志失败: {e}")
            return {"items": [], "total": 0, "page": page, "page_size": page_size, "total_pages": 1}
        finally:
            try:
                db.close()
            except Exception:
                pass

    def _load_active_from_db(self) -> Optional[dict]:
        """从 DB 加载运行中的任务（容器重启恢复）。"""
        db = self._get_db()
        if db is None:
            return None
        try:
            row = db.execute(text(
                "SELECT * FROM backfill_tasks WHERE status = 'running' ORDER BY created_at DESC LIMIT 1"
            )).fetchone()
            if row:
                return _row_to_dict(row)
            return None
        except Exception:
            return None
        finally:
            try:
                db.close()
            except Exception:
                pass

    def _recover_orphaned_tasks(self):
        """启动时将上次异常终止的任务标记为 'failed'。同时恢复 model_versions 的 TRAINING 状态。"""
        db = self._get_db()
        if db is None:
            return
        try:
            result = db.execute(text(
                "UPDATE backfill_tasks SET status='failed', error_message='服务重启，任务中断', completed_at=CURRENT_TIMESTAMP WHERE status IN ('running','cancelling')"
            ))
            db.commit()
            if result.rowcount and result.rowcount > 0:
                logger.info(f"[Backfill] 恢复: {result.rowcount} 个孤儿任务标记为 failed")
            # 恢复卡在 TRAINING 状态的模型版本
            r2 = db.execute(text(
                "UPDATE model_versions SET status='DRAFT' WHERE status='TRAINING'"
            ))
            db.commit()
            if r2.rowcount and r2.rowcount > 0:
                logger.info(f"[Backfill] 恢复: {r2.rowcount} 个模型版本 TRAINING→DRAFT")
        except Exception as e:
            logger.warning(f"[Backfill] 恢复孤儿任务失败: {e}")
        finally:
            try:
                db.close()
            except Exception:
                pass

    @staticmethod
    def _wake_ws():
        """唤醒 WebSocket 广播循环。"""
        try:
            app_signal.wake_dag_broadcast()
        except Exception:
            pass


def _row_to_dict(row) -> dict:
    """将 DB 行转为 dict（兼容 to_dict() 输出格式）。"""
    return {
        "task_id": row.task_id,
        "task_type": row.task_type,
        "task_label": row.task_label or "",
        "status": row.status,
        "start_date": str(row.start_date) if row.start_date else None,
        "end_date": str(row.end_date) if row.end_date else None,
        "force": bool(row.force),
        "progress": {
            "current_batch": row.current_batch or 0,
            "total_batches": row.total_batches or 0,
            "stocks_done": row.stocks_done or 0,
            "stocks_total": row.stocks_total or 0,
            "rows": row.rows or 0,
            "errors": row.errors or 0,
            "failed_codes": [],
        },
        "started_at": str(row.started_at) if row.started_at else None,
        "completed_at": str(row.completed_at) if row.completed_at else None,
        "updated_at": str(row.completed_at or row.started_at) if (row.completed_at or row.started_at) else None,
        "elapsed_seconds": _calc_elapsed(row.started_at, row.completed_at),
        "eta_seconds": 0,
        "error_message": row.error_message,
    }


def _calc_elapsed(started_at, completed_at) -> int:
    if not started_at:
        return 0
    try:
        from datetime import datetime as dt
        start = dt.fromisoformat(str(started_at))
        end = dt.fromisoformat(str(completed_at)) if completed_at else dt.now()
        return int((end - start).total_seconds())
    except Exception:
        return 0
