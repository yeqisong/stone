# 一、特征计算模块DAG节点规范适配文档

## Feature Compute Module - DAG Node Contract Implementation Specification


## 一、模块概述

### 1.1 模块定位

特征计算模块是KEPL语言体系的 **“执行层”** ，同时也是DAG流程编排体系中的 **“可编排节点”** 。在完成原有特征计算职责的基础上，本模块需要实现DAG节点合约规范（Node Contract），使自身可以被DAG管理模块识别、配置、调用，并在执行过程中向调度引擎汇报层级化进度。

### 1.2 双重职责

| 职责 | 说明 | 对应接口 |
| :--- | :--- | :--- |
| **业务职责** | 解析KEPL公式，计算特征值并写入宽表 | `compute_daily` / `compute_range` |
| **节点合约职责** | 向DAG系统暴露元数据、执行入口和进度信息 | `/dag/node-meta` / `/dag/execute` / ProgressCallback |

### 1.3 与DAG模块的协作关系

```
┌─────────────────────────────────────────────────────────────────┐
│                      DAG流程编排与节点注册中心                  │
│  1. 通过 /dag/node-meta 获取节点元数据（含内部依赖子图）       │
│  2. 通过 /dag/execute 触发执行                                 │
│  3. 通过 ProgressCallback 接收层级化进度                       │
└─────────────────────────────┬───────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                    特征计算模块                                 │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │  DAG节点合约实现层                                      │   │
│  │  • NodeMetaController：提供节点元数据                   │   │
│  │  • ExecuteController：接收执行请求                     │   │
│  │  • ProgressAdapter：将内部进度转换为层级进度            │   │
│  └────────────────────────┬────────────────────────────────┘   │
│                           │                                    │
│  ┌────────────────────────▼────────────────────────────────┐   │
│  │  业务核心层（原有）                                      │   │
│  │  • FeatureComputeEngine.compute_daily / compute_range    │   │
│  │  • DependencyResolver + FeatureScheduler                │   │
│  └─────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
```


## 二、节点合约实现

### 2.1 元数据接口：`GET /api/feature/dag/node-meta`

返回节点的静态元数据，包括配置表单Schema和内部依赖子图结构。

**请求参数**：无（或可选传入特征名称以预填充）

**响应格式**：

```json
{
  "type_id": "feature",
  "display_name": "特征计算",
  "icon": "🧮",
  "color": "#FF9800",
  "description": "计算指定特征在指定日期范围的值，自动解析依赖关系并拓扑执行。",
  
  "config_schema": {
    "type": "object",
    "properties": {
      "feature_name": {
        "type": "string",
        "title": "特征名称",
        "description": "选择要计算的特征",
        "enum_source": "feature_list",
        "x-component": "Select",
        "x-component-props": {
          "placeholder": "请选择特征"
        }
      },
      "entity_codes": {
        "type": "array",
        "title": "实体范围",
        "description": "留空表示全量实体，支持输入多个代码",
        "items": { "type": "string" },
        "x-component": "TagsInput",
        "x-component-props": {
          "placeholder": "输入实体代码，回车添加"
        }
      },
      "date_mode": {
        "type": "string",
        "title": "日期模式",
        "enum": ["fixed_range", "dynamic_today", "dynamic_yesterday"],
        "default": "dynamic_yesterday",
        "x-component": "RadioGroup"
      },
      "start_date": {
        "type": "string",
        "title": "起始日期",
        "format": "date",
        "description": "仅当日期模式为fixed_range时生效",
        "x-component": "DatePicker",
        "x-visible": "date_mode == 'fixed_range'"
      },
      "end_date": {
        "type": "string",
        "title": "结束日期",
        "format": "date",
        "description": "仅当日期模式为fixed_range时生效",
        "x-component": "DatePicker",
        "x-visible": "date_mode == 'fixed_range'"
      },
      "force_recompute": {
        "type": "boolean",
        "title": "强制重算",
        "description": "开启后忽略已COMPUTED状态，强制重新计算",
        "default": false,
        "x-component": "Switch"
      }
    },
    "required": ["feature_name", "date_mode"]
  },
  
  "internal_subgraph": {
    "nodes": [
      { "id": "dep_1", "label": "原始数据", "type": "data", "status": "pending" },
      { "id": "dep_2", "label": "MA(5)", "type": "feature", "status": "pending" },
      { "id": "dep_3", "label": "RSI(14)", "type": "feature", "status": "pending" },
      { "id": "dep_4", "label": "偏离度", "type": "feature", "status": "pending" },
      { "id": "target", "label": "目标特征", "type": "target", "status": "pending" }
    ],
    "edges": [
      { "source": "dep_1", "target": "dep_2" },
      { "source": "dep_1", "target": "dep_3" },
      { "source": "dep_2", "target": "dep_4" },
      { "source": "dep_3", "target": "dep_4" },
      { "source": "dep_4", "target": "target" }
    ],
    "description": "目标特征依赖偏离度，偏离度依赖MA5和RSI"
  },
  
  "config_options": {
    "feature_list": ["bias_5d", "rsi_14", "ma_5", "ma_10", "alpha_factor"]
  }
}
```

