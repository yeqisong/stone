"""DAG 调度器单元测试 — 拓扑加载 + force 解析 + 计划-执行模型。"""
import pytest
from scripts.dag import DagExecutor, DagNode


class TestDagTopology:
    """测试 DAG 拓扑排序和依赖解析。"""

    def test_topo_sort_linear(self):
        """线性依赖: a → b → c。"""
        dag = DagExecutor()
        dag.add(DagNode("a", deps=[], fn=lambda **kw: True))
        dag.add(DagNode("b", deps=["a"], fn=lambda **kw: True))
        dag.add(DagNode("c", deps=["b"], fn=lambda **kw: True))
        result = dag._topo_sort({"a", "b", "c"})
        assert result == ["a", "b", "c"]

    def test_topo_sort_diamond(self):
        """菱形依赖: a → b, a → c, b → d, c → d。"""
        dag = DagExecutor()
        dag.add(DagNode("a", deps=[], fn=lambda **kw: True))
        dag.add(DagNode("b", deps=["a"], fn=lambda **kw: True))
        dag.add(DagNode("c", deps=["a"], fn=lambda **kw: True))
        dag.add(DagNode("d", deps=["b", "c"], fn=lambda **kw: True))
        result = dag._topo_sort({"a", "b", "c", "d"})
        assert result[0] == "a"
        assert result[-1] == "d"
        assert set(result[1:3]) == {"b", "c"}  # b, c 顺序任意

    def test_topo_sort_parallel_layer(self):
        """第一层并行: a, b, c 无依赖。"""
        dag = DagExecutor()
        dag.add(DagNode("a", deps=[], fn=lambda **kw: True))
        dag.add(DagNode("b", deps=[], fn=lambda **kw: True))
        dag.add(DagNode("c", deps=[], fn=lambda **kw: True))
        result = dag._topo_sort({"a", "b", "c"})
        assert set(result) == {"a", "b", "c"}

    def test_topo_sort_cycle_detection(self):
        """循环依赖应抛出异常。"""
        dag = DagExecutor()
        dag.add(DagNode("a", deps=["b"], fn=lambda **kw: True))
        dag.add(DagNode("b", deps=["a"], fn=lambda **kw: True))
        with pytest.raises(RuntimeError, match="循环依赖"):
            dag._topo_sort({"a", "b"})

    def test_get_downstream(self):
        """测试下游传播。"""
        dag = DagExecutor()
        dag.add(DagNode("a", deps=[], fn=lambda **kw: True))
        dag.add(DagNode("b", deps=["a"], fn=lambda **kw: True))
        dag.add(DagNode("c", deps=["b"], fn=lambda **kw: True))
        dag.add(DagNode("d", deps=[], fn=lambda **kw: True))
        downstream = dag._get_downstream("a")
        assert downstream == {"b", "c"}  # 不含 d

    def test_start_node_subset(self):
        """start_node="b" 时只影响 b 及下游，a 标记完成。"""
        dag = DagExecutor()
        dag.add(DagNode("a", deps=[], fn=lambda **kw: True))
        dag.add(DagNode("b", deps=["a"], fn=lambda **kw: True))
        dag.add(DagNode("c", deps=["b"], fn=lambda **kw: True))

        # 模拟 start 的部分逻辑（不实际执行）
        names = {"b"} | dag._get_downstream("b")
        for name in dag._nodes:
            if name not in names:
                dag._completed[name] = True

        sorted_names = dag._topo_sort(names)
        assert "a" not in sorted_names  # a 被跳过
        assert dag._completed.get("a") is True


class TestForceResolution:
    """测试 force 参数的两层兼容解析。"""

    def test_force_bool_true(self):
        """force=True → 所有数据节点返回 True。"""
        assert DagExecutor._resolve_force(True, 'kline') is True
        assert DagExecutor._resolve_force(True, 'index') is True
        assert DagExecutor._resolve_force(True, 'etf') is True
        assert DagExecutor._resolve_force(True, 'fund') is True

    def test_force_bool_false(self):
        """force=False → 所有数据节点返回 False。"""
        assert DagExecutor._resolve_force(False, 'kline') is False
        assert DagExecutor._resolve_force(False, 'fund') is False

    def test_force_dict_per_node(self):
        """force 为 dict 时按节点取值，缺失默认 false。"""
        force = {"kline": True, "fund": False}
        assert DagExecutor._resolve_force(force, 'kline') is True
        assert DagExecutor._resolve_force(force, 'fund') is False
        assert DagExecutor._resolve_force(force, 'index') is False  # 缺失
        assert DagExecutor._resolve_force(force, 'etf') is False

    def test_force_non_data_nodes_always_false(self):
        """非数据节点不受 force 影响。"""
        assert DagExecutor._resolve_force(True, 'treemap') is False
        assert DagExecutor._resolve_force(True, 'strategy') is False
        assert DagExecutor._resolve_force(True, 'stats') is False
        assert DagExecutor._resolve_force({"kline": True}, 'treemap') is False

    def test_force_dict_empty(self):
        """空 dict → 全部 false。"""
        assert DagExecutor._resolve_force({}, 'kline') is False


