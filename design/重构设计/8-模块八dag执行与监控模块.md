# K道量化平台 - DAG执行与监控模块开发设计方案


## 一、模块概述

### 1.1 模块定位

DAG执行与监控模块是K道量化平台的 **“调度执行层”** 。它负责接收来自**DAG流程管理模块**的流程定义（手动触发或Cron定时触发），按照流程图中的拓扑顺序调用各业务模块（行情下载、特征计算等）的执行接口，并通过**WebSocket通道模块**实时将执行进度推送给前端，同时持久化存储执行历史供后续审计和回溯。

**核心定位**：本模块是连接“流程定义”与“业务执行”的桥梁，是平台自动化运转的引擎。

### 1.2 模块在整体架构中的位置

```
┌─────────────────────────────────────────────────────────────────────┐
│                     前端（流程监控页面）                           │
│  • 执行列表（历史 + 实时）                                         │
│  • 展开查看DAG可视化（实时状态着色）                                │
│  • 节点详情（日志、进度、结果）                                     │
└─────────────────────────────┬───────────────────────────────────────┘
                              │ WebSocket（实时推送）
                              │ HTTP（历史查询）
                              ▼
┌─────────────────────────────────────────────────────────────────────┐
│                DAG执行与监控模块（本模块）                         │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │  触发器层                                                   │   │
│  │  • Cron调度器（监听定时任务）                               │   │
│  │  • 手动触发接口                                             │   │
│  └────────────────────────┬────────────────────────────────────┘   │
│                           │                                        │
│  ┌────────────────────────▼────────────────────────────────────┐   │
│  │  执行引擎                                                   │   │
│  │  • 解析流程定义（flow_graph + node_configs）               │   │
│  │  • 拓扑排序，确定执行顺序                                   │   │
│  │  • 节点调用（调用各业务模块的 /dag/execute）               │   │
│  │  • 进度聚合与转发                                          │   │
│  │  • 异常处理与重试                                          │   │
│  └────────────────────────┬────────────────────────────────────┘   │
│                           │                                        │
│  ┌────────────────────────▼────────────────────────────────────┐   │
│  │  状态管理                                                   │   │
│  │  • 执行实例状态机                                           │   │
│  │  • 节点执行状态记录                                         │   │
│  │  • 执行历史持久化                                           │   │
│  └─────────────────────────────────────────────────────────────┘   │
└─────────────────────────────┬───────────────────────────────────────┘
                              │
        ┌─────────────────────┼─────────────────────┐
        │                     │                     │
        ▼                     ▼                     ▼
┌───────────────────┐ ┌───────────────────┐ ┌───────────────────┐
│ 流程管理模块       │ │ 业务模块          │ │ WebSocket通道模块  │
│ 读取流程定义       │ │ 调用执行接口      │ │ 推送进度           │
└───────────────────┘ └───────────────────┘ └───────────────────┘
```

### 1.3 核心设计原则

| 原则 | 说明 |
| :--- | :--- |
| **异步执行** | 所有流程执行均为异步，触发后立即返回，执行过程在后台进行 |
| **状态可观测** | 每个执行实例的状态、每个节点的状态均可实时查询 |
| **进度可推送** | 执行过程中的状态变化通过WebSocket主动推送给前端 |
| **断点可追溯** | 执行历史完整持久化，支持按时间、状态、流程等多维度检索 |
| **重试机制** | 节点执行失败时支持自动重试（可配置次数和间隔） |
| **隔离性** | 不同执行实例之间完全隔离，互不影响 |


## 二、数据模型设计

### 2.1 执行实例表：`dag_executions`

记录每次流程执行的完整信息。

