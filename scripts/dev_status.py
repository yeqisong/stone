#!/usr/bin/env python
"""开发环境状态总览：工作树 / 分支 / 端口 / 测试库。

用法：
    venv/bin/python scripts/dev_status.py

只读，不创建/删除任何东西；用于每次开工前确认没有旧任务残留。
"""
from __future__ import annotations

import socket
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import settings  # noqa: E402
PORTS = [3000, 8000, *range(8100, 8105)]


def _git(*args: str) -> str:
    try:
        return subprocess.check_output(["git", "-C", str(ROOT), *args],
                                       text=True, stderr=subprocess.DEVNULL).strip()
    except Exception as e:
        return f"<git 失败: {e}>"


def _port_open(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.15)
        return s.connect_ex(("127.0.0.1", port)) == 0


def _databases() -> list[tuple[str, str]]:
    try:
        import psycopg2
        c = psycopg2.connect(settings.DATABASE_URL_SYNC)
        try:
            cur = c.cursor()
            cur.execute("""
                SELECT datname, pg_size_pretty(pg_database_size(datname))
                FROM pg_database
                WHERE datname LIKE 'stone%' OR datname LIKE 'stock_monitor%'
                ORDER BY datname
            """)
            return cur.fetchall()
        finally:
            c.close()
    except Exception as e:
        return [("<数据库不可用>", str(e).splitlines()[0][:80])]


def main() -> None:
    print("== 仓库")
    print(f"  path: {ROOT}")
    print(f"  branch: {_git('branch', '--show-current')}")
    print(f"  status: {_git('status', '--short') or 'clean'}")

    print("\n== 工作树")
    print(_git("worktree", "list"))

    merged = {x.lstrip("* ").strip() for x in _git("branch", "--merged", "main").splitlines()}
    all_branches = {x.lstrip("* ").strip() for x in _git("branch").splitlines()}
    unmerged = sorted(all_branches - merged - {"main"})
    print(f"\n== 分支：merged={len(merged)} unmerged={len(unmerged)}")
    if unmerged:
        for b in unmerged:
            print(f"  ! {b}")
    else:
        print("  无未合并分支")

    print("\n== 端口")
    for p in PORTS:
        print(f"  {p}: {'LISTEN' if _port_open(p) else 'free'}")

    print("\n== 数据库")
    for name, size in _databases():
        print(f"  {name:28s} {size}")

    print("\n提示：任务环境约定 3100/8100、3101/8101……；主仓库只跑 main + 3000/8000。")


if __name__ == "__main__":
    main()
