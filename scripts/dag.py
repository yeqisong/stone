"""轻量 DAG 任务调度器 — 有向无环图 + 拓扑排序 + 手工触发下游传播。"""
from typing import Dict, List, Callable, Set
from loguru import logger
import time
import uuid


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
        self.on_node_enter = None                     # 节点执行前回调 fn(name, **context)

    def add(self, node: DagNode):
        self._nodes[node.name] = node
        for dep in node.deps:
            self._edges.setdefault(dep, []).append(node.name)

    def load_from_db(self, db_session, fn_map: Dict[str, callable]):
        """从 dag_config 表动态加载节点拓扑，fn_map 提供节点名 → 执行函数的映射。

        dag_config 表是拓扑的唯一来源，pipeline.py 只提供 {name: fn} 映射。
        新增节点只需：1. 写入 dag_config 表  2. 在 fn_map 中注册函数。
        """
        from sqlalchemy import text
        rows = db_session.execute(text(
            "SELECT node_name, deps, label FROM dag_config ORDER BY sort_order"
        )).fetchall()

        if not rows:
            logger.warning("[dag] dag_config 表为空，跳过加载")
            return

        for r in rows:
            name = r[0]
            deps = [d.strip() for d in r[1].split(',') if d.strip()]
            fn = fn_map.get(name)
            if fn is None:
                logger.warning(f"[dag] 节点 '{name}' 在 fn_map 中无对应函数，跳过")
                continue
            self.add(DagNode(name=name, deps=deps, fn=fn))

        logger.info(f"[dag] 从 dag_config 加载 {len(self._nodes)} 个节点")

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

    def _gen_run_id(self) -> str:
        """生成简短的任务ID。"""
        return uuid.uuid4().hex[:8]

    def _resolve_force(self, force, node_name: str) -> bool:
        """解析 force 参数为 per-node bool。
        自动从 DAG 拓扑推导可 force 的节点（无依赖的入口节点）。
        force 为 bool → 所有入口节点统一生效。
        force 为 dict → 取对应 key，缺失默认 false。
        非入口节点永远返回 false。
        """
        # 动态推导入口节点（无上游依赖）
        entries = {n for n, node in self._nodes.items() if not node.deps}
        if not entries:
            entries = {'kline', 'index', 'etf', 'fund'}  # fallback
        if node_name not in entries:
            return False
        if isinstance(force, bool):
            return force
        if isinstance(force, dict):
            return bool(force.get(node_name, False))
        return False

    def start(self, start_node: str = None, trade_date: str = '', force=False,
              include_downstream: bool = True):
        """统一入口：根据 start_node 创建计划并执行。"""
        run_id, sorted_names, context = self.prepare_context(
            trade_date, force, start_node, include_downstream)
        logger.info(f"[dag] start (start_node={start_node or 'None'}, force={force}) → "
                    f"{len(sorted_names)} 节点: {sorted_names}")
        self._execute(sorted_names, **context)

    def run(self, trigger: str, **context):
        """手工触发一个节点，自动传播到所有下游（兼容旧接口，内部转调 start）。"""
        if 'run_id' not in context:
            context['run_id'] = self._gen_run_id()
        td = context.get('trade_date', '')
        force = context.get('force', False)
        include_downstream = context.get('include_downstream', True)
        self.start(start_node=trigger, trade_date=td, force=force,
                   include_downstream=include_downstream)

    def prepare_context(self, trade_date: str = '', force=False, start_node: str = None,
                         include_downstream: bool = True):
        """抽象 context 初始化（供 start() 和动态流程 execute 共用）。

        Returns: (run_id, sorted_names, context_dict)
        """
        run_id = self._gen_run_id()
        self._completed.clear()

        # 确定节点集合
        if start_node is None:
            names = set(self._nodes.keys())
        else:
            if start_node not in self._nodes:
                raise ValueError(f"未知节点: {start_node}")
            names = {start_node}
            if include_downstream:
                names |= self._get_downstream(start_node)
            for name in self._nodes:
                if name not in names:
                    self._completed[name] = True

        sorted_names = self._topo_sort(names)

        context = {
            'run_id': run_id,
            'trade_date': trade_date,
            'force': force,
        }
        node_force = {name: self._resolve_force(force, name) for name in sorted_names}
        context['_node_force'] = node_force

        # 创建节点日志条目
        log_ids = {}
        if self.on_node_enter:
            for name in sorted_names:
                lid = self.on_node_enter(name, 'pending', **context)
                if lid:
                    log_ids[name] = lid
        context['_node_log_ids'] = log_ids
        self._wake_broadcast()

        return run_id, sorted_names, context

    def _wake_broadcast(self):
        """线程安全地通知 WS 广播立即推送。"""
        try:
            from app.signal import wake_dag_broadcast
            wake_dag_broadcast()
        except: pass

    def run_all(self, **context):
        """全部节点按拓扑顺序执行一次（兼容旧接口，内部转调 start）。"""
        td = context.get('trade_date', '')
        force = context.get('force', False)
        self.start(start_node=None, trade_date=td, force=force)

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

    def _run_with_hooks(self, node, context):
        """执行单个节点（context 是 dict，展开为 **kwargs）。"""
        return node.fn(**context)

    def _execute(self, sorted_names: List[str], **context):
        """按顺序执行，同层无依赖节点自动并行（ThreadPoolExecutor）。"""
        import concurrent.futures

        remaining = list(sorted_names)
        while remaining:
            # 找出当前所有依赖已满足的节点
            ready = []
            for name in remaining:
                node = self._nodes[name]
                if all(d in self._completed for d in node.deps):
                    ready.append(name)
            if not ready:
                # 没有就绪节点 → 死锁或依赖链断裂
                remaining_names = ', '.join(remaining)
                logger.error(f"[dag] 无就绪节点，剩余: {remaining_names}")
                for name in remaining:
                    if self.on_node_enter:
                        self.on_node_enter(name, 'failed', **context)
                break

            # 为本次运行创建终止事件（线程安全）
            run_id = context.get('run_id', '')
            from app.signal import set_stop_event, get_stop_event
            _stop_event = get_stop_event(run_id)

            # 并行执行就绪节点（启动前检查终止信号）
            with concurrent.futures.ThreadPoolExecutor(max_workers=len(ready)) as executor:
                future_info = {}
                for name in ready:
                    node = self._nodes[name]
                    from app.signal import is_stop_requested
                    if run_id and is_stop_requested(run_id):
                        log_id = (context.get('_node_log_ids', {}) or {}).get(name)
                        if log_id:
                            from scripts.pipeline import _terminate_node
                            _terminate_node(log_id, '用户手动终止')
                        self._completed[name] = time.time()
                        logger.info(f"[dag] {name} ⊗ 已终止 (跳过)")
                        continue
                    node_ctx = dict(context)
                    node_ctx['_stop_event'] = _stop_event
                    f = executor.submit(self._run_with_hooks, node, node_ctx)
                    future_info[f] = (name, time.time())
                for future in concurrent.futures.as_completed(future_info):
                    name, t0 = future_info[future]
                    try:
                        future.result()
                        elapsed = time.time() - t0
                        self._completed[name] = time.time()
                        logger.info(f"[dag] {name} ✓ ({elapsed:.1f}s)")
                    except Exception as e:
                        logger.error(f"[dag] {name} ✗ 失败: {e}")
                        self._completed[name] = time.time()  # 标记完成（失败态），下游可继续而非卡死

            # 从剩余列表中移除已执行的节点
            for name in ready:
                remaining.remove(name)

        # 执行完成后清理 stop 事件，防止内存泄漏
        run_id = context.get('run_id', '')
        if run_id:
            from app.signal import clear_stop_events
            clear_stop_events(run_id)