| 字段名 | 类型 | 约束 | 说明 |
| :--- | :--- | :--- | :--- |
| `id` | BIGINT | PRIMARY KEY, AUTO_INCREMENT | 主键 |
| `execution_id` | VARCHAR(64) | UNIQUE, NOT NULL | 全局唯一执行ID，格式：`exec_{timestamp}_{flow_id}` |
| `flow_id` | BIGINT | NOT NULL | 关联 `dag_flows.id` |
| `flow_version` | INT | NOT NULL | 执行时使用的流程版本号 |
| `flow_name` | VARCHAR(128) | NOT NULL | 冗余存储，便于快速检索 |
| `trigger_type` | VARCHAR(16) | NOT NULL | 触发方式：`manual` / `cron` / `api` |
| `triggered_by` | VARCHAR(64) | — | 触发者（手动触发时为用户名，定时触发时为 `system`） |
| `status` | VARCHAR(16) | NOT NULL | 执行状态（见状态机） |
| `started_at` | DATETIME | — | 开始执行时间 |
| `finished_at` | DATETIME | — | 结束执行时间 |
| `duration_ms` | BIGINT | — | 总耗时（毫秒） |
| `node_status` | JSON | — | 各节点的执行状态快照（用于前端展示） |
| `error_message` | TEXT | — | 整体错误信息（如有） |
| `created_at` | DATETIME | DEFAULT CURRENT_TIMESTAMP | 创建时间 |

**`node_status` 结构示例**：
```json
{
  "node_1": { "type": "download", "status": "completed", "started_at": "...", "finished_at": "...", "duration_ms": 3000, "result": "..." },
  "node_2": { "type": "feature", "status": "running", "started_at": "...", "finished_at": null, "duration_ms": null },
  "node_3": { "type": "model_train", "status": "pending", "started_at": null, "finished_at": null, "duration_ms": null }
}
```

### 2.2 执行状态机

```
                         ┌─────────────────────────────────┐
                         │                                 │
                         ▼                                 │
┌──────────┐    ┌──────────────┐    ┌──────────────┐    ┌──┴───────────┐
│ PENDING  │───▶│   RUNNING    │───▶│  COMPLETED   │    │   CANCELLED   │
│ (已触发)  │    │  (执行中)    │    │  (全部成功)   │    │  (已取消)     │
└──────────┘    └──────┬───────┘    └──────────────┘    └──────────────┘
                       │
                       ▼
                ┌──────────────┐
                │   PARTIAL    │
                │ (部分成功)    │
                └──────────────┘
                       │
                       ▼
                ┌──────────────┐
                │   FAILED     │
                │ (执行失败)    │
                └──────────────┘
```

**状态说明**：

| 状态 | 说明 | 转换条件 |
| :--- | :--- | :--- |
| `pending` | 已触发，等待资源分配 | 触发后立即进入 |
| `running` | 正在执行中 | 调度引擎开始处理 |
| `completed` | 全部节点执行成功 | 所有节点状态为 `completed` |
| `partial` | 部分节点成功，部分失败 | 有节点失败但未终止整体执行 |
| `failed` | 整体执行失败 | 关键节点失败且未配置继续 |
| `cancelled` | 用户手动取消 | 用户在监控页点击取消 |


## 三、执行引擎架构

### 3.1 执行流程总览

```
触发执行（手动/Cron）
        │
        ▼
1. 生成 execution_id，插入 dag_executions（状态：pending）
        │
        ▼
2. 从流程管理模块加载流程定义
   • flow_graph（节点列表 + 连线列表）
   • node_configs（各节点参数）
   • 该流程的版本号
        │
        ▼
3. 拓扑排序，确定节点执行顺序
   • 检测环（安全校验）
   • 生成并行执行计划
        │
        ▼
4. 更新执行状态为 running，推送 WebSocket
        │
        ▼
5. 按拓扑顺序逐批执行节点
   • 并行节点：放入线程池并发执行
   • 串行节点：等待前置节点完成
   • 每个节点：调用业务模块 /dag/execute
   • 接收进度回调 → 推送 WebSocket
   • 记录节点结果
        │
        ▼
6. 所有节点执行完毕，汇总结果
   • 全部成功 → status = completed
   • 部分失败 → status = partial
   • 关键失败 → status = failed
        │
        ▼
7. 回写执行结果，推送最终状态
        │
        ▼
8. 清理运行时上下文，释放资源
```

