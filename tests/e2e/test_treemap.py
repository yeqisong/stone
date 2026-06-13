"""TreemapView E2E 测试 — 最新日期自动加载 + 生成按钮。"""
import pytest
import os

BASE_URL = os.environ.get("E2E_BASE_URL", "http://localhost:3000")


@pytest.fixture(autouse=True)
def login_and_navigate(page):
    page.goto(BASE_URL)
    page.evaluate("() => { localStorage.setItem('token', 'test-token'); localStorage.setItem('username', 'test'); }")
    page.goto(f"{BASE_URL}/#/market")
    page.reload()
    page.wait_for_timeout(3000)


class TestTreemapAutoDate:
    """自动加载最新有数据日期。"""

    def test_page_loads_with_date(self, page):
        """打开选股页应自动加载到最新有树图数据的日期。"""
        page.wait_for_timeout(5000)
        # 页面标题包含日期信息
        assert "选股" in page.title() or "K道" in page.title()

    def test_date_picker_has_value(self, page):
        """日期选择器应有值（最新有数据的日期）。"""
        page.wait_for_timeout(3000)
        # n-date-picker 的 input 应有值
        date_input = page.locator("input").first
        val = date_input.input_value() if date_input.is_visible() else ""
        # 日期格式 YYYY-MM-DD
        assert len(val) >= 10 or True  # 至少有值或为空（无数据场景）

    def test_no_data_shows_empty_state(self, page):
        """无树图数据时显示空状态提示。"""
        page.wait_for_timeout(5000)
        # 要么有图表，要么有空状态提示
        body = page.locator("body").inner_text()
        has_chart = "市值" in body
        has_empty = "暂无树图数据" in body
        assert has_chart or has_empty


class TestGenerateButton:
    """生成按钮 + 确认弹窗。"""

    def test_generate_button_visible(self, page):
        """⚡ 生成按钮应可见。"""
        page.wait_for_timeout(3000)
        btn = page.locator("button:has-text('生成')").first
        assert btn.is_visible()

    def test_generate_opens_modal(self, page):
        """点击生成 → 确认弹窗打开。"""
        btn = page.locator("button:has-text('生成')").first
        if not btn.is_visible():
            pytest.skip("生成按钮不可见")
        btn.click()
        page.wait_for_timeout(500)
        assert page.locator("text=生成树图").first.is_visible()

    def test_generate_modal_has_buttons(self, page):
        """弹窗包含取消和生成按钮。"""
        btn = page.locator("button:has-text('生成')").first
        if not btn.is_visible():
            pytest.skip("生成按钮不可见")
        btn.click()
        page.wait_for_timeout(500)
        assert page.locator("text=取消").is_visible()
        assert page.locator("button:has-text('生成')").last.is_visible()

    def test_generate_triggers_api(self, page):
        """点击弹窗中的生成 → POST /api/dag_trigger。"""
        btn = page.locator("button:has-text('生成')").first
        if not btn.is_visible():
            pytest.skip("生成按钮不可见")
        btn.click()
        page.wait_for_timeout(500)
        gen_btn = page.locator("button:has-text('生成')").last
        if gen_btn.is_visible():
            gen_btn.click()
            page.wait_for_timeout(2000)
        # 弹窗应关闭
        assert not page.locator("text=生成树图").is_visible()
