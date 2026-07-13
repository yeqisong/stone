"""统一任务管理器 — 单例，管理所有任务生命周期与并发控制。"""
import threading
import time
from typing import Dict, List, Optional
from loguru import logger
from app.task.models import Task, TaskNode, _ts


class TaskManager:
    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._tasks: Dict[str, Task] = {}
                    cls._instance._task_lock = threading.Lock()
                    cls._instance._running_count = 0
                    cls._instance._flow_locks: Dict[int, bool] = {}
                    cls._instance._cleanup_stale_tasks()
        return cls._instance

    def _cleanup_stale_tasks(self):
        """启动时清理数据库中僵尸 running 状态（超 5 分钟无心跳）。"""
        try:
            from app.db.connection import get_sync_db
            from sqlalchemy import text
            from datetime import datetime, timedelta
            db = get_sync_db()
            cutoff = (datetime.now() - timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M:%S")
            db.execute(text("""
                UPDATE dag_run_log SET status='failed',
                    detail='服务器重启，任务中断',
                    finished_at=CURRENT_TIMESTAMP
                WHERE status='running' AND (heartbeat_at IS NULL OR heartbeat_at < :cutoff)
            """), {"cutoff": cutoff})
            db.commit()
            db.close()
        except Exception:
            pass

    # ── 并发控制 ──
    MAX_CONCURRENT = 5

    def _acquire_flow_lock(self, flow_id: int) -> bool:
        with self._task_lock:
            if self._flow_locks.get(flow_id):
                return False
            self._flow_locks[flow_id] = True
            return True

    def _release_flow_lock(self, flow_id: int):
        with self._task_lock:
            self._flow_locks.pop(flow_id, None)

    def _can_start(self, flow_id: Optional[int] = None) -> Optional[str]:
        with self._task_lock:
            if self._running_count >= self.MAX_CONCURRENT:
                return f"系统繁忙，当前已有 {self.MAX_CONCURRENT} 个任务在运行"
            if flow_id is not None and self._flow_locks.get(flow_id):
                return f"流程 #{flow_id} 已有任务在运行"
        return None

    # ── 任务管理 ──
    def create_task(self, task_type: str = "dag_flow", flow_id: Optional[int] = None,
                    flow_name: Optional[str] = None, nodes: Optional[list] = None) -> Task:
        """创建新任务。返回 Task 对象，调用方需检查 error。"""
        err = self._can_start(flow_id)
        if err:
            task = Task(task_type=task_type, flow_id=flow_id, flow_name=flow_name,
                        status="failed", error=err)
            logger.warning(f"[task] 创建失败: {err}")
            return task

        task = Task(task_type=task_type, flow_id=flow_id, flow_name=flow_name)
        if nodes:
            task.nodes = [TaskNode(node_name=n.get("node_name", f"node-{i}"),
                                    run_id=f"run-{i}")
                          for i, n in enumerate(nodes)]

        with self._task_lock:
            self._tasks[task.task_id] = task
            self._running_count += 1
            if flow_id is not None:
                self._flow_locks[flow_id] = True

        logger.info(f"[task] 创建 {task.task_id} ({flow_name})")
        self._wake()
        return task

    def start_task(self, task_id: str):
        task = self._tasks.get(task_id)
        if task:
            task.status = "running"
            task.started_at = _ts()
            self._wake()

    def complete_task(self, task_id: str):
        task = self._tasks.get(task_id)
        if not task:
            return
        task.status = "completed"
        task.progress_pct = 100
        task.finished_at = _ts()
        self._release_flow_lock(task.flow_id)
        with self._task_lock:
            self._running_count -= 1
        logger.info(f"[task] 完成 {task_id}")
        self._wake()

    def fail_task(self, task_id: str, error: str):
        task = self._tasks.get(task_id)
        if not task:
            return
        task.status = "failed"
        task.error = error
        task.finished_at = _ts()
        self._release_flow_lock(task.flow_id)
        with self._task_lock:
            self._running_count -= 1
        logger.warning(f"[task] 失败 {task_id}: {error}")
        self._wake()

    def update_node(self, task_id: str, node_name: str, **fields):
        """更新节点状态。fields 可包含 status/progress_pct/rows/detail/error 等。"""
        task = self._tasks.get(task_id)
        if not task:
            return
        for n in task.nodes:
            if n.node_name == node_name:
                for k, v in fields.items():
                    if hasattr(n, k) and v is not None:
                        setattr(n, k, v)
                # 自动更新时间
                if fields.get("status") == "running" and not n.started_at:
                    n.started_at = _ts()
                if fields.get("status") in ("success", "failed"):
                    n.finished_at = _ts()
                # 更新任务进度
                done = sum(1 for nd in task.nodes if nd.status in ("success", "failed"))
                task.progress_pct = int(done / len(task.nodes) * 100) if task.nodes else 0
                task.current_detail = f"{done}/{len(task.nodes)} 个节点完成"
                self._wake()
                return

    def get_task(self, task_id: str) -> Optional[Task]:
        return self._tasks.get(task_id)

    def get_active_tasks(self) -> List[Task]:
        """返回 running 任务 + 刚完成的任务（供 WS 广播）。"""
        now = time.time()
        with self._task_lock:
            result = []
            for t in list(self._tasks.values()):
                if t.status == "running":
                    result.append(t)
                elif t.status in ("completed", "failed") and t.finished_at:
                    try:
                        ft = t.finished_at
                        from datetime import datetime
                        ft_dt = datetime.strptime(ft, "%Y-%m-%d %H:%M:%S")
                        if (datetime.now() - ft_dt).total_seconds() < 300:
                            result.append(t)
                    except Exception:
                        pass
            return result

    def get_flow_active_task(self, flow_id: int) -> Optional[Task]:
        """查询某个流程当前是否有活跃任务。"""
        with self._task_lock:
            for t in self._tasks.values():
                if t.flow_id == flow_id and t.status == "running":
                    return t
        return None

    def clean_old_tasks(self):
        """清理完成超过 10 分钟的任务。"""
        now = time.time()
        with self._task_lock:
            stale = []
            for tid, t in list(self._tasks.items()):
                if t.status in ("completed", "failed") and t.finished_at:
                    try:
                        from datetime import datetime
                        ft = datetime.strptime(t.finished_at, "%Y-%m-%d %H:%M:%S")
                        if (datetime.now() - ft).total_seconds() > 600:
                            stale.append(tid)
                    except Exception:
                        stale.append(tid)
            for tid in stale:
                del self._tasks[tid]

    # ── WS 唤醒 ──
    def _wake(self):
        try:
            from app.signal import wake_dag_broadcast
            wake_dag_broadcast()
        except Exception:
            pass