class TestLogIdFlow:
    """测试 log_id 在计划-执行流程中的传递。"""

    def test_start_injects_log_ids(self):
        """dag.start() 应将 _node_log_ids 注入 context（mock on_node_enter）。"""
        dag = DagExecutor()
        counter = [0]
        def mock_enter(name, status, **ctx):
            counter[0] += 1
            return counter[0]  # 返回模拟的 log_id
        dag.on_node_enter = mock_enter

        captured_log_ids = {}
        def fn(**kw):
            captured_log_ids.update(kw.get('_node_log_ids', {}))
            return True

        dag.add(DagNode("a", deps=[], fn=fn))
        dag.add(DagNode("b", deps=["a"], fn=fn))

        dag.start(trade_date="2026-06-07", force=False)
        assert captured_log_ids == {"a": 1, "b": 2}

    def test_log_ids_unique_per_run(self):
        """不同 run 应产生不同的 log_id。"""
        dag = DagExecutor()
        counter = [0]
        def mock_enter(name, status, **ctx):
            counter[0] += 1
            return counter[0]
        dag.on_node_enter = mock_enter

        collected = []
        def fn(**kw):
            collected.append(dict(kw.get('_node_log_ids', {})))
            return True

        dag.add(DagNode("x", deps=[], fn=fn))
        dag.start(trade_date="2026-06-07", force=False)
        dag.start(trade_date="2026-06-08", force=False)

        assert len(collected) == 2
        assert collected[0]["x"] != collected[1]["x"]


class TestDagStart:
    """测试统一入口 dag.start() 的计划创建逻辑。"""

    def test_start_full_without_start_node(self):
        """不传 start_node → 全量节点。"""
        dag = DagExecutor()
        executed = []

        def make_fn(name):
            def fn(**kw):
                executed.append(name)
                return True
            return fn

        dag.add(DagNode("a", deps=[], fn=make_fn("a")))
        dag.add(DagNode("b", deps=["a"], fn=make_fn("b")))

        dag.start(trade_date="2026-06-07", force=False)
        assert executed == ["a", "b"]

    def test_start_with_start_node(self):
        """传 start_node → 跳过上游。"""
        dag = DagExecutor()
        executed = []

        def make_fn(name):
            def fn(**kw):
                executed.append(name)
                return True
            return fn

        dag.add(DagNode("a", deps=[], fn=make_fn("a")))
        dag.add(DagNode("b", deps=["a"], fn=make_fn("b")))
        dag.add(DagNode("c", deps=["b"], fn=make_fn("c")))

        dag.start(start_node="b", trade_date="2026-06-07", force=False)
        assert "a" not in executed  # 上游跳过
        assert executed == ["b", "c"]

    def test_start_passes_force_to_context(self):
        """force 应被解析并注入 _node_force。"""
        dag = DagExecutor()
        captured_force = {}

        def data_fn(**kw):
            nf = kw.get('_node_force', {})
            captured_force.update(nf)
            return True

        dag.add(DagNode("kline", deps=[], fn=data_fn))
        dag.add(DagNode("fund", deps=["kline"], fn=data_fn))

        dag.start(trade_date="2026-06-07", force={"kline": True, "fund": False})
        assert captured_force.get("kline") is True
        assert captured_force.get("fund") is False

    def test_start_bool_force_expands(self):
        """force=True → 展开为所有数据节点 true。"""
        dag = DagExecutor()
        captured_force = {}

        def data_fn(**kw):
            nf = kw.get('_node_force', {})
            captured_force.update(nf)
            return True

        dag.add(DagNode("kline", deps=[], fn=data_fn))
        dag.add(DagNode("index", deps=[], fn=data_fn))

        dag.start(trade_date="2026-06-07", force=True)
        assert captured_force.get("kline") is True
        assert captured_force.get("index") is True
