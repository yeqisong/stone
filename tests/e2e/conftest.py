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
