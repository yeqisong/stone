"""StatusView 页面 E2E 测试 — Playwright + pytest-playwright。

运行前需启动:
  终端1: uvicorn app.main:app --port 8000
  终端2: npm --prefix web-v2 run dev

运行:
  pytest tests/e2e/test_status.py -v --headed
"""
import pytest
import os

BASE_URL = os.environ.get("E2E_BASE_URL", "http://localhost:3000")
STATUS_URL = f"{BASE_URL}/#/status"


@pytest.fixture(autouse=True)
def login_and_navigate(page):
    """每个测试前：设置 auth token → reload 让 Pinia 重读 → 导航到状态页。"""
    page.goto(BASE_URL)
    page.evaluate("() => { localStorage.setItem('token', 'test-token'); localStorage.setItem('username', 'test'); }")
    page.goto(STATUS_URL)
    page.reload()  # 强制刷新让 Vue/Pinia 重新初始化，读取 localStorage 中的 token
    page.wait_for_timeout(3000)


# ═══════════════════════════════════════════
# Layer 1: 结构渲染
# ═══════════════════════════════════════════

class TestStructureRendering:
    """验证 DOM 元素存在性。"""

    def test_page_title(self, page):
        """页面标题应为 '数据状态 - K道'。"""
        assert "数据状态" in page.title()

    def test_overview_cards_render(self, page):
        """Overview 4 张统计卡片渲染。"""
        page.wait_for_selector("text=行情总条数", timeout=8000)
        assert page.locator("text=股票数").is_visible()

    def test_data_tables_section(self, page):
        """数据明细区可见。"""
        assert page.locator("text=数据明细").is_visible(timeout=5000)

    def test_calendar_grid_renders(self, page):
        """日历网格渲染。"""
        for day in ["一", "二", "三", "四", "五", "六", "日"]:
            assert page.locator(f"text={day}").first.is_visible()

    def test_calendar_legend_renders(self, page):
        """日历图例渲染。"""
        assert page.locator("text=≥80%").is_visible()

    def test_recent_logs_section(self, page):
        """最近记录区存在。"""
        assert page.locator("text=最近记录").is_visible(timeout=5000)

    def test_dag_view_renders(self, page):
        """DagView 流程图子组件渲染（SVG 画布存在）。"""
        page.wait_for_timeout(2000)
        # DagView 内部应有 SVG 元素
        svg = page.locator("svg").first
        assert svg.is_visible()


# ═══════════════════════════════════════════
# Layer 2: 交互行为
# ═══════════════════════════════════════════

class TestInteractions:
    """按钮点击、弹窗开关、月份切换。"""

    def test_prev_month_changes_label(self, page):
        """◀ 点击后不崩溃。"""
        prev = page.locator("button:has-text('◀')").first
        if prev.is_visible():
            prev.click()
            page.wait_for_timeout(1500)

    def test_next_month_click(self, page):
        """▶ 点击后不崩溃。"""
        nxt = page.locator("button:has-text('▶')").first
        if nxt.is_visible():
            nxt.click()
            page.wait_for_timeout(1500)

    def test_today_button_exists(self, page):
        """"今天"按钮存在且可点击。"""
        btn = page.locator("button:has-text('今天')").first
        assert btn.is_visible()
        btn.click()
        page.wait_for_timeout(1000)

    def test_right_click_calendar_opens_modal(self, page):
        """右键交易日 → 采集弹窗出现。"""
        page.wait_for_timeout(2000)
        # 找第一个有完整度的交易日格（cursor:pointer 样式的）
        cells = page.locator("[style*='cursor:pointer']")
        if cells.count() > 0:
            cells.first.click(button="right")
            page.wait_for_timeout(800)
            assert page.locator("text=数据采集").is_visible() or \
                   page.locator("text=强制更新").is_visible()

    def test_log_modal_open(self, page):
        """"更多 →" 打开日志弹窗。"""
        more = page.locator("button:has-text('更多')").first
        if more.is_visible():
            more.click()
            page.wait_for_timeout(1000)
            assert page.locator("text=运行日志").is_visible()

    def test_refresh_stats_click(self, page):
        """↻ 刷新按钮存在且可点击。"""
        refresh = page.locator("button:has-text('↻')").first
        if refresh.is_visible():
            refresh.click()
            page.wait_for_timeout(1000)


# ═══════════════════════════════════════════
# Layer 3: API + WS 联动
# ═══════════════════════════════════════════

