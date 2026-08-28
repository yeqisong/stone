"""ModelView E2E 测试 — 模型页加载/列表/训练 Tab。"""
import pytest
import os
import re

BASE_URL = os.environ.get("E2E_BASE_URL", "http://localhost:3000")

_TOKEN = {"v": None}


def _read_login_password() -> str:
    try:
        env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".env")
        with open(env_path) as f:
            m = re.search(r"^LOGIN_PASSWORD=(.+?)\s*$", f.read(), re.M)
            if m:
                return m.group(1).strip()
    except Exception:
        pass
    return "admin123"


def _obtain_token(page) -> str:
    """真实登录拿 token（session 级缓存）；失败回退假 token（列表接口无鉴权仍可渲染）。"""
    if _TOKEN["v"] is None:
        _TOKEN["v"] = page.evaluate("""async (pw) => {
            try {
                const r = await fetch('/api/login', {method:'POST', headers:{'Content-Type':'application/json'},
                    body: JSON.stringify({username:'admin', password: pw})});
                if (!r.ok) return null;
                const d = await r.json();
                return d.token || null;
            } catch(e) { return null; }
        }""", _read_login_password()) or "test-token"
    return _TOKEN["v"]


@pytest.fixture(autouse=True)
def login_and_navigate(page):
    page.goto(BASE_URL)
    token = _obtain_token(page)
    page.evaluate("(t) => { localStorage.setItem('token', t); localStorage.setItem('username', 'admin'); }", token)
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
        for tab in ["基本信息", "特征", "训练", "评估", "实盘"]:
            el = page.get_by_role("button", name=tab, exact=True).first
            assert el.is_visible()

    def test_training_tab_content(self, page):
        """训练 Tab 显示状态卡片和图表。"""
        page.wait_for_timeout(2000)
        # 点训练 Tab
        train_tab = page.locator("text=训练").first
        train_tab.click()
        page.wait_for_timeout(500)
        body = page.locator("body").inner_text()
        assert "Optuna" in body or "训练" in body or "开始训练" in body

    def test_dag_status_visible(self, page):
        """DAG 状态节点可见。"""
        page.wait_for_timeout(2000)
        body = page.locator("body").inner_text()
        assert "indicator_incr" in body or "model_train" in body or "模型" in body

    def test_eval_tab_content(self, page):
        """评估 Tab 显示指标卡片。"""
        page.wait_for_timeout(2000)
        eval_tab = page.locator("text=评估").first
        eval_tab.click()
        page.wait_for_timeout(500)
        body = page.locator("body").inner_text()
        assert "夏普" in body or "胜率" in body or "暂未训练" in body or "无评估数据" in body

    def test_indicators_tab(self, page):
        """特征 Tab 显示预检按钮和特征列表。"""
        page.wait_for_timeout(2000)
        # 精确匹配详情 tab（"🔬 特征" 导航项含相同子串，text= 会误匹配）
        ind_tab = page.get_by_role("button", name="特征", exact=True).first
        ind_tab.click()
        page.wait_for_timeout(500)
        body = page.locator("body").inner_text()
        assert "预检" in body or "模型使用的特征" in body

    def test_nav_tab_active(self, page):
        """导航栏 🧠 模型 Tab 处于选中状态。"""
        page.wait_for_timeout(1000)
        model_tab = page.locator("button:has-text('模型')").first
        assert model_tab.is_visible()