**字段说明**：

| 字段 | 类型 | 说明 |
| :--- | :--- | :--- |
| `config_schema` | JSON Schema | 定义节点配置参数的结构，前端据此动态渲染配置表单 |
| `config_schema.properties.*.enum_source` | string | 特殊标记，指示前端从指定API获取下拉选项（如 `feature_list`） |
| `config_schema.properties.*.x-component` | string | 前端组件类型映射，如 `Select`、`Switch`、`DatePicker` |
| `internal_subgraph` | object | 该特征内部的依赖子图结构，用于在DAG画布上展示节点内部详情 |
| `internal_subgraph.nodes[].status` | string | 状态占位，在编排阶段统一为 `pending`，执行时更新 |
| `config_options` | object | 动态选项数据，供前端下拉框使用 |

### 2.2 执行接口：`POST /api/feature/dag/execute`

接收DAG调度引擎发起的执行请求，启动特征计算任务并返回任务标识。

**请求体**：

```json
{
  "execution_id": "exec_20260629_001",
  "flow_id": 123,
  "flow_version": 5,
  "node_id": "node_2",
  "config": {
    "feature_name": "bias_5d",
    "entity_codes": null,
    "date_mode": "dynamic_yesterday",
    "start_date": null,
    "end_date": null,
    "force_recompute": false
  },
  "context": {
    "date_range": ["2026-06-01", "2026-06-27"],
    "trigger_time": "2026-06-29T18:30:00Z",
    "timezone": "Asia/Shanghai"
  },
  "progress_callback_url": "http://dag-scheduler:8080/api/callback/progress",
  "status_callback_url": "http://dag-scheduler:8080/api/callback/status"
}
```

**字段说明**：

| 字段 | 类型 | 说明 |
| :--- | :--- | :--- |
| `execution_id` | string | 全局唯一的执行ID，用于进度关联 |
| `flow_id` / `flow_version` | int | 流程标识，用于审计追溯 |
| `node_id` | string | 节点在流程图中的唯一标识 |
| `config` | object | 用户在DAG画布上为该节点配置的参数 |
| `context.date_range` | array | 调度引擎计算出的实际日期范围（根据 `date_mode` 解析） |
| `progress_callback_url` | string | 调度引擎提供的进度接收端点 |
| `status_callback_url` | string | 调度引擎提供的状态回写端点 |

**响应格式**：

```json
{
  "execution_id": "exec_20260629_001",
  "node_id": "node_2",
  "status": "running",
  "message": "任务已启动，正在解析依赖关系...",
  "sub_tasks": [
    { "id": "dep_1", "label": "原始数据", "status": "completed" },
    { "id": "dep_2", "label": "MA(5)", "status": "pending" },
    { "id": "dep_3", "label": "RSI(14)", "status": "pending" },
    { "id": "dep_4", "label": "偏离度", "status": "pending" },
    { "id": "target", "label": "bias_5d", "status": "pending" }
  ],
  "estimated_duration_ms": 15000
}
```

### 2.3 执行接口的实现逻辑

