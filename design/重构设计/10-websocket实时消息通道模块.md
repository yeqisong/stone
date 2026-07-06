# K道量化平台 - WebSocket 实时消息通道模块设计方案

---

## 一、模块概述

### 1.1 模块定位

WebSocket 消息通道模块是 K道量化平台的 **“实时状态同步层”**。它作为前后端之间的中立数据管道，专门负责将后端耗时任务（如特征计算、模型训练、回测执行）的**进度、状态、中间结果与最终数据**，实时、可靠地推送给前端，供前端进行动态展示。

### 1.2 核心设计原则

| 原则 | 说明 |
| :--- | :--- |
| **业务无状态** | 通道不解析消息内容，不理解业务语义，只负责传输。消息格式由前后端业务层自行约定。 |
| **单向推送为主** | 通道的核心职责是 **后端 → 前端** 的数据推送。前端不必通过此通道发送业务数据（但可发送ACK确认或连接控制信号）。 |
| **连接独立** | 前端与后端的业务服务（如特征计算服务）之间没有直接的HTTP长连接，全部通过此通道中转。 |
| **可扩展** | 支持多个后端服务同时向同一个前端连接推送消息。 |

### 1.3 架构位置

```
┌─────────────┐      WebSocket       ┌─────────────────────────────┐
│   前端      │ ◄─────────────────── │  WebSocket 消息通道服务      │
│   (浏览器)  │                       │  (独立部署，无业务逻辑)      │
└─────────────┘                       └─────────────┬───────────────┘
                                                    │ 内部消息队列
                                                    │ (Redis Pub/Sub)
                                                    ▼
                                    ┌─────────────────────────────┐
                                    │     后端业务服务集群        │
                                    │  ┌─────────────────────┐   │
                                    │  │ 特征计算服务        │   │
                                    │  ├─────────────────────┤   │
                                    │  │ 模型训练服务        │   │
                                    │  ├─────────────────────┤   │
                                    │  │ 回测执行服务        │   │
                                    │  └─────────────────────┘   │
                                    └─────────────────────────────┘
```

---

## 二、通信协议设计

### 2.1 消息格式规范

为了保持通道中立，所有消息统一使用 JSON 格式，包含以下固定外层字段（由通道透传，不解析）：

| 外层字段 | 类型 | 必填 | 说明 |
| :--- | :--- | :--- | :--- |
| `topic` | String | ✅ | 消息主题，用于前端路由分发。由业务方自行定义，通道不解析。约定格式：`{服务名}.{任务类型}`，如 `feature.calc.progress`、`model.train.log`、`backtest.result` |
| `task_id` | String | ✅ | 任务唯一标识。业务方生成，前端用此 ID 关联 UI 组件（如某个特定的计算进度条） |
| `event` | String | ✅ | 事件类型，推荐枚举：`started` / `progress` / `log` / `partial_result` / `completed` / `failed` / `cancelled` |
| `timestamp` | Long | ✅ | 消息产生时间戳（毫秒级），由业务方填充 |
| `data` | Object | ✅ | 业务数据体，通道完全不解析，透传给前端。业务方自定义内部结构 |
| `_meta` | Object | ❌ | 可选元数据，如 `{"retry": 0}`，供通道内部调试使用 |

**示例消息（特征计算进度）**：

```json
{
  "topic": "feature.calc.progress",
  "task_id": "feat_calc_20260627_001",
  "event": "progress",
  "timestamp": 1719480123456,
  "data": {
    "feature_name": "bias_5d",
    "target_entity": "stock",
    "processed": 3500,
    "total": 5000,
    "percent": 70.0,
    "estimated_remaining_ms": 12000
  },
  "_meta": {}
}
```

### 2.2 连接建立流程

1. 前端发起 WebSocket 连接请求：`wss://api.kdao.com/ws`
2. 连接建立后，通道服务返回 **连接确认消息**（固定格式）：
   ```json
   {
     "type": "connection_established",
     "connection_id": "conn_abc123",
     "timestamp": 1719480123000
   }
   ```
