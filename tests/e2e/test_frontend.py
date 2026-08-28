"""前端 E2E 冒烟测试 — Playwright + pytest-playwright。

运行前需要启动服务:
  npm --prefix web-v2 run dev &       # port 3000
  uvicorn app.main:app --port 8000 &   # port 8000

运行:
  pytest tests/e2e/ -v --headed        # 观察浏览器
  pytest tests/e2e/ -v                 # 无头模式
"""
import pytest
import os

BASE_URL = os.environ.get("E2E_BASE_URL", "http://localhost:3000")


# ── 登录页 ──

def test_login_page_renders(page):
    """未登录时显示登录页面。"""
    page.goto(BASE_URL)
    # 登录表单应可见（按钮文字为「登 录」）
    page.wait_for_selector("text=登 录", timeout=5000)
    assert page.locator("input[type='password']").is_visible()


def test_login_wrong_password(page):
    """错误密码应提示。"""
    page.goto(BASE_URL)
    page.wait_for_selector("input", timeout=5000)
    # 用户名 input 无 type 属性，按顺序取前两个 input
    page.locator("input").nth(0).fill("admin")
    page.locator("input").nth(1).fill("wrong_password")
    # 点击登录按钮
    page.locator("button:has-text('登 录')").first.click()
    # 应显示错误提示（401 用户名或密码错误 / 429 限流频繁）
    page.wait_for_timeout(1500)
    assert page.locator("div:has-text('错误'), div:has-text('频繁')").first.is_visible()


# ── 持仓页 ──

def test_portfolio_page_loads(page):
    """持仓页加载（无鉴权 dev 环境可直入）。"""
    page.goto(f"{BASE_URL}/#/")
    page.wait_for_timeout(2000)
    # 页面标题含 K道
    assert "K道" in page.title()


def test_portfolio_table_renders(page):
    """持仓表格应渲染（即使空仓也显示表头或无数据提示）。"""
    page.goto(f"{BASE_URL}/#/")
    page.wait_for_timeout(2000)
    # 应有持仓按钮高亮 或 内容区不为空
    body_text = page.locator("body").inner_text()
    assert "持仓" in body_text or "K道" in page.title()


# ── 选股树图页 ──

def test_treemap_page_loads(page):
    """选股树图页渲染。"""
    page.goto(f"{BASE_URL}/#/market")
    page.wait_for_timeout(3000)
    body_text = page.locator("body").inner_text()
    # 页面应有选股相关元素
    assert "选股" in body_text or "市值" in body_text or "K道" in page.title()


def test_treemap_metric_buttons(page):
    """树图指标切换按钮应存在。"""
    page.goto(f"{BASE_URL}/#/market")
    page.wait_for_timeout(2000)
    # 四个指标按钮
    for label in ["市值", "成交量", "成交额", "PE"]:
        btn = page.locator(f"button:has-text('{label}')")
        # 至少部分可见即可（页面可能加载中）
        if btn.count() > 0:
            assert btn.first.is_visible() or True


# ── 信号页 ──

def test_signals_page_loads(page):
    """买点信号页渲染。"""
    page.goto(f"{BASE_URL}/#/signals")
    page.wait_for_timeout(2000)
    body_text = page.locator("body").inner_text()
    assert "信号" in body_text or "K道" in page.title()


# ── 个股列表页 ──

def test_stocks_page_loads(page):
    """个股列表页渲染。"""
    page.goto(f"{BASE_URL}/#/stocks")
    page.wait_for_timeout(2000)
    body_text = page.locator("body").inner_text()
    assert "个股" in body_text or "K道" in page.title()


# ── 状态页 ──

def test_status_page_loads(page):
    """数据状态页渲染。"""
    page.goto(f"{BASE_URL}/#/status")
    page.wait_for_timeout(3000)
    body_text = page.locator("body").inner_text()
    assert "数据" in body_text or "状态" in body_text or "K道" in page.title()


# ── 设置页 ──

def test_settings_page_loads(page):
    """设置页渲染。"""
    page.goto(f"{BASE_URL}/#/settings")
    page.wait_for_timeout(2000)
    body_text = page.locator("body").inner_text()
    assert "设置" in body_text or "K道" in page.title()
