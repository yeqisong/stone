"""共享信号模块 — DAG 调度器与 WS-DAG 服务之间的解耦桥梁。

两个模块都依赖本模块，彼此不直接 import：
  scripts/dag.py  ──→  app/signal.py  ←──  app/api/status.py
  scripts/pipeline.py ──→  app/signal.py

提供：
- _dag_wake_event: asyncio.Event，DAG 写入 pending 后 set，WS 的 broadcast loop 被唤醒
- _stop_requests: set[str]，存储用户请求终止的 run_id
- set_main_loop / wake_dag_broadcast: 线程安全的唤醒接口
"""
import asyncio
import threading

_dag_wake_event = asyncio.Event()
_stop_requests: set = set()
_stop_events: dict = {}  # run_id → threading.Event
_main_loop = None


def request_stop(run_id: str):
    """标记 run_id 为需终止（API 层调用）。"""
    _stop_requests.add(run_id)
    # 触发 threading.Event，让阻塞中的训练线程感知到
    evt = _stop_events.get(run_id)
    if evt:
        evt.set()


def set_stop_event(run_id: str, event: threading.Event):
    """存储 run_id 对应的终止事件（DAG 创建 run 时调用）。"""
    _stop_events[run_id] = event


def get_stop_event(run_id: str) -> threading.Event:
    """为指定 run_id 创建或获取终止事件（DAG 执行器调用）。"""
    if run_id not in _stop_events:
        _stop_events[run_id] = threading.Event()
    return _stop_events[run_id]


def is_stop_requested(run_id: str) -> bool:
    """检查 run_id 是否已被标记终止（DAG 执行器/心跳线程调用）。"""
    return run_id in _stop_requests


def clear_stop_request(run_id: str):
    """清除终止标记（心跳线程终止完成后调用）。"""
    _stop_requests.discard(run_id)


def clear_stop_events(run_id: str):
    """清除 run_id 对应的所有终止相关资源（DAG 执行完成后调用，防止内存泄漏）。"""
    _stop_requests.discard(run_id)
    _stop_events.pop(run_id, None)


def set_main_loop(loop):
    """由 main.py 在 lifespan 中调用，保存主事件循环引用。"""
    global _main_loop
    _main_loop = loop


def wake_dag_broadcast():
    """线程安全地唤醒 broadcast_dag_status 的 asyncio 等待。

    DAG 调度器在后台线程中调用此函数，因此需要 call_soon_threadsafe。
    """
    if _main_loop and _main_loop.is_running():
        _main_loop.call_soon_threadsafe(_dag_wake_event.set)
    else:
        _dag_wake_event.set()