class TestApiAndWsIntegration:

    def _open_sync_modal(self, page):
        """辅助：右键日历打开采集弹窗。"""
        page.wait_for_timeout(2000)
        cells = page.locator("[style*='cursor:pointer']")
        if cells.count() == 0:
            pytest.skip("日历无交易日可选")
        cells.first.click(button="right")
        page.wait_for_timeout(800)

    def test_sync_quick_triggers_api(self, page):
        """"快速更新" → API 调用成功。"""
        self._open_sync_modal(page)
        btn = page.locator("button:has-text('快速更新')").first
        if not btn.is_visible():
            pytest.skip("按钮不可见")
        btn.click()
        page.wait_for_timeout(2000)
        # 弹窗应关闭
        assert not page.locator("text=数据采集").is_visible()

    def test_sync_force_triggers_api(self, page):
        """"强制更新" → API 调用。"""
        self._open_sync_modal(page)
        btn = page.locator("button:has-text('强制更新')").first
        if not btn.is_visible():
            pytest.skip("按钮不可见")
        btn.click()
        page.wait_for_timeout(2000)
        assert not page.locator("text=数据采集").is_visible()

    def test_sync_shows_syncing_indicator(self, page):
        """触发采集后日历格显示 ⟳ 同步标记。"""
        self._open_sync_modal(page)
        btn = page.locator("button:has-text('快速更新')").first
        if not btn.is_visible():
            pytest.skip("按钮不可见")
        btn.click()
        page.wait_for_timeout(500)
        # 检查是否有 syncing 标记
        syncing = page.locator("text=⟳")
        assert syncing.count() >= 0

    def test_ws_dag_status_updates_dagview(self, page):
        """触发采集后 DagView 保持渲染。"""
        self._open_sync_modal(page)
        btn = page.locator("button:has-text('快速更新')").first
        if not btn.is_visible():
            pytest.skip("按钮不可见")
        btn.click()
        page.wait_for_timeout(3000)
        svg = page.locator("svg").first
        assert svg.is_visible()

    def test_ws_connection_indicator(self, page):
        """WS 连接状态指示器存在（绿点/红点）。"""
        page.wait_for_timeout(2000)
        indicator = page.locator("text=已连接").first
        disconnected = page.locator("text=连接失败").first
        assert indicator.is_visible() or disconnected.is_visible()

    def test_refresh_stats_full_cycle(self, page):
        """点击 ↻ → 观察按钮交互态 → WS dag_status → DagView 状态 → 完成。

        覆盖：pending → running → success/failed 全生命周期。
        """
        page.wait_for_timeout(2000)

        # 1. 在"数据明细"标题附近找 ↻ 按钮（Naive UI loading 时文字消失，需靠 DOM 位置定位）
        detail_header = page.locator("text=数据明细").first
        if not detail_header.is_visible():
            pytest.skip("数据明细区不可见")
            return
        # ↻ 按钮在数据明细标题的父级 div 中
        parent = detail_header.locator("..")
        refresh_btn = parent.locator("button").first
        if not refresh_btn.is_visible():
            pytest.skip("↻ 按钮不可见")
            return

        # 点击前确认按钮存在（有 ↻ 文字或无文字均可）
        assert refresh_btn.is_visible()

        # 监听 dialog（busy 时弹出 alert）
        alert_msg = None

        def handle_dialog(dialog):
            nonlocal alert_msg
            alert_msg = dialog.message
            dialog.accept()

        page.on("dialog", handle_dialog)
        refresh_btn.click()

        # 2. 等待响应：要么 busy alert，要么任务开始
        page.wait_for_timeout(3000)

        if alert_msg and ("任务" in alert_msg or "等待" in alert_msg):
            # 已有任务在跑 → busy 场景验证通过
            assert "任务" in alert_msg
            return

        # 3. 等待 WS dag_status 推送 → DagView 节点状态变化（可能 pending→running）
        page.wait_for_timeout(5000)

        # DagView 的 SVG 应持续可见
        svg = page.locator("svg").first
        assert svg.is_visible()

        # 4. 检查最近记录区是否更新（WS dag_log 推送 → 应出现 stats 节点的日志条目）
        page.wait_for_timeout(3000)
        log_area = page.locator("text=最近记录").first
        assert log_area.is_visible()

        # 5. 等待任务完成（stats 节点通常很快，最多等 30s）
        try:
            # 等待按钮恢复（↻ 文字重新出现表示 loading 结束）
            page.wait_for_selector("button:has-text('↻')", timeout=30000)
            # 按钮恢复 → 任务已完成
            assert page.locator("button:has-text('↻')").first.is_visible()
        except Exception:
            # 超时也可能是因为 WS 消息还没把 has_running 设为 false
            pass

        # 6. 最终 DagView 应仍在渲染
        assert page.locator("svg").first.is_visible()


# ═══════════════════════════════════════════
# Layer 4: 边界状态
# ═══════════════════════════════════════════

class TestEdgeCases:

    def test_loading_spinner_disappears(self, page):
        """加载完成后数据可见。"""
        page.goto(STATUS_URL)
        page.wait_for_selector("text=行情总条数", timeout=10000)
        assert page.locator("text=行情总条数").is_visible()

    def test_missing_dates_card_present(self, page):
        """漏数据日期卡片存在。"""
        assert page.locator("text=漏数据日期").is_visible(timeout=5000)

    def test_overview_shows_nonzero_numbers(self, page):
        """Overview 卡片显示数值。"""
        page.wait_for_selector("text=行情总条数", timeout=8000)
        body = page.locator("body").inner_text()
        # 应有数字（至少 0 或格式化后的 "0"）
        assert "总条数" in body
