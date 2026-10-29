"""LLM 调用的进程内实时指标——并发、速率、错误码、时延。

为什么进程内而不是落库：所有 GLM 调用都发生在同一个 uvicorn 进程（Agent/新闻总结/
雪球打分 DAG 节点/连通性测试），内存计数零开销且实时；每次调用写库反而会污染业务表。
CLI 单独起进程的调用不计入（内部标注说明）。

取用：状态页「大模型配置」卡片的实时指标行，接口 GET /api/llm_config/metrics。
"""
import threading
import time
from collections import deque
from contextlib import contextmanager

_lock = threading.Lock()
_active = 0                  # 当前在途请求数（真实并发）
_peak = 0                    # 进程启动以来峰值并发
_total = 0
_ok = 0
_fail = 0
_lat = deque(maxlen=200)     # 最近 200 次耗时（ms）
_stamps = deque()            # 最近 5 分钟的调用时间戳
_errors = deque(maxlen=20)   # (ts, code, msg)
_by_model = {}               # model -> {'total','ok','fail','active'}
_last_call_at = None
_started_at = time.time()


def _trim(now: float):
    while _stamps and now - _stamps[0] > 300:
        _stamps.popleft()


def classify_error(e: Exception) -> tuple:
    """把 OpenAI/HTTP 异常压成 (code, 短消息)，用于判断是限速还是鉴权/余额问题。"""
    code = ""
    body = getattr(e, "body", None)
    if isinstance(body, dict):
        err = body.get("error") or {}
        if isinstance(err, dict):
            code = str(err.get("code") or "")
    if not code:
        sc = getattr(e, "status_code", None)
        code = f"HTTP {sc}" if sc else type(e).__name__
    msg = str(getattr(e, "message", None) or e)[:160]
    return code, msg


@contextmanager
def track(model: str = ""):
    """包住一次 LLM 请求：进出各记一次，异常也记（含错误码）。"""
    global _active, _peak, _total, _ok, _fail, _last_call_at
    t0 = time.time()
    with _lock:
        _active += 1
        _total += 1
        _peak = max(_peak, _active)
        _stamps.append(t0)
        _trim(t0)
        _last_call_at = t0
        st = _by_model.setdefault(model or "-", {"total": 0, "ok": 0, "fail": 0, "active": 0})
        st["total"] += 1
        st["active"] += 1
    ok = False
    err = None
    try:
        yield
        ok = True
    except Exception as e:
        err = classify_error(e)
        raise
    finally:
        dt_ms = (time.time() - t0) * 1000
        with _lock:
            _active = max(0, _active - 1)
            if ok:
                _ok += 1
            else:
                _fail += 1
            _lat.append(dt_ms)
            st = _by_model.setdefault(model or "-", {"total": 0, "ok": 0, "fail": 0, "active": 0})
            st["active"] = max(0, st["active"] - 1)
            if ok:
                st["ok"] += 1
            else:
                st["fail"] += 1
            if err:
                _errors.append((time.strftime("%H:%M:%S"), err[0], err[1]))


def _pct(vals, q):
    if not vals:
        return None
    s = sorted(vals)
    idx = min(len(s) - 1, max(0, int(round((len(s) - 1) * q))))
    return round(s[idx], 1)


def snapshot() -> dict:
    """当前指标快照（含与配置上限的对照）。"""
    with _lock:
        now = time.time()
        _trim(now)
        rpm_1m = sum(1 for t in _stamps if now - t <= 60)
        rpm_5m = round(len(_stamps) / 5.0, 1) if _stamps else 0
        lat = list(_lat)
        errs = list(_errors)[-5:]
        data = {
            "active": _active,
            "peak": _peak,
            "total": _total,
            "ok": _ok,
            "fail": _fail,
            "rpm_1m": rpm_1m,
            "rpm_5m_avg": rpm_5m,
            "avg_ms": round(sum(lat) / len(lat), 1) if lat else None,
            "p95_ms": _pct(lat, 0.95),
            "last_call_at": (time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(_last_call_at))
                             if _last_call_at else None),
            "uptime_s": int(now - _started_at),
            "by_model": {k: dict(v) for k, v in _by_model.items()},
            "recent_errors": [{"at": a, "code": c, "msg": m} for a, c, m in errs],
        }
    # 配置上限（帮助判断是否已顶到 RPM/并发）
    try:
        from app.llm_runtime import get_llm_settings
        cfg = get_llm_settings()
        data["config"] = {"rpm": cfg["rpm"], "workers": cfg["workers"], "model": cfg["model"]}
        data["rpm_pct"] = round(rpm_1m * 100.0 / cfg["rpm"], 1) if cfg["rpm"] else None
        data["saturated"] = bool(cfg["workers"] and _active >= cfg["workers"])
    except Exception:
        data["config"] = {}
    return data


def reset():
    """清空指标（重启进程等价；测试与手工重置用）。"""
    global _active, _peak, _total, _ok, _fail, _last_call_at, _started_at
    with _lock:
        _active = _peak = _total = _ok = _fail = 0
        _lat.clear()
        _stamps.clear()
        _errors.clear()
        _by_model.clear()
        _last_call_at = None
        _started_at = time.time()
