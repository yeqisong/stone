"""app.signal 模块单元测试 — 验证解耦后的唤醒机制。"""
import asyncio
import pytest
import threading
import time


class TestSignalModule:
    """测试 app.signal 的 Event 唤醒机制。"""

    def test_wake_event_set_clears(self):
        """Event.set() 后 wait() 应立即返回。"""
        from app.signal import _dag_wake_event

        _dag_wake_event.clear()
        _dag_wake_event.set()

        async def _wait():
            await asyncio.wait_for(_dag_wake_event.wait(), timeout=0.5)
            return True

        result = asyncio.run(_wait())
        assert result is True
        _dag_wake_event.clear()

    def test_wake_event_timeout_when_not_set(self):
        """未 set 时 wait 应超时。"""
        from app.signal import _dag_wake_event

        _dag_wake_event.clear()

        async def _wait():
            try:
                await asyncio.wait_for(_dag_wake_event.wait(), timeout=0.1)
                return False
            except asyncio.TimeoutError:
                return True

        result = asyncio.run(_wait())
        assert result is True

    def test_wake_dag_broadcast_without_main_loop(self):
        """没有 main_loop 时，直接 set Event（fallback 路径）。"""
        from app.signal import _dag_wake_event, wake_dag_broadcast, set_main_loop

        # 确保 main_loop 为 None
        import app.signal as sig
        sig._main_loop = None
        _dag_wake_event.clear()

        wake_dag_broadcast()
        assert _dag_wake_event.is_set()


class TestSignalThreadSafety:
    """验证线程安全的唤醒。"""

    def test_wake_from_background_thread(self):
        """后台线程通过 call_soon_threadsafe 唤醒 asyncio wait。"""
        from app.signal import set_main_loop, wake_dag_broadcast
        import app.signal as sig

        async def _main():
            loop = asyncio.get_running_loop()
            # 创建绑定到当前 loop 的 Event
            evt = asyncio.Event()
            set_main_loop(loop)

            # 临时替换模块级 Event 为本地 Event（测试用）
            orig_event = sig._dag_wake_event
            sig._dag_wake_event = evt

            def _wake_later():
                time.sleep(0.05)
                wake_dag_broadcast()

            t = threading.Thread(target=_wake_later, daemon=True)
            t.start()

            try:
                await asyncio.wait_for(evt.wait(), timeout=1.0)
                return True
            except asyncio.TimeoutError:
                return False
            finally:
                sig._dag_wake_event = orig_event
                sig._main_loop = None

        result = asyncio.run(_main())
        assert result is True

    def test_wake_with_closed_loop_does_not_crash(self):
        """loop 已关闭时调用 call_soon_threadsafe 不应崩溃（fallback 到直接 set）。"""
        from app.signal import _dag_wake_event, wake_dag_broadcast, set_main_loop
        import app.signal as sig

        # 模拟一个已关闭的 loop
        class FakeClosedLoop:
            def is_running(self):
                return False

        sig._main_loop = FakeClosedLoop()
        _dag_wake_event.clear()

        # 不应抛异常
        wake_dag_broadcast()
        assert _dag_wake_event.is_set()
        sig._main_loop = None