### 3.2 核心组件

#### 3.2.1 执行上下文（Execution Context）

每个执行实例对应一个运行时上下文，包含：

```python
class ExecutionContext:
    execution_id: str
    flow_id: int
    flow_version: int
    flow_graph: dict           # 流程拓扑
    node_configs: dict         # 节点配置
    node_status: dict          # 实时节点状态
    progress_callbacks: dict   # 各节点的进度回调URL（由业务模块回调时传入）
    start_time: datetime
    is_cancelled: bool         # 取消标记
```

#### 3.2.2 拓扑执行器（TopologyExecutor）

负责按拓扑顺序调度节点执行：

```python
def execute_topology(self, context: ExecutionContext):
    # 1. 计算入度
    in_degree = {node['id']: 0 for node in context.flow_graph['nodes']}
    for edge in context.flow_graph['edges']:
        in_degree[edge['target']] += 1
    
    # 2. 初始化待执行队列
    queue = deque([node_id for node_id, deg in in_degree.items() if deg == 0])
    
    # 3. 按批次执行
    while queue:
        batch = list(queue)  # 当前可并行执行的节点列表
        
        # 并发执行本批次所有节点
        results = await asyncio.gather(*[
            self.execute_node(node_id, context) for node_id in batch
        ])
        
        # 更新入度
        for node_id in batch:
            for edge in context.flow_graph['edges']:
                if edge['source'] == node_id:
                    in_degree[edge['target']] -= 1
                    if in_degree[edge['target']] == 0:
                        queue.append(edge['target'])
        
        # 检查取消标记
        if context.is_cancelled:
            break
    
    return self.aggregate_results(context)
```

#### 3.2.3 节点执行器（NodeExecutor）

负责执行单个节点：

```python
def execute_node(self, node_id: str, context: ExecutionContext):
    node_def = next(n for n in context.flow_graph['nodes'] if n['id'] == node_id)
    node_config = context.node_configs.get(node_id, {})
    
    # 1. 更新状态为 running
    self.update_node_status(context, node_id, 'running')
    self.push_progress(context, node_id, 'running')
    
    # 2. 从节点注册中心获取业务模块信息
    node_type = self.registry.get_node_type(node_def['type'])
    
    # 3. 构建执行请求
    request = {
        "execution_id": context.execution_id,
        "flow_id": context.flow_id,
        "flow_version": context.flow_version,
        "node_id": node_id,
        "config": node_config,
        "context": {
            "date_range": context.date_range,  # 由调度引擎计算
            "trigger_time": context.start_time.isoformat(),
            "timezone": "Asia/Shanghai"
        },
        "progress_callback_url": f"{self.callback_base_url}/progress/{context.execution_id}/{node_id}",
        "status_callback_url": f"{self.callback_base_url}/status/{context.execution_id}/{node_id}"
    }
    
    # 4. 调用业务模块的 /dag/execute 接口
    response = await self.http_client.post(
        f"{node_type.api_base_url}{node_type.execution_endpoint}",
        json=request,
        timeout=300  # 5分钟超时
    )
    
    # 5. 记录返回的 sub_tasks 结构（用于前端展示节点内部子图）
    self.update_node_subtasks(context, node_id, response['sub_tasks'])
    
    return response
```

### 3.3 进度回调接收器

业务模块在执行过程中会主动回调 `progress_callback_url`，本模块接收并处理：

