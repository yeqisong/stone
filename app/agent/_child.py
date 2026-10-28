"""沙箱子进程启动器——在 exec 目标程序前设置资源上限。

为什么多这一个文件：preexec_fn 在多线程进程（uvicorn）里 fork 后执行 Python 代码
不安全，实测会导致子进程 SIGSEGV；改为 start_new_session + 本启动器在子进程内
（已是独立进程）设置 rlimit 再 exec，既保留内存/CPU 上限又不碰 fork 期状态。
用法：python _child.py <python|node> <target> [args...]
"""
import os
import resource
import sys

MEM_LIMIT = 2 * 1024 ** 3   # 2GB 虚拟内存
CPU_LIMIT = 180             # 秒


def main():
    if len(sys.argv) < 3:
        raise SystemExit("usage: _child.py <python|node> <target> [args...]")
    kind, target = sys.argv[1], sys.argv[2]
    args = sys.argv[3:]
    try:
        resource.setrlimit(resource.RLIMIT_AS, (MEM_LIMIT, MEM_LIMIT))
        resource.setrlimit(resource.RLIMIT_CPU, (CPU_LIMIT, CPU_LIMIT))
    except Exception:
        pass
    if kind == "python":
        os.execv(sys.executable, [sys.executable, target, *args])
    else:
        os.execvp(target, [target, *args])


if __name__ == "__main__":
    main()
