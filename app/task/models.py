"""统一任务数据模型。"""
from dataclasses import dataclass, field
from typing import Optional, List
from datetime import datetime
import uuid


def _ts():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


@dataclass
class TaskNode:
    """任务的单个子节点。"""
    node_name: str           # 节点名（如 "kline"）
    run_id: str              # 节点级别子 run_id
    status: str = "pending"  # pending → running → success / failed
    log_id: int = 0          # dag_run_log 表主键
    progress_pct: int = 0    # 内部进度 0-100
    rows: int = 0
    detail: str = ""
    error: Optional[str] = None
    started_at: Optional[str] = None
    finished_at: Optional[str] = None

    def to_dict(self):
        return {
            "node_name": self.node_name,
            "status": self.status,
            "progress_pct": self.progress_pct,
            "rows": self.rows,
            "detail": self.detail,
            "error": self.error,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }


@dataclass
class Task:
    """统一任务。"""
    task_id: str = field(default_factory=lambda: f"task-{uuid.uuid4().hex[:8]}")
    task_type: str = "dag_flow"   # dag_flow | backfill | feature_compute
    flow_id: Optional[int] = None
    flow_name: Optional[str] = None
    status: str = "pending"       # pending → running → completed / failed
    progress_pct: int = 0
    current_detail: str = ""
    nodes: List[TaskNode] = field(default_factory=list)
    error: Optional[str] = None
    created_at: str = field(default_factory=_ts)
    started_at: Optional[str] = None
    finished_at: Optional[str] = None

    def to_dict(self):
        return {
            "task_id": self.task_id,
            "task_type": self.task_type,
            "flow_id": self.flow_id,
            "flow_name": self.flow_name,
            "status": self.status,
            "progress_pct": self.progress_pct,
            "current_detail": self.current_detail,
            "nodes": [n.to_dict() for n in self.nodes],
            "error": self.error,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }
