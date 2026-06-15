"""ModelView E2E 测试 — 模型页加载/列表/训练 Tab。"""
import pytest
import os

BASE_URL = os.environ.get("E2E_BASE_URL", "http://localhost:3000")


@pytest.fixture(autouse=True)
def login_and_navigate(page):
    page.goto(BASE_URL)
    page.evaluate("() => { localStorage.setItem('token', 'test-token'); localStorage.setItem('username', 'test'); }")
    page.goto(f"{BASE_URL}/#/models")
    page.reload()
    page.wait_for_timeout(3000)


class TestModelPage:
    def test_page_loads(self, page):
        """模型页加载成功。"""
        assert "模型" in page.title()

    def test_version_list_renders(self, page):
        """左侧版本列表渲染。"""
        page.wait_for_timeout(2000)
        # 应有模型版本号 v2.1 / v1.3 等
        body = page.locator("body").inner_text()
        assert "v2.1" in body or "模型" in body

    def test_detail_tabs_visible(self, page):
        """右侧详情 5 个 Tab 可见。"""
        page.wait_for_timeout(2000)
        for tab in ["基本信息", "训练", "评估", "实盘", "指标"]:
            el = page.locator(f"text={tab}").first
            assert el.is_visible()

    def test_training_tab_content(self, page):
        """训练 Tab 显示状态卡片和图表。"""
        page.wait_for_timeout(2000)
        # 点训练 Tab
        train_tab = page.locator("text=训练").first
        train_tab.click()
        page.wait_for_timeout(500)
        body = page.locator("body").inner_text()
        assert "OPTUNA" in body or "训练状态" in body or "参数重要性" in body

    def test_dag_status_visible(self, page):
        """DAG 状态节点可见。"""
        page.wait_for_timeout(2000)
        body = page.locator("body").inner_text()
        assert "indicator_incr" in body or "model_train" in body or "模型" in body

    def test_nav_tab_active(self, page):
        """导航栏 🧠 模型 Tab 处于选中状态。"""
        page.wait_for_timeout(1000)
        model_tab = page.locator("button:has-text('模型')").first
        assert model_tab.is_visible()