```python
def execute(self, request: ExecuteRequest) -> ExecuteResponse:
    # 1. 解析日期模式，确定实际日期范围
    date_range = self._resolve_date_range(
        mode=request.config.date_mode,
        context=request.context,
        config=request.config
    )
    
    # 2. 获取特征依赖图，构建内部子任务列表
    dep_graph = self.resolver.resolve(request.config.feature_name)
    sub_tasks = self._build_sub_tasks(dep_graph)
    
    # 3. 启动异步任务（不阻塞DAG调度引擎）
    async_task = self._start_async_compute(
        feature_name=request.config.feature_name,
        date_range=date_range,
        entity_codes=request.config.entity_codes,
        force=request.config.force_recompute,
        execution_id=request.execution_id,
        progress_callback_url=request.progress_callback_url,
        status_callback_url=request.status_callback_url
    )
    
    # 4. 立即返回任务已启动状态
    return ExecuteResponse(
        execution_id=request.execution_id,
        status='running',
        sub_tasks=sub_tasks
    )
```

### 2.4 进度回调适配（核心：将内部进度转化为层级结构）

在执行过程中，特征计算模块需要将内部的粒度进度，转化为DAG节点合约要求的层级进度格式，并主动回调调度引擎提供的 `progress_callback_url`。

**转化映射**：

| 内部进度事件 | DAG节点合约事件 | 说明 |
| :--- | :--- | :--- |
| `on_start(total_entities, total_days)` | `sub_tasks` 初始化为 `pending` | 构建子任务列表 |
| `on_feature_start(feature_name, step, total_steps)` | 更新对应的 `sub_task.status = 'running'` | 开始计算某个依赖特征 |
| `on_feature_progress(feature_name, processed, total)` | 更新对应的 `sub_task.percent` 和 `detail` | 更新进度百分比 |
| `on_feature_complete(feature_name, rows_written)` | 更新对应的 `sub_task.status = 'completed'` | 依赖特征完成 |
| `on_chunk_complete(chunk_index, total_chunks)` | 更新 `overall_percent` | 整体进度推进 |
| `on_task_complete(result)` | 发送 `status='completed'` | 全部完成 |

**回调请求格式**：

```json
{
  "execution_id": "exec_20260629_001",
  "node_id": "node_2",
  "event": "progress",
  "timestamp": 1719480123456,
  "data": {
    "status": "running",
    "overall_percent": 65.5,
    "sub_tasks": [
      { "id": "dep_1", "label": "原始数据", "status": "completed", "percent": 100, "detail": "已加载" },
      { "id": "dep_2", "label": "MA(5)", "status": "completed", "percent": 100, "detail": "5000/5000" },
      { "id": "dep_3", "label": "RSI(14)", "status": "running", "percent": 60, "detail": "3000/5000" },
      { "id": "dep_4", "label": "偏离度", "status": "pending", "percent": 0, "detail": "等待依赖" },
      { "id": "target", "label": "bias_5d", "status": "pending", "percent": 0, "detail": "等待依赖" }
    ]
  }
}
```

**回调成功标准**：
- 调度引擎在收到回调后，返回 HTTP 200。
- 若回调失败（超时或非200响应），特征计算模块**记录日志但不中断计算**，待计算完成后通过 `status_callback_url` 回写最终状态。


## 三、模块内部改动点

### 3.1 新增文件/类

| 路径 | 职责 |
| :--- | :--- |
| `controller/DagNodeController.java` | 实现 `/dag/node-meta` 和 `/dag/execute` 接口 |
| `service/DagNodeMetaService.java` | 构建 `internal_subgraph`，从特征管理模块获取依赖信息 |
| `service/DagExecutionService.java` | 接收DAG执行请求，调用原有 `FeatureComputeEngine` |
| `callback/ProgressCallbackAdapter.java` | 将内部进度转化为DAG回调格式，调用 `progress_callback_url` |
| `dto/DagNodeMetaDto.java` | 定义节点元数据响应结构 |
| `dto/ExecuteRequestDto.java` | 定义执行请求结构 |
| `dto/ExecuteResponseDto.java` | 定义执行响应结构 |
| `dto/ProgressPayloadDto.java` | 定义层级进度结构 |

