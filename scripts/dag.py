"""轻量 DAG 任务调度器 — 有向无环图 + 拓扑排序 + 手工触发下游传播。"""
from typing import Dict, List, Callable, Set
from loguru import logger
import time


class DagNode:
    def __init__(self, name: str, deps: List[str], fn: Callable):
        self.name = name       # 节点唯一标识
        self.deps = deps       # [依赖节点名, ...]，空列表表示无依赖
        self.fn = fn           # fn(**context)

    def __repr__(self):
        return f"DagNode({self.name}, deps={self.deps})"


class DagExecutor:
    """DAG 调度器。

    依赖检查逻辑：节点曾经完成过即可（累计记录），不是本轮刚完成。
    手工触发一个节点 → 自动传播到所有下游（只要依赖都曾经完成过）。

    用法:
        dag = DagExecutor()
        dag.add(DagNode("kline",    deps=[],          fn=download_kline))
        dag.add(DagNode("treemap",  deps=["kline"],   fn=gen_treemap))
        dag.add(DagNode("stats",    deps=["treemap","index"], fn=gen_stats))

        dag.run("kline")   # kline→treemap→stats（复用 index 上次结果）
        dag.run_all()      # 全部按拓扑顺序执行一次
        dag.status()       # 查看各节点最近完成时间
    """

    def __init__(self):
        self._nodes: Dict[str, DagNode] = {}
        self._edges: Dict[str, List[str]] = {}       # node → 下游列表
        self._completed: Dict[str, float] = {}        # node → 最近完成时间戳

    def add(self, node: DagNode):
        self._nodes[node.name] = node
        for dep in node.deps:
            self._edges.setdefault(dep, []).append(node.name)

    def _topo_sort(self, names: Set[str]) -> List[str]:
        """拓扑排序。"""
        in_degree = {n: 0 for n in names}
        for n in names:
            node = self._nodes.get(n)
            if node:
                for dep in node.deps:
                    if dep in names:
                        in_degree[n] = in_degree.get(n, 0) + 1
        queue = [n for n, d in in_degree.items() if d == 0]
        result = []
        while queue:
            n = queue.pop(0)
            result.append(n)
            for downstream in self._edges.get(n, []):
                if downstream in in_degree:
                    in_degree[downstream] -= 1
                    if in_degree[downstream] == 0:
                        queue.append(downstream)
        if len(result) != len(names):
            raise RuntimeError(f"循环依赖: {names - set(result)}")
        return result

    def _get_downstream(self, name: str) -> Set[str]:
        """获取所有下游（含间接下游）。"""
        visited = set()
        queue = [name]
        while queue:
            n = queue.pop(0)
            for d in self._edges.get(n, []):
                if d not in visited:
                    visited.add(d)
                    queue.append(d)
        return visited

    def run(self, trigger: str, **context):
        """手工触发一个节点，自动传播到所有下游（依赖满足即跑）。"""
        if trigger not in self._nodes:
            raise ValueError(f"未知节点: {trigger}")

        affected = {trigger} | self._get_downstream(trigger)
        sorted_names = self._topo_sort(affected)

        logger.info(f"[dag] 触发 {trigger} → 影响 {len(sorted_names)} 个节点: {sorted_names}")
        self._execute(sorted_names, **context)

    def run_all(self, **context):
        """全部节点按拓扑顺序执行一次。"""
        sorted_names = self._topo_sort(set(self._nodes.keys()))
        logger.info(f"[dag] 全量执行 {len(sorted_names)} 个节点")
        self._execute(sorted_names, **context)

    def run_node(self, name: str, **context):
        """只执行单个节点（不传播下游），用于重跑失败节点。"""
        if name not in self._nodes:
            raise ValueError(f"未知节点: {name}")
        logger.info(f"[dag] 单节点重跑 {name}")
        self._execute([name], **context)

    def status(self) -> Dict[str, str]:
        """查看各节点最近完成时间。"""
        from datetime import datetime
        return {name: datetime.fromtimestamp(ts).strftime('%H:%M:%S')
                for name, ts in sorted(self._completed.items())}

    def _execute(self, sorted_names: List[str], **context):
        """按顺序执行，依赖检查基于累计完成记录。"""
        for name in sorted_names:
            node = self._nodes[name]
            # 依赖检查：曾经完成过即可（累计记录）
            missing = [d for d in node.deps if d not in self._completed]
            if missing:
                logger.info(f"[dag] {name} 跳过，依赖未完成: {missing}")
                continue
            try:
                t0 = time.time()
                result = node.fn(**context)
                elapsed = time.time() - t0
                self._completed[name] = time.time()
                logger.info(f"[dag] {name} ✓ ({elapsed:.2f}s)")
            except Exception as e:
                logger.error(f"[dag] {name} ✗ 失败: {e}")
                raise