```python
@router.post("/callback/progress/{execution_id}/{node_id}")
async def receive_progress(
    execution_id: str,
    node_id: str,
    payload: ProgressPayload
):
    # 1. 更新执行上下文中的节点状态
    context = execution_manager.get_context(execution_id)
    context.node_status[node_id]['sub_tasks'] = payload.data.get('sub_tasks', [])
    context.node_status[node_id]['overall_percent'] = payload.data.get('overall_percent', 0)
    
    # 2. 立即通过WebSocket推送给前端
    await ws_channel.publish(
        topic="dag.execution.progress",
        execution_id=execution_id,
        data={
            "node_id": node_id,
            "status": payload.data.get('status'),
            "overall_percent": payload.data.get('overall_percent'),
            "sub_tasks": payload.data.get('sub_tasks', [])
        }
    )
    
    # 3. 更新数据库（可批量更新，减少IO）
    await self._update_node_status_db(execution_id, node_id, payload.data)
    
    return {"code": 0, "message": "ok"}
```

### 3.4 触发机制

#### 3.4.1 手动触发

前端通过 `POST /api/dag/executions/run` 接口触发：

```python
def manual_trigger(flow_id: int, triggered_by: str):
    # 1. 检查流程是否存在且状态为 enabled
    flow = flow_service.get_flow(flow_id)
    if flow.status != 'enabled':
        raise ValueError("流程未启用，无法执行")
    
    # 2. 获取最新版本
    version = flow.version
    
    # 3. 创建执行实例
    execution_id = f"exec_{int(time.time())}_{flow_id}"
    execution = dag_execution_service.create(
        execution_id=execution_id,
        flow_id=flow_id,
        flow_version=version,
        flow_name=flow.flow_name,
        trigger_type='manual',
        triggered_by=triggered_by
    )
    
    # 4. 异步提交执行任务
    executor.submit(execution_id)
    
    return {"execution_id": execution_id, "status": "pending"}
```

#### 3.4.2 Cron定时触发

调度器定期扫描 `dag_flows` 表，检查是否有 `cron_expression` 不为空且 `status='enabled'` 的流程，使用 `croniter` 计算下次触发时间：

```python
class CronScheduler:
    def run(self):
        while True:
            now = datetime.now()
            flows = flow_service.get_enabled_with_cron()
            
            for flow in flows:
                # 计算下次触发时间
                cron = croniter(flow.cron_expression, flow.last_triggered_at or now)
                next_time = cron.get_next(datetime)
                
                # 如果当前时间 >= 下次触发时间，则触发执行
                if now >= next_time:
                    self.trigger_flow(flow.id, trigger_type='cron', triggered_by='system')
                    # 更新上次触发时间
                    flow_service.update_last_triggered_at(flow.id, now)
            
            time.sleep(60)  # 每分钟扫描一次
```


## 四、WebSocket实时推送协议

### 4.1 推送主题设计

| Topic | 说明 | 推送时机 |
| :--- | :--- | :--- |
| `dag.execution.started` | 执行实例开始 | 流程开始执行时推送一次 |
| `dag.execution.progress` | 执行进度更新 | 节点状态变化、子任务进度更新时推送 |
| `dag.execution.completed` | 执行实例结束 | 全部节点执行完成时推送一次 |
| `dag.execution.failed` | 执行实例失败 | 执行失败时推送 |
| `dag.execution.cancelled` | 执行实例取消 | 用户取消时推送 |

### 4.2 推送消息格式

**开始消息**：
```json
{
  "topic": "dag.execution.started",
  "data": {
    "execution_id": "exec_20260629_001",
    "flow_id": 123,
    "flow_name": "每日数据更新",
    "trigger_type": "cron",
    "started_at": "2026-06-29T18:30:00Z",
    "total_nodes": 5
  }
}
```

**进度消息**：
```json
{
  "topic": "dag.execution.progress",
  "data": {
    "execution_id": "exec_20260629_001",
    "overall_percent": 65.5,
    "node_status": {
      "node_1": { "type": "download", "status": "completed", "percent": 100 },
      "node_2": { "type": "feature", "status": "running", "percent": 60 },
      "node_3": { "type": "model_train", "status": "pending", "percent": 0 }
    },
    "current_running_nodes": ["node_2"],
    "completed_nodes": ["node_1"]
  }
}
```

