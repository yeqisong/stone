"""Playwright E2E fixtures。前端需 Vite dev server (port 3000) + FastAPI (port 8000) 同时运行。"""
import pytest
import os

BASE_URL = os.environ.get("E2E_BASE_URL", "http://localhost:3000")


@pytest.fixture(scope="session")
def browser_context_args(browser_context_args):
    """全局浏览器上下文配置。"""
    return {
        **browser_context_args,
        "viewport": {"width": 1280, "height": 800},
        "locale": "zh-CN",
    }


@pytest.fixture(scope="session")
def browser_type_launch_args(browser_type_launch_args):
    """支持 PLAYWRIGHT_EXECUTABLE 指定浏览器可执行文件（WSL 环境 playwright 装不上新版 chromium 时用）。"""
    exe = os.environ.get("PLAYWRIGHT_EXECUTABLE")
    if not exe:
        return browser_type_launch_args
    return {**browser_type_launch_args, "executable_path": exe, "args": ["--no-sandbox"]}
