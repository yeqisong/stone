"""Agent 会话沙箱——每会话独立工作区，Python/Node 代码执行与依赖安装。

设计取舍（单人本地系统）：
- 会话工作区 data/agent_ws/<session_id>/，代码、数据、依赖都落在里面，会话之间互不干扰；
- Python 依赖装到 <ws>/pylibs（PYTHONPATH 注入），Node 依赖装到 <ws>/node（NODE_PATH 注入）——
  不动系统环境，删会话目录即彻底清理；
- 执行有超时 + 内存上限 + 输出截断；超时/停止杀整个进程组（防子进程残留）；
- 代码执行优先用 bubblewrap（--unshare-all 无网络、根只读、仅工作区可写）；
  bwrap 不可用时退回普通子进程（仍受 rlimit 约束）。pip/npm 需要网络，不走 bwrap。
"""
import os
import re
import shutil
import signal
import subprocess
import threading
import time
from pathlib import Path

from loguru import logger

ROOT = Path(__file__).resolve().parents[2]
WS_ROOT = ROOT / "data" / "agent_ws"

MAX_OUTPUT = 12000          # 单次执行返回给模型的输出上限（字符）
DEFAULT_TIMEOUT = 60        # 秒
MEM_LIMIT = 2 * 1024 ** 3   # 2GB 虚拟内存上限（Python 子进程）
MAX_UPLOAD = 20 * 1024 ** 2  # 单文件上传上限 20MB
_PY = str(ROOT / "venv" / "bin" / "python")
_BWRAP = shutil.which("bwrap")
_SANDBOX_MODE = os.environ.get("AGENT_SANDBOX", "auto")  # auto / bwrap / none

# 运行中的子进程组（会话 → {pid}），用于「停止」时立刻杀进程
_PROCS: dict = {}
_PROC_GUARD = threading.Lock()


def workspace(session_id: int) -> Path:
    ws = WS_ROOT / str(session_id)
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "pylibs").mkdir(exist_ok=True)
    (ws / "node").mkdir(exist_ok=True)
    (ws / "uploads").mkdir(exist_ok=True)
    return ws


_NAME_RE = re.compile(r"[^A-Za-z0-9._\-\u4e00-\u9fff]+")


def save_upload(session_id: int, filename: str, data: bytes) -> dict:
    """把用户上传文件落到 <ws>/uploads/，文件名去危险字符并防重名覆盖。"""
    if not data:
        return {"error": "文件为空"}
    if len(data) > MAX_UPLOAD:
        return {"error": f"文件超过 {MAX_UPLOAD // 1024 // 1024}MB 上限"}
    raw = Path(filename or "upload.bin").name
    safe = _NAME_RE.sub("_", raw).strip("._") or "upload.bin"
    if len(safe) > 120:
        suffix = Path(safe).suffix[:16]
        safe = safe[:100] + suffix
    ws = workspace(session_id)
    d = ws / "uploads"
    target = d / safe
    if target.exists():
        target = d / f"{Path(safe).stem}-{int(time.time())}{Path(safe).suffix}"
    target.write_bytes(data)
    rel = f"uploads/{target.name}"
    return {"ok": True, "name": target.name, "path": rel, "size": len(data)}


def _env(ws: Path) -> dict:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ws / "pylibs") + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    env["NODE_PATH"] = str(ws / "node" / "node_modules")
    env["PATH"] = str(ws / "node" / "node_modules" / ".bin") + os.pathsep + env.get("PATH", "")
    env["HOME"] = str(ws)          # 沙箱内脚本的临时/缓存目录也落会话工作区
    env["MPLBACKEND"] = "Agg"      # 无显示环境，避免 matplotlib 报错
    env["PYTHONIOENCODING"] = "utf-8"
    return env


_CHILD = str(Path(__file__).resolve().parent / "_child.py")


def sandbox_enabled() -> bool:
    """代码执行是否走 bubblewrap（pip/npm 永远不走）。"""
    if _SANDBOX_MODE == "none" or not _BWRAP:
        return False
    return True


def _wrap_cmd(cmd: list, ws: Path) -> list:
    """用 bwrap 包一层：根只读、网络隔离、仅会话工作区可写。"""
    if not sandbox_enabled():
        return cmd
    return [
        _BWRAP,
        "--ro-bind", "/", "/",
        "--dev", "/dev",
        "--proc", "/proc",
        "--tmpfs", "/tmp",
        "--bind", str(ws), str(ws),
        "--chdir", str(ws),
        "--unshare-all",          # 独立 user/net/pid/ipc/uts/cgroup 命名空间
        "--die-with-parent",
        "--new-session",
        *cmd,
    ]


def terminate_session(session_id: int) -> int:
    """杀掉该会话所有在跑的子进程组，返回击杀数（停止按钮的底层动作）。"""
    with _PROC_GUARD:
        pids = list(_PROCS.get(session_id) or ())
    killed = 0
    for pid in pids:
        try:
            os.killpg(os.getpgid(pid), signal.SIGKILL)
            killed += 1
        except Exception:
            try:
                os.kill(pid, signal.SIGKILL)
                killed += 1
            except Exception:
                pass
    if killed:
        logger.info(f"[agent] 会话 {session_id} 已终止 {killed} 个沙箱进程")
    return killed