3. 前后端后续通信均通过此 WebSocket 连接进行。

### 2.3 心跳机制

| 方向 | 间隔 | 消息格式 | 超时处理 |
| :--- | :--- | :--- | :--- |
| 前端 → 后端 | 每 30 秒 | `{"type": "ping", "timestamp": 1719480123000}` | 通道响应 `pong`，若 60 秒无响应，服务端主动断开 |
| 后端 → 前端 | 每 30 秒 | `{"type": "ping"}` | 前端若 60 秒未收到任何消息（含 ping），主动重连 |

---

## 三、后端集成规范

### 3.1 后端推送接口

后端业务服务不直接与 WebSocket 通道通信，而是通过 **内部消息中间件（Redis Pub/Sub）** 将消息发布给通道服务，由通道服务统一推送给前端。

**后端推送标准流程**：

```python
# 伪代码：后端业务服务发布消息
import redis

redis_client = redis.Redis(host='redis.internal', port=6379)

# 发布消息到通道
redis_client.publish(
    'ws_channel:push',  # 固定频道
    json.dumps({
        "topic": "feature.calc.progress",
        "task_id": self.task_id,
        "event": "progress",
        "timestamp": now_ms(),
        "data": {
            "feature_name": self.feature_name,
            "processed": self.processed,
            "total": self.total,
            "percent": self.processed / self.total * 100,
            "estimated_remaining_ms": self.estimate_time()
        }
    })
)
```

**关键说明**：
- 通道服务订阅 Redis 频道 `ws_channel:push`。
- 后端业务服务只负责向 Redis 发布消息，不感知前端连接状态。
- 通道服务负责将消息路由到对应的前端连接（根据 `task_id` 或连接分组策略）。

### 3.2 任务标识符约定

为了确保消息能正确关联到前端 UI，业务方需遵循以下约定：

| 任务类型 | `task_id` 生成规则 | 示例 |
| :--- | :--- | :--- |
| 特征计算 | `feat_calc_{feature_name}_{timestamp}` | `feat_calc_bias_5d_20260627_1430` |
| 模型训练 | `model_train_{model_version}_{timestamp}` | `model_train_v2.1_20260627_1500` |
| 回测执行 | `backtest_{config_hash}_{timestamp}` | `backtest_a3f9c2_20260627_1600` |

**前端关联规则**：前端在启动任务时，会先通过 HTTP API 获取该任务的 `task_id`，然后建立 WebSocket 连接并监听该 `task_id` 相关的消息。

### 3.3 连接分组（推送路由）

对于需要指定接收者（即某特定前端实例）的消息，使用 `connection_id` 或 `user_id` 进行路由：

```json
{
  "topic": "feature.calc.progress",
  "task_id": "feat_calc_001",
  "target_connection_id": "conn_abc123",  // 可选：指定接收者
  "event": "progress",
  ...
}
```

- 如果未指定 `target_connection_id`，通道服务将广播给所有已连接的客户端（适用于系统级通知，不推荐用于任务进度）。
- **推荐实践**：业务服务应先通过用户会话（Session）或 Token 获取前端的 `connection_id`，再定向推送。

---

## 四、前端集成规范

### 4.1 连接建立与重连

```javascript
// 伪代码：前端 WebSocket 客户端
class WSChannel {
  connect() {
    this.ws = new WebSocket('wss://api.kdao.com/ws');
    
    this.ws.onmessage = (event) => {
      const msg = JSON.parse(event.data);
      
      // 1. 处理系统级消息（连接控制）
      if (msg.type === 'connection_established') {
        this.connectionId = msg.connection_id;
        return;
      }
      if (msg.type === 'pong') {
        // 重置心跳计时器
        return;
      }
      
      // 2. 业务消息路由
      const topic = msg.topic;
      const taskId = msg.task_id;
      
      // 根据 topic 和 task_id 找到对应的 UI 组件/回调
      const handler = this.handlers[topic]?.[taskId];
      if (handler) {
        handler(msg);
      }
    };
  }
}
```