### 3.2 原有代码修改点

| 原有类 | 修改内容 | 影响范围 |
| :--- | :--- | :--- |
| `FeatureComputeEngine` | `compute_range` 方法增加 `ProgressCallback` 参数，用于输出层级进度 | **新增参数，现有调用方需适配** |
| `DependencyResolver` | 新增 `resolve_with_subgraph()` 方法，返回带节点ID的依赖图结构（用于构建 `internal_subgraph`） | 新增方法，不影响原有逻辑 |
| `FeatureScheduler` | 在调度过程中，调用 `ProgressCallback` 上报依赖级别的进度 | 增强原有回调逻辑 |


## 四、节点注册数据（种子数据）

在DAG节点注册中心中，本模块的注册信息如下：

```sql
INSERT INTO dag_node_types (
    type_id, display_name, icon, color, description,
    service_name, api_base_url,
    metadata_endpoint, execution_endpoint,
    enabled
) VALUES (
    'feature',
    '特征计算',
    '🧮',
    '#FF9800',
    '计算指定特征在指定日期范围的值，自动解析依赖关系并拓扑执行。支持日期模式：固定范围/当天/昨日。',
    'feature-service',
    'http://feature-svc:8080/api/feature',
    '/dag/node-meta',
    '/dag/execute',
    TRUE
);
```


## 五、与特征管理模块的协作（依赖子图构建）

特征计算模块在构建 `internal_subgraph` 时，需要从特征管理模块获取特征的依赖信息：

1. 调用特征管理模块的 `GET /api/features/{feature_name}/dependencies` 接口。
2. 获取递归展开的依赖列表：`["ma5", "rsi14", "bias_5d"]`。
3. 将依赖列表转化为 `internal_subgraph.nodes` 和 `internal_subgraph.edges` 结构。

**依赖子图转换规则**：
- 原始字段（如 `close`）：标记为 `type: "data"`，不放入子图节点（或放入但标记为已就绪）。
- 基础特征（如 `ma5`）：标记为 `type: "feature"`。
- 目标特征：标记为 `type: "target"`。
- 边：根据 `depends_on` 关系建立。


## 六、接口汇总表

| 接口路径 | 方法 | 用途 | 调用方 |
| :--- | :--- | :--- | :--- |
| `/api/feature/dag/node-meta` | GET | 获取节点元数据（含内部依赖子图） | DAG前端 |
| `/api/feature/dag/execute` | POST | 接收执行请求，启动计算任务 | DAG调度引擎 |
| （回调）`{progress_callback_url}` | POST | 推送层级化进度（由本模块主动调用） | 特征计算模块→DAG调度引擎 |
| （回写）`{status_callback_url}` | POST | 回写最终执行状态 | 特征计算模块→DAG调度引擎 |


## 七、与调度引擎的协作约定

| 事项 | 约定内容 |
| :--- | :--- |
| **执行超时** | 单次执行不超过 `date_range` × `entity_codes` / 1000 × 60 秒。若超过，调度引擎可主动取消任务（通过独立接口） |
| **取消机制** | 调度引擎通过 `POST /api/feature/dag/cancel/{execution_id}` 通知取消。本模块检测到取消信号后，优雅终止并回写 `status='cancelled'` |
| **幂等性** | 相同 `execution_id` 的重复执行请求，本模块返回已存在的任务状态，不重复启动 |


## 八、改动影响评估

| 影响项 | 影响程度 | 说明 |
| :--- | :--- | :--- |
| 原有 `compute_range` 接口 | 中 | 增加 `ProgressCallback` 参数，已有调用方需适配 |
| 特征管理模块依赖 | 低 | 需新增 `GET /api/features/{name}/dependencies` 接口（或复用现有接口） |
| 前端展示 | 低 | DAG前端自动适配，无需额外修改 |
| 数据库变更 | 无 | 无需新增表或字段 |


*文档版本：v1.0*  
*发布日期：2026-06-29*  
*编制人：K道量化平台 架构组*  
*状态：✅ 可交付开发*


# 二、行情下载模块DAG节点规范适配文档