**节点展开详情（当用户点击展开某个节点时）**：
```json
{
  "topic": "dag.execution.node.detail",
  "data": {
    "execution_id": "exec_20260629_001",
    "node_id": "node_2",
    "status": "running",
    "sub_tasks": [
      { "id": "dep_ma5", "label": "MA(5)", "status": "completed", "percent": 100 },
      { "id": "dep_rsi", "label": "RSI(14)", "status": "running", "percent": 60 },
      { "id": "target", "label": "偏离度", "status": "pending", "percent": 0 }
    ],
    "logs": ["正在计算 RSI(14): 处理 3000/5000 只股票..."]
  }
}
```

**完成消息**：
```json
{
  "topic": "dag.execution.completed",
  "data": {
    "execution_id": "exec_20260629_001",
    "flow_id": 123,
    "status": "completed",
    "duration_ms": 15000,
    "node_summary": {
      "total": 5,
      "completed": 5,
      "failed": 0
    }
  }
}
```


## 五、前端监控页面设计

### 5.1 页面路由与布局

**路由**：`/dag/monitor`

**整体布局**：

```
┌──────────────────────────────────────────────────────────────────┐
│  流程监控  [刷新] [筛选] [搜索]                                  │
├──────────────────────────────────────────────────────────────────┤
│  统计卡片                                                        │
│  ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐              │
│  │ 运行中   │ │ 今日完成 │ │ 今日失败 │ │ 总执行   │              │
│  │ 3        │ │ 12      │ │ 1       │ │ 256     │              │
│  └─────────┘ └─────────┘ └─────────┘ └─────────┘              │
├──────────────────────────────────────────────────────────────────┤
│  筛选栏：状态筛选 | 流程名称筛选 | 触发方式筛选 | 时间范围     │
├──────────────────────────────────────────────────────────────────┤
│  执行列表（可展开）                                              │
│  ┌─────────────────────────────────────────────────────────────┐ │
│  │ ✅ 2026-06-29 18:30  每日数据更新  v5  已完成  15.2s  展开 │ │
│  │   └─ 展开后显示可视化DAG流程图（见下方）                   │ │
│  ├─────────────────────────────────────────────────────────────┤ │
│  │ 🔄 2026-06-29 19:00  特征重算     v3  运行中  8.3s   展开 │ │
│  │   └─ 展开后显示可视化DAG流程图（节点实时着色）             │ │
│  ├─────────────────────────────────────────────────────────────┤ │
│  │ ❌ 2026-06-29 17:00  模型训练     v2  失败    2.1s   展开 │ │
│  │   └─ 展开后显示可视化DAG流程图（失败节点标红）             │ │
│  └─────────────────────────────────────────────────────────────┘ │
├──────────────────────────────────────────────────────────────────┤
│  分页：上一页  1  2  3  ...  10  下一页                         │
└──────────────────────────────────────────────────────────────────┘
```

### 5.2 展开后的DAG可视化流程图

当用户点击行首的“展开”按钮时，显示该次执行的DAG流程图：

