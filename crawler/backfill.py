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

BATCH_SIZE = 50  # 每批 50 只，约 2-3 分钟完成一批，进度反馈更及时

LABEL_MAP = {
    "kline": "个股日K线",
    "index": "指数日K线",
    "etf": "ETF日K线",
    "fund": "基本面",
    "indicator": "基础指标加工",
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
              end_date: str = None, force: bool = False) -> str:
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
            )
            self._active_task = task
            self._persist_task(task)  # 落库：pending 状态

            thread = threading.Thread(target=self._run_task, args=(task,), daemon=True)
            thread.start()
            return task_id

    def cancel(self, task_id: str):
        """取消运行中的任务。当前批次完成后停止。"""
        with self._lock:
            if not self._active_task or self._active_task.task_id != task_id:
                raise ValueError("任务不存在或已完成")
            if self._active_task.status != "running":
                raise ValueError("任务不在运行中，无法取消")
            self._active_task._stop_requested = True
            self._update_task_db(self._active_task, status="cancelling")

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
            elif task.task_type == "indicator":
                self._run_indicator_backfill(task)
        except Exception as e:
            logger.error(f"[Backfill] 任务 {task.task_id} 异常: {e}")
            task.status = "failed"
            task.error_message = str(e)[:500]
        finally:
            if task.status == "running":
                task.status = "completed"  # 正常结束
            task.completed_at = datetime.now().isoformat()
            task.updated_at = task.completed_at
            self._persist_task(task)  # 落库：最终状态
            self._wake_ws()

            with self._lock:
                if self._active_task is task:
                    self._active_task = None

    # ═══════════════════════════════════════════════
    #  K线补数（个股/指数/ETF 通用）
    # ═══════════════════════════════════════════════

    def _run_kline_backfill(self, task: BackfillTask):
        """K线补数 — 含单日增量 / 跨日期批量策略分发。"""
        # ── 初始化适配器 ──
        manager = get_data_source_manager()
        try:
            adapter = manager.get_source()
        except RuntimeError as e:
            task.status = "failed"
            task.error_message = f"所有数据源均不可用: {e}"
            return

        stock_type_map = {"kline": "stock", "index": "index", "etf": "etf"}
        stock_type = stock_type_map[task.task_type]

        try:
            adapter._ensure_login()
            stock_list = adapter.get_stock_list(stock_type)
        except Exception as e:
            task.status = "failed"
            task.error_message = f"获取股票列表失败: {e}"
            return

        all_codes = [s.stock_code for s in stock_list]
        if not all_codes:
            task.status = "failed"
            task.error_message = f"未找到任何{LABEL_MAP[task.task_type]}代码"
            return

        # ── 确定表、取数函数、写入函数 ──
        is_single_day = (task.start_date == task.end_date)

        if task.task_type == "index":
            table = "index_daily_quote"
            code_col = "index_code"
            fetch_fn = adapter.fetch_index_kline
            write_fn = writers.batch_upsert_index_kline
        else:
            table = "daily_quote"
            code_col = "stock_code"
            write_fn = writers.batch_upsert_kline
            fetch_fn = adapter.fetch_etf_kline if task.task_type == "etf" else adapter.fetch_stock_kline

        db = get_sync_db()

        # ── 断点续传：确定跳过集 ──
        if task.force:
            skip_set: Set[str] = set()
        elif is_single_day:
            rows = db.execute(text(
                f"SELECT DISTINCT {code_col} FROM {table} WHERE trade_date = :d"
            ), {"d": task.start_date}).fetchall()
            skip_set = {r[0] for r in rows}
        else:
            rows = db.execute(text(
                f"SELECT DISTINCT {code_col} FROM {table} "
                f"WHERE trade_date BETWEEN :s AND :e"
            ), {"s": task.start_date, "e": task.end_date}).fetchall()
            skip_set = {r[0] for r in rows}

        remaining = [c for c in all_codes if c not in skip_set]
        task.stocks_total = len(remaining)
        task.total_batches = max((len(remaining) + BATCH_SIZE - 1) // BATCH_SIZE, 1)

        if not remaining:
            task.status = "completed"
            task.error_message = "数据已完整，无需补数"
            db.close()
            adapter._logout()
            return

        # ── IPO 日期映射（停牌检测用）──
        ipo_map = self._load_ipo_map(db, stock_type)

        # ── 会话刷新：get_stock_list 后重置连接，防止 baostock TCP 连接退化 ──
        try:
            adapter._logout()
        except Exception:
            pass
        time.sleep(2)
        try:
            adapter._ensure_login()
        except Exception as e:
            task.status = "failed"
            task.error_message = f"数据源重连失败: {e}"
            db.close()
            return

        # ── 分批执行 ──
        for batch_idx in range(0, len(remaining), BATCH_SIZE):
            if task._stop_requested:
                task.status = "cancelled"
                db.close()
                adapter._logout()
                return

            batch = remaining[batch_idx:batch_idx + BATCH_SIZE]
            task.current_batch = batch_idx // BATCH_SIZE + 1
            logger.info("[Backfill] {} 批次 {}/{}: {} 只开始拉取",
                        task.task_type, task.current_batch, task.total_batches, len(batch))

            try:
                rows = fetch_fn(batch, task.start_date, task.end_date)
                saved = write_fn(db, rows) if rows else 0
                task.rows += saved
                logger.info("[Backfill] {} 批次 {}/{}: {} rows → 写入 {} 行",
                            task.task_type, task.current_batch, task.total_batches,
                            len(rows) if rows else 0, saved)

                # 停牌检测：返回 0 行但已上市 → 写入停牌标记
                if rows:
                    fetched = self._extract_codes(rows, task.task_type)
                else:
                    fetched = set()
                for code in batch:
                    if code not in fetched:
                        ipo = ipo_map.get(code)
                        if ipo and ipo <= (task.end_date or task.start_date):
                            self._write_suspension_marker(
                                db, table, code_col, code,
                                task.task_type, task.end_date or task.start_date
                            )

                task.stocks_done += len(batch)

            except Exception as e:
                logger.error(f"[Backfill] {task.task_type} 批次 {task.current_batch} 失败: {e}")
                task.errors += len(batch)
                task.failed_codes.extend(batch[:5])

            task.updated_at = datetime.now().isoformat()
            self._update_task_db(task)  # 每批完成后落库进度
            self._wake_ws()

            # 批次间会话维护（baostock 连接退化防护）
            if batch_idx + BATCH_SIZE < len(remaining):
                try:
                    adapter._logout()
                except Exception:
                    pass
                time.sleep(2)
                try:
                    if not adapter._login():
                        task.status = "failed"
                        task.error_message = "数据源重连失败，请稍后重试"
                        db.close()
                        return
                except Exception as e:
                    task.status = "failed"
                    task.error_message = f"数据源重连异常: {e}"
                    db.close()
                    return

        task.status = "completed"
        try:
            adapter._logout()
        except Exception:
            pass
        db.close()

    # ═══════════════════════════════════════════════
    #  基本面补数
    # ═══════════════════════════════════════════════

    def _run_fund_backfill(self, task: BackfillTask):
        """基本面补数。无日期范围概念，即时快照。"""
        manager = get_data_source_manager()
        try:
            adapter = manager.get_source()
        except RuntimeError as e:
            task.status = "failed"
            task.error_message = f"所有数据源均不可用: {e}"
            return

        try:
            adapter._ensure_login()
            stock_list = adapter.get_stock_list("stock")
        except Exception as e:
            task.status = "failed"
            task.error_message = f"获取股票列表失败: {e}"
            return

        all_codes = [s.stock_code for s in stock_list]
        db = get_sync_db()

        if not task.force:
            rows = db.execute(text(
                "SELECT DISTINCT stock_code FROM stock_fundamentals"
            )).fetchall()
            skip_set = {r[0] for r in rows}
        else:
            skip_set = set()

        remaining = [c for c in all_codes if c not in skip_set]
        task.stocks_total = len(remaining)
        task.total_batches = max((len(remaining) + BATCH_SIZE - 1) // BATCH_SIZE, 1)

        if not remaining:
            task.status = "completed"
            task.error_message = "基本面数据已完整，无需补数"
            db.close()
            adapter._logout()
            return

        # ── 会话刷新 ──
        try:
            adapter._logout()
        except Exception:
            pass
        time.sleep(2)
        try:
            adapter._ensure_login()
        except Exception as e:
            task.status = "failed"
            task.error_message = f"数据源重连失败: {e}"
            db.close()
            return

        for batch_idx in range(0, len(remaining), BATCH_SIZE):
            if task._stop_requested:
                task.status = "cancelled"
                db.close()
                adapter._logout()
                return

            batch = remaining[batch_idx:batch_idx + BATCH_SIZE]
            task.current_batch = batch_idx // BATCH_SIZE + 1
            logger.info("[Backfill] fund 批次 {}/{}: {} 只开始拉取",
                        task.current_batch, task.total_batches, len(batch))

            try:
                fund_rows = adapter.fetch_fundamentals(batch)
                saved = writers.batch_upsert_fundamentals(db, fund_rows) if fund_rows else 0
                task.rows += saved
                task.stocks_done += len(batch)
            except Exception as e:
                logger.error(f"[Backfill] 基本面批次 {task.current_batch} 失败: {e}")
                task.errors += len(batch)
                task.failed_codes.extend(batch[:5])

            task.updated_at = datetime.now().isoformat()
            self._wake_ws()

            # 批次间会话维护
            if batch_idx + BATCH_SIZE < len(remaining):
                try:
                    adapter._logout()
                except Exception:
                    pass
                time.sleep(2)
                try:
                    if not adapter._login():
                        task.status = "failed"
                        task.error_message = "数据源重连失败"
                        db.close()
                        return
                except Exception as e:
                    task.status = "failed"
                    task.error_message = f"数据源重连异常: {e}"
                    db.close()
                    return

        task.status = "completed"
        try:
            adapter._logout()
        except Exception:
            pass
        db.close()

    # ═══════════════════════════════════════════════
    #  基础指标加工补数
    # ═══════════════════════════════════════════════

    def _run_indicator_backfill(self, task: BackfillTask):
        """基础指标加工补数。依赖 daily_quote 已有数据。"""
        db = get_sync_db()

        # 前置检查：daily_quote 是否有数据
        kline_count = db.execute(text(
            "SELECT COUNT(*) FROM daily_quote"
        )).fetchone()[0]
        if kline_count == 0:
            task.status = "failed"
            task.error_message = "daily_quote 无数据，请先补全日K线"
            db.close()
            return

        from scripts.pipeline import dag_task_indicator_full

        try:
            dag_task_indicator_full(
                trade_date=task.end_date,
                start_date=task.start_date,
                force=task.force,
            )
            task.stocks_done = 1  # indicator runs in bulk
            task.status = "completed"
        except Exception as e:
            logger.error(f"[Backfill] 指标计算失败: {e}")
            task.status = "failed"
            task.error_message = str(e)[:500]

        task.updated_at = datetime.now().isoformat()
        self._wake_ws()
        db.close()

    # ═══════════════════════════════════════════════
    #  工具函数
    # ═══════════════════════════════════════════════

    @staticmethod
    def _load_ipo_map(db, stock_type: str) -> Dict[str, str]:
        """加载股票 IPO 日期映射。"""
        rows = db.execute(text(
            "SELECT stock_code, ipo_date FROM stock_master WHERE stock_type = :t AND ipo_date IS NOT NULL"
        ), {"t": stock_type}).fetchall()
        return {r[0]: str(r[1]) for r in rows if r[1]}

    @staticmethod
    def _extract_codes(rows: list, task_type: str) -> Set[str]:
        """从适配器返回的行列表中提取股票代码集合。"""
        codes = set()
        for r in rows:
            if task_type == "index":
                codes.add(r.index_code if hasattr(r, 'index_code') else '')
            else:
                codes.add(r.stock_code if hasattr(r, 'stock_code') else '')
        codes.discard('')
        return codes

    @staticmethod
    def _write_suspension_marker(db, table: str, code_col: str, code: str,
                                  task_type: str, marker_date: str):
        """写入停牌标记行（is_suspended=true）。

        对单日补数：标记该日期。
        对跨日期补数：在 end_date 写入一条标记，使断点续传跳过该股票。
        """
        if task_type == "index":
            sql = (
                f"INSERT INTO {table} (trade_date, {code_col}, index_name, "
                "open, high, low, close, volume, amount, is_suspended) "
                "VALUES (:d, :c, '', 0, 0, 0, 0, 0, 0, true) "
                f"ON CONFLICT (trade_date, {code_col}) DO UPDATE SET is_suspended = true"
            )
        else:
            exchange = "SSE" if code.startswith("6") else "SZSE"
            sql = (
                f"INSERT INTO {table} (trade_date, exchange, {code_col}, stock_name, "
                "open, high, low, close, close_hfq, close_qfq, volume, amount, is_suspended) "
                "VALUES (:d, :ex, :c, '', 0, 0, 0, 0, 0, 0, 0, 0, true) "
                f"ON CONFLICT ({code_col}, exchange, trade_date) DO UPDATE SET is_suspended = true"
            )
        try:
            db.execute(text(sql), {"d": marker_date, "c": code,
                                    "ex": exchange if task_type != "index" else ""})
            db.commit()
        except Exception as e:
            logger.warning(f"[Backfill] 停牌标记写入失败 {code}: {e}")

    # ═══════════════════════════════════════════════
    #  DB 持久化
    # ═══════════════════════════════════════════════

    @staticmethod
    def _get_db():
        """获取 DB 连接（用于任务持久化，与数据 DB 共用）。"""
        try:
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
            sets = ["current_batch=:cb", "stocks_done=:sdo", "rows=:r", "errors=:e"]
            params = {"tid": task.task_id, "cb": task.current_batch,
                      "sdo": task.stocks_done, "r": task.rows, "e": task.errors}
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
        """启动时将上次异常终止的 'running' 任务标记为 'failed'。"""
        db = self._get_db()
        if db is None:
            return
        try:
            result = db.execute(text(
                "UPDATE backfill_tasks SET status='failed', error_message='服务重启，任务中断', completed_at=CURRENT_TIMESTAMP WHERE status='running'"
            ))
            db.commit()
            if result.rowcount and result.rowcount > 0:
                logger.info(f"[Backfill] 恢复: {result.rowcount} 个孤儿任务标记为 failed")
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