## Data Download Module - DAG Node Contract Implementation Specification


## 一、模块概述

### 1.1 模块定位

行情下载模块是K道量化平台的 **“数据准备层”** ，同时也是DAG流程编排体系中的 **“可编排节点”** 。在完成原有数据下载职责的基础上，本模块需要实现DAG节点合约规范（Node Contract），使自身可以被DAG管理模块识别、配置、调用，并在执行过程中向调度引擎汇报层级化进度。

### 1.2 双重职责

| 职责 | 说明 | 对应接口 |
| :--- | :--- | :--- |
| **业务职责** | 从外部数据源下载行情数据（个股/ETF/指数/基本面）并写入存储 | `download_daily` / `download_range` |
| **节点合约职责** | 向DAG系统暴露元数据、执行入口和进度信息 | `/dag/node-meta` / `/dag/execute` / ProgressCallback |

### 1.3 与DAG模块的协作关系

```
┌─────────────────────────────────────────────────────────────────┐
│                      DAG流程编排与节点注册中心                  │
│  1. 通过 /dag/node-meta 获取节点元数据（含内部依赖子图）       │
│  2. 通过 /dag/execute 触发执行                                 │
│  3. 通过 ProgressCallback 接收层级化进度                       │
└─────────────────────────────┬───────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                    行情下载模块                                 │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │  DAG节点合约实现层                                      │   │
│  │  • NodeMetaController：提供节点元数据                   │   │
│  │  • ExecuteController：接收执行请求                     │   │
│  │  • ProgressAdapter：将内部进度转换为层级进度            │   │
│  └────────────────────────┬────────────────────────────────┘   │
│                           │                                    │
│  ┌────────────────────────▼────────────────────────────────┐   │
│  │  业务核心层（原有）                                      │   │
│  │  • DownloadEngine.download_daily / download_range        │   │
│  │  • IncrementalDetector + DataSourceRouter               │   │
│  └─────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
```


## 二、节点合约实现

### 2.1 元数据接口：`GET /api/download/dag/node-meta`

返回节点的静态元数据，包括配置表单Schema和内部依赖子图结构。

**响应格式**：

```json
{
  "type_id": "download",
  "display_name": "行情下载",
  "icon": "📥",
  "color": "#2196F3",
  "description": "从外部数据源下载行情数据（个股/ETF/指数/基本面）并写入本地存储。",
  
  "config_schema": {
    "type": "object",
    "properties": {
      "data_type": {
        "type": "string",
        "title": "数据类型",
        "enum": ["stock", "etf", "index", "fundamental"],
        "default": "stock",
        "x-component": "Select",
        "x-component-props": {
          "placeholder": "请选择数据类型"
        }
      },
      "entity_codes": {
        "type": "array",
        "title": "实体范围",
        "description": "留空表示全量实体，支持输入多个代码",
        "items": { "type": "string" },
        "x-component": "TagsInput",
        "x-component-props": {
          "placeholder": "输入实体代码，回车添加"
        }
      },
      "date_mode": {
        "type": "string",
        "title": "日期模式",
        "enum": ["fixed_range", "dynamic_today", "dynamic_yesterday"],
        "default": "dynamic_yesterday",
        "x-component": "RadioGroup"
      },
      "start_date": {
        "type": "string",
        "title": "起始日期",
        "format": "date",
        "description": "仅当日期模式为fixed_range时生效",
        "x-component": "DatePicker",
        "x-visible": "date_mode == 'fixed_range'"
      },
      "end_date": {
        "type": "string",
        "title": "结束日期",
        "format": "date",
        "description": "仅当日期模式为fixed_range时生效",
        "x-component": "DatePicker",
        "x-visible": "date_mode == 'fixed_range'"
      },
      "force": {
        "type": "boolean",
        "title": "强制覆盖",
        "description": "开启后强制重新下载，覆盖已存在的数据",
        "default": false,
        "x-component": "Switch"
      }
    },
    "required": ["data_type", "date_mode"]
  },
  
  "internal_subgraph": {
    "nodes": [
      { "id": "dep_stock", "label": "个股行情", "type": "download", "status": "pending" },
      { "id": "dep_etf", "label": "ETF行情", "type": "download", "status": "pending" },
      { "id": "dep_index", "label": "指数行情", "type": "download", "status": "pending" },
      { "id": "dep_fund", "label": "原始基本面", "type": "download", "status": "pending" },
      { "id": "target", "label": "全面数据就绪", "type": "target", "status": "pending" }
    ],
    "edges": [
      { "source": "dep_stock", "target": "target" },
      { "source": "dep_etf", "target": "target" },
      { "source": "dep_index", "target": "target" },
      { "source": "dep_fund", "target": "target" }
    ],
    "description": "全面数据需等待所有数据类型下载完成。当选择具体数据类型时，子图将自动过滤仅显示该类型。"
  },
  
  "config_options": {
    "data_type_list": ["stock", "etf", "index", "fundamental"]
  }
}
```