```
┌──────────────────────────────────────────────────────────────────┐
│  执行详情：每日数据更新 ｜ 执行ID：exec_20260629_001 ｜ v5      │
│  状态：🟢 已完成 ｜ 耗时：15.2s ｜ 触发：定时(Cron)             │
├──────────────────────────────────────────────────────────────────┤
│  DAG流程图                                                     │
│                                                                 │
│   ┌──────────┐     ┌──────────┐     ┌──────────┐              │
│   │ 📥下载    │────▶│ 🧮特征   │────▶│ 🤖训练   │              │
│   │ 个股行情   │     │ 偏离度    │     │ 模型A    │              │
│   │ ✅ 已完成  │     │ ✅ 已完成 │     │ ✅ 已完成 │              │
│   └──────────┘     └──────────┘     └──────────┘              │
│         │                                                     │
│         ▼                                                     │
│   ┌──────────┐     ┌──────────┐                               │
│   │ 📥下载    │────▶│ 📊报告   │                               │
│   │ 基本面    │     │ 生成报表  │                               │
│   │ ✅ 已完成 │     │ ✅ 已完成 │                               │
│   └──────────┘     └──────────┘                               │
│                                                                 │
│  图例：✅ 已完成  🔄 执行中  ⏳ 等待中  ❌ 失败  ⏹ 已取消     │
├──────────────────────────────────────────────────────────────────┤
│  节点详情（点击节点后展开）                                      │
│  节点名称：特征计算 - 偏离度                                     │
│  状态：✅ 已完成 ｜ 耗时：6.3s ｜ 处理：5000/5000 只股票         │
│  内部子图：                                                     │
│   ┌──────────┐     ┌──────────┐     ┌──────────┐              │
│   │ MA(5)    │────▶│ 偏离度   │     │ 目标特征 │              │
│   │ ✅ 已完成 │     │ ✅ 已完成 │     │ ✅ 已完成 │              │
│   └──────────┘     └──────────┘     └──────────┘              │
│  日志：                                                         │
│  2026-06-29 18:30:05  开始计算 MA(5)...                        │
│  2026-06-29 18:30:08  MA(5) 完成 (5000 行)                     │
│  2026-06-29 18:30:08  开始计算偏离度...                         │
│  2026-06-29 18:30:11  偏离度 完成 (5000 行)                     │
└──────────────────────────────────────────────────────────────────┘
```

### 5.3 WebSocket实时更新逻辑

前端连接WebSocket后，订阅 `dag.execution.*` 主题：

```javascript
// 伪代码
ws.subscribe('dag.execution.*');

ws.onMessage((msg) => {
  switch(msg.topic) {
    case 'dag.execution.started':
      // 在列表顶部插入新记录，状态为运行中
      addExecutionToList(msg.data);
      break;
      
    case 'dag.execution.progress':
      // 更新列表中对应行的进度条
      updateExecutionProgress(msg.data.execution_id, msg.data);
      
      // 如果该行当前处于展开状态，更新流程图节点颜色
      if (isExpanded(msg.data.execution_id)) {
        updateFlowGraph(msg.data.execution_id, msg.data.node_status);
      }
      break;
      
    case 'dag.execution.completed':
    case 'dag.execution.failed':
      // 更新列表中对应行的状态和耗时
      updateExecutionStatus(msg.data.execution_id, msg.data);
      break;
      
    case 'dag.execution.node.detail':
      // 用户点击展开节点时，填充节点内部子图和日志
      renderNodeDetail(msg.data);
      break;
  }
});
```


## 六、API接口设计

### 6.1 执行管理接口

| 方法 | 路径 | 功能 | 备注 |
| :--- | :--- | :--- | :--- |
| `POST` | `/api/dag/executions/run` | 手动触发执行 | 参数：`flow_id` |
| `GET` | `/api/dag/executions` | 获取执行历史列表 | 支持分页、状态筛选、流程名搜索 |
| `GET` | `/api/dag/executions/{execution_id}` | 获取执行详情 | 含完整节点状态 |
| `GET` | `/api/dag/executions/{execution_id}/graph` | 获取执行时的DAG图（含状态着色） | 用于前端渲染流程图 |
| `POST` | `/api/dag/executions/{execution_id}/cancel` | 取消正在执行的流程 | 仅当状态为 `running` 或 `pending` |
| `GET` | `/api/dag/executions/statistics` | 获取执行统计概览 | 运行中/今日完成/失败/总数 |

### 6.2 进度回调接口（供业务模块调用）

| 方法 | 路径 | 功能 | 备注 |
| :--- | :--- | :--- | :--- |
| `POST` | `/api/internal/callback/progress/{execution_id}/{node_id}` | 接收业务模块的进度回调 | 内部接口，不对外暴露 |
| `POST` | `/api/internal/callback/status/{execution_id}/{node_id}` | 接收业务模块的状态回写 | 内部接口，不对外暴露 |

### 6.3 WebSocket连接接口

| 路径 | 功能 | 备注 |
| :--- | :--- | :--- |
| `ws://api.kdao.com/ws` | WebSocket连接端点 | 连接后订阅 `dag.execution.*` 主题 |