### 4.2 进度展示组件与消息的绑定

前端页面在发起耗时任务（如点击“计算特征”）时，会建立任务与 UI 的关联：

```javascript
// 伪代码：前端创建任务并绑定监听
const taskId = 'feat_calc_bias_5d_20260627_1430';

// 1. 创建进度条 UI
const progressBar = new ProgressBar(taskId);

// 2. 注册消息回调
wsChannel.registerHandler('feature.calc.progress', taskId, (msg) => {
  if (msg.event === 'started') {
    progressBar.start(msg.data.total);
  } else if (msg.event === 'progress') {
    progressBar.update(msg.data.processed, msg.data.total);
  } else if (msg.event === 'completed') {
    progressBar.done(msg.data.result_preview);
  } else if (msg.event === 'failed') {
    progressBar.fail(msg.data.error_message);
  }
});

// 3. 发起 HTTP 请求启动任务（非 WebSocket 通信）
// 前端调用后端 HTTP API，该 API 会立即返回 task_id 并异步启动任务
fetch('/api/features/calc', {
  method: 'POST',
  body: JSON.stringify({ feature_name: 'bias_5d' })
}).then(res => res.json()).then(data => {
  // 后端返回的 task_id 与前端注册的一致
  console.log('任务已启动:', data.task_id);
});
```

### 4.3 断线重连与任务恢复

当 WebSocket 连接意外断开时，前端需要自动重连并恢复对任务的监听：

| 步骤 | 实现 |
| :--- | :--- |
| **1. 检测断开** | 监听 `onclose` 事件，记录断开时间 |
| **2. 重连策略** | 指数退避重连（1s → 2s → 4s → 8s → 最大 60s），避免频繁重连打崩服务器 |
| **3. 恢复状态** | 重连成功后，前端向服务端发送 **`sync` 消息**，告知当前正在监听的任务列表：<br>`{"type": "sync", "task_ids": ["feat_calc_001", "model_train_002"]}`<br>后端业务服务根据此信息，重新发送这些任务的当前进度和状态。 |
| **4. 超时兜底** | 若 120 秒内重连失败，前端提示用户“连接中断，请刷新页面重新查询任务状态”，并引导用户通过 HTTP 接口查询任务最终结果。 |

---

## 五、服务端实现要点

### 5.1 与后端业务服务的集成方案

由于通道服务本身是无状态的，它需要从后端业务服务获取“当前有哪些正在进行的任务及其状态”。推荐使用 **Redis Pub/Sub + Redis 状态缓存** 的组合方案：

| 组件 | 作用 |
| :--- | :--- |
| **Redis Pub/Sub** | 后端业务服务发布实时消息（进度、日志），通道服务订阅并实时推送给前端。 |
| **Redis Hash（状态缓存）** | 后端业务服务在任务启动时写入任务的初始状态，任务进行中定期更新，任务完成后保留最终状态（设置 TTL 为 24 小时）。通道服务在收到前端的 `sync` 请求时，从 Redis 读取当前状态返回给前端。 |

**状态缓存的 Key 设计**：
```
Key:   task:status:{task_id}
Value: {
  "status": "running",           // running / completed / failed
  "percent": 70.0,
  "processed": 3500,
  "total": 5000,
  "last_update": 1719480123456
}
TTL: 86400 秒（24小时）
```

### 5.2 连接管理（连接池）

通道服务需要管理所有活跃的 WebSocket 连接：

| 管理功能 | 实现方式 |
| :--- | :--- |
| **连接池** | 内存中维护 `Map<connection_id, WebSocketSession>`，定期清理僵尸连接 |
| **连接认证** | 连接建立时携带 JWT Token 或 API Key，校验身份后绑定 `user_id` |
| **连接隔离** | 不同用户的连接互相隔离，推送时按 `user_id` 过滤 |
| **连接数限制** | 单用户最大连接数限制为 5 个（防止恶意开大量连接消耗资源） |