### 2.2 内部依赖子图的动态化

根据用户选择的 `data_type`，`internal_subgraph` 应动态调整：

| 选择的 `data_type` | 返回的 `internal_subgraph` |
| :--- | :--- |
| `stock` | 仅显示个股行情 → 目标 |
| `etf` | 仅显示ETF行情 → 目标 |
| `fundamental` | 显示原始基本面 + 衍生字段计算（如市值依赖个股行情） |
| 未选择（默认） | 显示全部四种数据类型并行 → 目标 |

**`fundamental` 类型的内部子图示例**：

```json
{
  "internal_subgraph": {
    "nodes": [
      { "id": "dep_stock_price", "label": "个股行情（依赖）", "type": "dependency", "status": "pending" },
      { "id": "dep_raw_fund", "label": "原始基本面", "type": "download", "status": "pending" },
      { "id": "calc_market_cap", "label": "计算市值", "type": "derive", "status": "pending" },
      { "id": "calc_turnover", "label": "计算换手率", "type": "derive", "status": "pending" },
      { "id": "target", "label": "基本面数据就绪", "type": "target", "status": "pending" }
    ],
    "edges": [
      { "source": "dep_stock_price", "target": "calc_market_cap" },
      { "source": "dep_raw_fund", "target": "calc_market_cap" },
      { "source": "dep_stock_price", "target": "calc_turnover" },
      { "source": "dep_raw_fund", "target": "calc_turnover" },
      { "source": "calc_market_cap", "target": "target" },
      { "source": "calc_turnover", "target": "target" }
    ],
    "description": "市值和换手率需结合个股行情和原始基本面数据计算"
  }
}
```

### 2.3 执行接口：`POST /api/download/dag/execute`

接收DAG调度引擎发起的执行请求，启动数据下载任务并返回任务标识。

**请求体**：

```json
{
  "execution_id": "exec_20260629_001",
  "flow_id": 123,
  "flow_version": 5,
  "node_id": "node_1",
  "config": {
    "data_type": "fundamental",
    "entity_codes": null,
    "date_mode": "dynamic_yesterday",
    "start_date": null,
    "end_date": null,
    "force": false
  },
  "context": {
    "date_range": ["2026-06-28", "2026-06-28"],
    "trigger_time": "2026-06-29T18:30:00Z",
    "timezone": "Asia/Shanghai"
  },
  "progress_callback_url": "http://dag-scheduler:8080/api/callback/progress",
  "status_callback_url": "http://dag-scheduler:8080/api/callback/status"
}
```

**响应格式**：

```json
{
  "execution_id": "exec_20260629_001",
  "node_id": "node_1",
  "status": "running",
  "message": "任务已启动，正在检测数据存在性...",
  "sub_tasks": [
    { "id": "dep_stock_price", "label": "个股行情", "status": "pending" },
    { "id": "dep_raw_fund", "label": "原始基本面", "status": "pending" },
    { "id": "calc_market_cap", "label": "计算市值", "status": "pending" },
    { "id": "calc_turnover", "label": "计算换手率", "status": "pending" },
    { "id": "target", "label": "基本面数据就绪", "status": "pending" }
  ],
  "estimated_duration_ms": 8000
}
```

### 2.4 执行接口的实现逻辑