## 七、错误处理与重试策略

### 7.1 节点失败处理

| 失败类型 | 处理策略 |
| :--- | :--- |
| **业务模块调用超时** | 重试3次，间隔5s/10s/20s（指数退避） |
| **业务模块返回错误** | 记录错误信息，标记节点失败，继续执行下游节点（若配置允许） |
| **进度回调失败** | 记录日志但不中断执行，最终状态回写兜底 |
| **网络中断** | 重试3次，若仍失败则标记节点失败 |

### 7.2 整体失败处理

| 场景 | 处理策略 |
| :--- | :--- |
| **关键节点失败** | 整体标记为 `failed`，终止执行，推送失败通知 |
| **非关键节点失败** | 标记节点为 `failed`，继续执行其他节点，最终状态为 `partial` |
| **系统异常（OOM等）** | 捕获异常，标记整体为 `failed`，记录错误堆栈 |


## 八、数据库索引建议

| 表名 | 索引字段 | 说明 |
| :--- | :--- | :--- |
| `dag_executions` | `flow_id` | 按流程查询执行记录 |
| `dag_executions` | `status` | 按状态筛选（运行中/已完成） |
| `dag_executions` | `trigger_type` | 按触发方式筛选 |
| `dag_executions` | `started_at` | 按时间范围查询 |
| `dag_executions` | `(flow_id, started_at DESC)` | 流程详情页的最近执行列表 |


## 九、实施建议（优先级拆分）

| 阶段 | 功能范围 | 预估工作量 |
| :--- | :--- | :--- |
| **Phase 1（MVP）** | 执行实例表 + 手动触发接口 + 基础执行引擎（顺序执行，无并行） + 执行列表页 | 5~6 人天 |
| **Phase 2（并行执行）** | 拓扑排序 + 并行节点执行 + 状态管理 + 执行详情页（含流程图） | 4~5 人天 |
| **Phase 3（实时推送）** | WebSocket进度推送 + 节点展开详情 + 内部子图展示 | 3~4 人天 |
| **Phase 4（定时触发）** | Cron调度器 + 流程监控统计卡片 + 取消功能 + 重试机制 | 3~4 人天 |


## 十、模块边界总结

| 内容 | 属于本模块 | 属于其他模块 |
| :--- | :--- | :--- |
| 执行实例的创建与管理 | ✅ | — |
| 流程定义的解析与拓扑排序 | ✅ | — |
| 节点调用（调用业务模块 `/dag/execute`） | ✅ | — |
| 进度回调的接收与转发 | ✅ | — |
| WebSocket实时推送 | ✅（调用通道模块） | — |
| 执行历史的持久化与查询 | ✅ | — |
| Cron定时触发 | ✅ | — |
| 流程定义的存储与管理 | — | DAG流程管理模块 |
| 节点类型的注册与管理 | — | 节点注册中心 |
| 节点的业务逻辑执行 | — | 各业务模块（下载/特征等） |
| WebSocket连接管理 | — | WebSocket通道模块 |


## 十一、与各模块的协作关系速查

| 协作方 | 调用方向 | 用途 |
| :--- | :--- | :--- |
| **DAG流程管理模块** | 本模块 → 流程管理模块 | 读取流程定义（`flow_graph`、`node_configs`） |
| **节点注册中心** | 本模块 → 节点注册中心 | 查询节点类型的服务地址（`api_base_url`） |
| **业务模块**（下载/特征等） | 本模块 → 业务模块 | 调用 `/dag/execute` 执行节点 |
| **业务模块**（下载/特征等） | 业务模块 → 本模块 | 回调 `progress_callback_url` 推送进度 |
| **WebSocket通道模块** | 本模块 → 通道模块 | 推送执行进度给前端 |


*文档版本：v1.0*  
*发布日期：2026-06-29*  
*编制人：K道量化平台 架构组*  
*状态：✅ 可交付开发*