def _run(cmd: list, ws: Path, timeout: int, kind: str = "python",
         session_id: int = 0, use_sandbox: bool = True) -> dict:
    """执行子进程。经 _child.py 启动器设 rlimit（preexec_fn 在多线程 uvicorn 下
    fork 会 SIGSEGV，实测 exit_code=-11；start_new_session 可安全使用 posix_spawn）。"""
    t0 = time.time()
    full = [_PY, _CHILD, kind, *cmd]
    if use_sandbox:
        full = _wrap_cmd(full, ws)
    try:
        p = subprocess.Popen(
            full, cwd=str(ws), env=_env(ws),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            start_new_session=True,
        )
        if session_id:
            with _PROC_GUARD:
                _PROCS.setdefault(session_id, set()).add(p.pid)
        try:
            out, err = p.communicate(timeout=timeout)
            timed_out = False
        except subprocess.TimeoutExpired:
            timed_out = True
            try:
                os.killpg(os.getpgid(p.pid), signal.SIGKILL)
            except Exception:
                p.kill()
            out, err = p.communicate()
        finally:
            if session_id:
                with _PROC_GUARD:
                    _PROCS.get(session_id, set()).discard(p.pid)
    except Exception as e:
        return {"ok": False, "stdout": "", "stderr": f"启动失败: {e}", "elapsed_ms": int((time.time() - t0) * 1000)}

    def _cut(b):
        s = (b or b"").decode("utf-8", "replace")
        return s if len(s) <= MAX_OUTPUT else s[:MAX_OUTPUT] + f"\n…（输出已截断，共 {len(s)} 字符）"

    stdout, stderr = _cut(out), _cut(err)
    ok = (p.returncode == 0) and not timed_out
    if timed_out:
        stderr = (stderr + f"\n执行超时（>{timeout}s）已终止").strip()
    return {"ok": ok, "stdout": stdout, "stderr": stderr, "exit_code": p.returncode,
            "elapsed_ms": int((time.time() - t0) * 1000), "timed_out": timed_out}


def run_python(session_id: int, code: str, timeout: int = DEFAULT_TIMEOUT) -> dict:
    ws = workspace(session_id)
    (ws / "main.py").write_text(code, encoding="utf-8")
    r = _run(["main.py"], ws, timeout, kind="python", session_id=session_id)
    r["cwd"] = str(ws)
    r["sandboxed"] = sandbox_enabled()
    return r


def run_node(session_id: int, code: str, timeout: int = DEFAULT_TIMEOUT) -> dict:
    ws = workspace(session_id)
    (ws / "main.mjs").write_text(code, encoding="utf-8")
    node = shutil.which("node") or "/usr/bin/node"
    r = _run([node, "main.mjs"], ws, timeout, kind="node", session_id=session_id)
    r["cwd"] = str(ws)
    r["sandboxed"] = sandbox_enabled()
    return r


def pip_install(session_id: int, packages: list, timeout: int = 180) -> dict:
    pkgs = [p for p in (packages or []) if p and len(p) < 80]
    if not pkgs:
        return {"ok": False, "stdout": "", "stderr": "未指定包名"}
    ws = workspace(session_id)
    cmd = ["-m", "pip", "install", "--quiet", "--disable-pip-version-check",
           "--target", str(ws / "pylibs"), *pkgs]
    return _run(cmd, ws, timeout, kind="python", session_id=session_id, use_sandbox=False)


def npm_install(session_id: int, packages: list, timeout: int = 240) -> dict:
    pkgs = [p for p in (packages or []) if p and len(p) < 80]
    if not pkgs:
        return {"ok": False, "stdout": "", "stderr": "未指定包名"}
    ws = workspace(session_id)
    npm = shutil.which("npm") or "/usr/bin/npm"
    if not os.path.exists(npm):
        return {"ok": False, "stdout": "", "stderr": "npm 不可用"}
    return _run([npm, "install", "--silent", "--no-audit", "--no-fund",
                 "--prefix", str(ws / "node"), *pkgs], ws, timeout, kind="node",
                session_id=session_id, use_sandbox=False)


def list_workspace(session_id: int) -> dict:
    """列会话工作区文件（给模型和自己排查用）。"""
    ws = workspace(session_id)
    items = []
    for p in sorted(ws.iterdir()):
        if p.name in ("pylibs", "node"):
            continue
        try:
            items.append({"name": p.name, "size": p.stat().st_size if p.is_file() else None,
                          "is_dir": p.is_dir()})
        except Exception:
            pass
    return {"cwd": str(ws), "files": items[:100]}


def reset_workspace(session_id: int):
    ws = WS_ROOT / str(session_id)
    if ws.exists():
        shutil.rmtree(ws, ignore_errors=True)
        logger.info(f"[agent] 已重置会话 {session_id} 工作区")