```python
def execute(self, request: ExecuteRequest) -> ExecuteResponse:
    # 1. 解析日期模式，确定实际日期范围
    date_range = self._resolve_date_range(
        mode=request.config.date_mode,
        context=request.context,
        config=request.config
    )
    
    # 2. 根据数据类型构建内部子任务列表
    sub_tasks = self._build_sub_tasks(
        data_type=request.config.data_type,
        date_range=date_range
    )
    
    # 3. 检测数据存在性（增量检测）
    existing_data = self.detector.check_existing(
        data_type=request.config.data_type,
        date_range=date_range,
        entity_codes=request.config.entity_codes
    )
    
    # 4. 构建下载任务（仅下载缺失部分）
    download_plan = self._build_download_plan(
        data_type=request.config.data_type,
        date_range=date_range,
        entity_codes=request.config.entity_codes,
        existing_data=existing_data,
        force=request.config.force
    )
    
    # 5. 启动异步下载任务（不阻塞DAG调度引擎）
    async_task = self._start_async_download(
        download_plan=download_plan,
        execution_id=request.execution_id,
        progress_callback_url=request.progress_callback_url,
        status_callback_url=request.status_callback_url
    )
    
    # 6. 立即返回任务已启动状态
    return ExecuteResponse(
        execution_id=request.execution_id,
        status='running',
        sub_tasks=sub_tasks
    )
```

### 2.5 进度回调适配（核心：将内部进度转化为层级结构）

在执行过程中，下载模块需要将内部进度转化为DAG节点合约要求的层级进度格式，并主动回调调度引擎提供的 `progress_callback_url`。

**转化映射**：

| 内部进度事件 | DAG节点合约事件 | 说明 |
| :--- | :--- | :--- |
| `on_start(total_entities, total_days)` | `sub_tasks` 初始化为 `pending` | 构建子任务列表 |
| `on_data_type_start(data_type, total_entities)` | 更新对应的 `sub_task.status = 'running'` | 开始下载某类数据 |
| `on_entity_progress(entity_code, processed, total)` | 更新对应的 `sub_task.percent` 和 `detail` | 更新进度百分比 |
| `on_data_type_complete(data_type, success_count)` | 更新对应的 `sub_task.status = 'completed'` | 某类数据下载完成 |
| `on_derive_start(field_name)` | 更新对应的 `sub_task.status = 'running'` | 开始计算衍生字段 |
| `on_derive_complete(field_name)` | 更新对应的 `sub_task.status = 'completed'` | 衍生字段计算完成 |
| `on_task_complete(result)` | 发送 `status='completed'` | 全部完成 |

**回调请求格式**（以基本面为例）：

```json
{
  "execution_id": "exec_20260629_001",
  "node_id": "node_1",
  "event": "progress",
  "timestamp": 1719480123456,
  "data": {
    "status": "running",
    "overall_percent": 55.0,
    "sub_tasks": [
      { "id": "dep_stock_price", "label": "个股行情", "status": "completed", "percent": 100, "detail": "5000/5000" },
      { "id": "dep_raw_fund", "label": "原始基本面", "status": "running", "percent": 65, "detail": "3250/5000" },
      { "id": "calc_market_cap", "label": "计算市值", "status": "pending", "percent": 0, "detail": "等待依赖" },
      { "id": "calc_turnover", "label": "计算换手率", "status": "pending", "percent": 0, "detail": "等待依赖" },
      { "id": "target", "label": "基本面数据就绪", "status": "pending", "percent": 0, "detail": "等待依赖" }
    ]
  }
}
```


## 三、模块内部改动点

### 3.1 新增文件/类

| 路径 | 职责 |
| :--- | :--- |
| `controller/DagNodeController.java` | 实现 `/dag/node-meta` 和 `/dag/execute` 接口 |
| `service/DagNodeMetaService.java` | 构建 `internal_subgraph`，根据 `data_type` 动态生成依赖子图 |
| `service/DagExecutionService.java` | 接收DAG执行请求，调用原有 `DownloadEngine` |
| `callback/ProgressCallbackAdapter.java` | 将内部进度转化为DAG回调格式，调用 `progress_callback_url` |
| `dto/DagNodeMetaDto.java` | 定义节点元数据响应结构 |
| `dto/ExecuteRequestDto.java` | 定义执行请求结构 |
| `dto/ExecuteResponseDto.java` | 定义执行响应结构 |