---

## 六、消息类型设计建议（业务方自行扩展）

通道本身不定义具体的 `data` 内容，但为了前端能统一处理，建议后端业务服务遵循以下推荐的事件类型与 `data` 结构规范：

| `event` 类型 | 推荐的 `data` 结构 | 说明 |
| :--- | :--- | :--- |
| `started` | `{"feature_name": "bias_5d", "total": 5000, "message": "开始计算..."}` | 任务启动，告知总量 |
| `progress` | `{"processed": 3500, "total": 5000, "percent": 70.0, "estimated_remaining_ms": 12000}` | 进度更新，含预估剩余时间 |
| `log` | `{"level": "INFO", "message": "正在计算股票 000001...", "timestamp": 1719480123456}` | 日志输出，用于调试 |
| `partial_result` | `{"sample_data": [{"date": "2026-06-27", "value": 12.34}]}` | 中间结果预览（可选） |
| `completed` | `{"result_preview": {...}, "total_time_ms": 15000}` | 任务完成，附带结果摘要 |
| `failed` | `{"error_code": "DATA_SOURCE_ERROR", "error_message": "连接数据源超时"}` | 任务失败，附错误码与描述 |
| `cancelled` | `{"reason": "用户手动取消"}` | 任务被取消 |

---

## 七、错误处理与异常场景

| 异常场景 | 系统行为 |
| :--- | :--- |
| **推送失败（后端→通道）** | 通道服务收到消息后，若无法推送给前端（如连接已断开），将消息丢弃，不做重试（由业务服务的状态缓存兜底）。 |
| **连接断开导致消息丢失** | 前端重连后发送 `sync` 请求，从后端状态缓存获取任务的最新状态，补全断线期间错过的消息。 |
| **心跳超时** | 服务端主动断开连接，前端触发重连流程。 |
| **单用户连接数超限** | 拒绝新连接，并返回 HTTP 429（Too Many Requests）错误码。 |
| **消息体过大（> 1MB）** | 通道服务主动拒绝转发，返回错误码给后端，建议业务方将大结果拆分为多条消息或使用对象存储（OSS）传递。 |

---

## 八、部署与监控建议

| 维度 | 建议 |
| :--- | :--- |
| **部署方式** | 独立微服务，无状态可水平扩展，支持 Kubernetes 多副本部署 |
| **端口** | 独立端口（如 8081），与 HTTP API 端口分离 |
| **连接数预估** | 按同时在线用户数 × 1.5 倍估算连接数（含重连间隙的冗余） |
| **监控指标** | • 当前活跃连接数<br>• 消息吞吐量（每秒消息数）<br>• 平均推送延迟（从后端发布到前端收到）<br>• 连接失败率 / 重连率 |
| **日志** | 记录连接建立/关闭、消息发布失败、心跳超时等关键事件，用于故障排查 |

---

## 九、模块边界总结

| 内容 | 属于本模块 | 属于业务服务 |
| :--- | :--- | :--- |
| WebSocket 连接的建立、维护、断开 | ✅ | ❌ |
| 消息的接收与透传推送 | ✅ | ❌ |
| 心跳维持与重连逻辑 | ✅（部分） | 前端负责重连逻辑 |
| 消息的业务含义解析（如进度百分比的计算） | ❌ | ✅ |
| 任务状态的持久化存储（状态缓存） | ❌（若要求高可用） | ✅（或独立缓存服务） |
| 任务执行过程中的业务逻辑（如实际计算特征） | ❌ | ✅ |
| 断线重连时的任务状态同步（`sync` 响应） | ❌（仅转发） | ✅（业务服务提供状态数据） |

---

*文档版本：v1.0*  
*发布日期：2026-06-27*  
*编制人：K道量化平台 架构组*  
*状态：✅ 可交付开发*