### 3.2 原有代码修改点

| 原有类 | 修改内容 | 影响范围 |
| :--- | :--- | :--- |
| `DownloadEngine` | `download_range` 方法增加 `ProgressCallback` 参数，用于输出层级进度 | **新增参数，现有调用方需适配** |
| `DownloadScheduler` | 在下载过程中，调用 `ProgressCallback` 报告数据类型级别的进度 | 增强原有回调逻辑 |
| `FundamentalStrategy` | 衍生字段计算阶段，调用 `ProgressCallback` 报告子任务进度 | 新增回调调用 |


## 四、节点注册数据（种子数据）

```sql
INSERT INTO dag_node_types (
    type_id, display_name, icon, color, description,
    service_name, api_base_url,
    metadata_endpoint, execution_endpoint,
    enabled
) VALUES (
    'download',
    '行情下载',
    '📥',
    '#2196F3',
    '从外部数据源下载行情数据（个股/ETF/指数/基本面）并写入本地存储。支持增量下载和强制覆盖。',
    'download-service',
    'http://download-svc:8080/api/download',
    '/dag/node-meta',
    '/dag/execute',
    TRUE
);
```


## 五、接口汇总表

| 接口路径 | 方法 | 用途 | 调用方 |
| :--- | :--- | :--- | :--- |
| `/api/download/dag/node-meta` | GET | 获取节点元数据（含内部依赖子图） | DAG前端 |
| `/api/download/dag/execute` | POST | 接收执行请求，启动下载任务 | DAG调度引擎 |
| （回调）`{progress_callback_url}` | POST | 推送层级化进度 | 下载模块→DAG调度引擎 |
| （回写）`{status_callback_url}` | POST | 回写最终执行状态 | 下载模块→DAG调度引擎 |


## 六、与调度引擎的协作约定

| 事项 | 约定内容 |
| :--- | :--- |
| **执行超时** | 单次下载不超过 `date_range` × `entity_codes` / 100 × 60 秒（受数据源限频影响）。调度引擎可根据实际情况配置超时时间 |
| **取消机制** | 调度引擎通过 `POST /api/download/dag/cancel/{execution_id}` 通知取消。本模块检测到取消信号后，优雅终止并回写 `status='cancelled'` |
| **增量检测** | 本模块内部自动检测数据存在性，仅下载缺失部分。调度引擎无需关心增量逻辑 |


## 七、改动影响评估

| 影响项 | 影响程度 | 说明 |
| :--- | :--- | :--- |
| 原有 `download_range` 接口 | 中 | 增加 `ProgressCallback` 参数，已有调用方需适配 |
| 数据适配层 | 低 | 无需修改 |
| 数据存储层 | 低 | 无需修改 |
| 前端展示 | 低 | DAG前端自动适配，无需额外修改 |
| 数据库变更 | 无 | 无需新增表或字段 |


## 八、总结

特征计算模块和行情下载模块的DAG节点规范适配，遵循统一的设计模式：

| 维度 | 特征计算模块 | 行情下载模块 |
| :--- | :--- | :--- |
| **元数据接口** | 返回特征依赖子图（从特征管理模块获取） | 返回数据类型依赖子图（根据 `data_type` 动态生成） |
| **执行接口** | 调用 `compute_range` | 调用 `download_range` |
| **进度子任务** | 按特征依赖链分层（MA5 → RSI → 偏离度 → 目标） | 按数据类型分层（个股→ETF→指数→基本面→衍生计算） |
| **注册类型ID** | `feature` | `download` |

两者共同构成DAG流程编排体系中的 **“数据准备-特征计算”** 双核心节点，为上层DAG流程提供可编排、可观测的原子能力。


*文档版本：v1.0*  
*发布日期：2026-06-29*  
*编制人：K道量化平台 架构组*  
*状态：✅ 可交付开发，两个模块均已完成DAG节点合约适配*