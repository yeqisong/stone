# Agent 问答页 v2 优化：开发与测试报告

- 分支：`feat/agent-qa-v2`
- 日期：2026-11-10
- 范围：`app/agent/*`、`app/api/agent.py`、`app/db/schema.py`、`app/llm_runtime.py`、`web-v2` 问答页与详情页入口
- 原则：每个功能点先自测再进入下一个；不改变既有工具权限模型（仍为单用户本地系统）

---

## 1. 交付清单（按三批）

### 第一批：体验止血

| 功能点 | 实现 | 关键位置 |
|---|---|---|
| LLM 流式输出 | `stream=True`，逐 delta 发 `assistant_delta`；支持 `reasoning_content`/`reasoning` 发 `reasoning_delta`；保留非流式客户端兼容分支（测试替身/老网关） | `app/agent/runtime.py` |
| 前端增量渲染 | 40ms 合批刷新 + 光标动画；最终 `assistant` 事件校准全文；思考过程折叠展示 | `web-v2/src/components/AgentView.vue` |
| 图片持久化 | 工具结果图片写入 `agent_messages.meta.images`（相对路径）；回放时由 API 重新签发短时 URL | `runtime.py`、`api/agent.py`、`agent/media.py` |
| 停止贯通 | 停止事件 + `sandbox.terminate_session()` 杀进程组；流式循环每 chunk 检查停止；前端等 `stopped` 事件收尾，8s 兜底断开 | `runtime.py`、`sandbox.py`、`AgentView.vue` |
| 用量记账 | 记录 prompt/completion/total tokens；无 usage 时粗略估算并标记 `estimated`；回答底部展示模型/步数/耗时/token | `runtime.py`、`AgentView.vue` |
| 工具卡一致 | 实时与回放统一为最多 20KB 结果；SQL 结果保留 `columns/rows` 结构，按体积裁剪；新增 `已截断` 徽标与 CSV 下载 | `tools.py`、`AgentView.vue` |

### 第二批：能力补齐

| 功能点 | 实现 | 关键位置 |
|---|---|---|
| 上下文治理 | 按用户轮裁剪（最近 10 轮）；旧轮工具结果压缩为一行摘要；60k 字符预算；超 60 条消息后台压缩为会话 `memo`，后续只带 memo + 最近消息 | `runtime.py` |
| 消息编辑/重答/回滚 | `POST /sessions/{sid}/rewind` 删除指定消息及之后内容；前端用户气泡「编辑/重发」、回答「重答」 | `runtime.py`、`api/agent.py`、`AgentView.vue` |
| 会话分支 | `POST /sessions/{sid}/branch` 复制截至某条消息的上下文为新会话 | 同上 |
| 附件上传 | `POST /sessions/{sid}/upload` 原始字节流（不新增 python-multipart 依赖），落 `uploads/`，文件名清洗 + 20MB 上限 + 防重名；系统提示告知模型用 pandas 读取 | `sandbox.py`、`api/agent.py`、`runtime.py` |
| 剪贴板截图 | 输入框 Ctrl+V 检测 `clipboardData` 中的 `image/*`，自动命名为 `paste-<时间戳>-<序号>.<ext>` 并上传为附件；纯文本粘贴不受影响 | `AgentView.vue` |
| 多模态读图 | 图片附件按 OpenAI `image_url` content parts 直接送模型；长边 >1600px 等比缩放，PNG 截图优先保留 PNG；最近 2 轮带图，旧轮次只留文件名省 token；网关不支持视觉时自动降级纯文本并提示 | `runtime.py` |
| 高阶工具 | 新增 `get_dag_run_status`、`model_performance`、`portfolio_summary`、`recent_signals`；TABLE_DOCS 从 23 表扩到 40 表 | `tools.py` |
| 数据新鲜度 | 系统提示注入最新个股日线/指数日线/资讯时间；数据落后时要求模型主动说明 | `runtime.py` |
| SQL 治理 | `LIMIT n OFFSET m` 识别；只读事务显式结束；`search_news`/`stock_overview` 等补 `statement_timeout`；超限裁剪保留结构 | `tools.py` |

### 第三批：产品化

| 功能点 | 实现 | 关键位置 |
|---|---|---|
| 会话管理 | 标题+正文搜索、置顶、归档/恢复、置顶排序、草稿按会话持久化、断线轮询追平 | `runtime.py`、`api/agent.py`、`AgentView.vue` |
| 自动标题 | 首轮先截断占位；后台 LLM 生成 ≤12 字标题（失败保持占位） | `runtime.py` |
| 技能体验 | 索引上限 40→120 并提示未列出数量；修复描述解析优先级 bug；GitHub 预览/确认复用同一次浅克隆（10 分钟缓存）；编辑未保存提示 | `skills.py`、`AgentView.vue` |
| SSE 心跳/恢复 | 同步生成器搬到工作线程，主协程每 15s 发 `: ping`；新增 `/sessions/{sid}/status`，页面刷新后轮询追平 | `api/agent.py`、`AgentView.vue` |
| 反馈闭环 | `agent_feedback` 表；👍/👎（👎 可填原因）；差评列表可回看问题并跳转会话 | `schema.py`、`runtime.py`、`api/agent.py`、`AgentView.vue` |
| 跨页问 AI | 详情页「问 AI」→ 预填股票代码与名称到问答页（nav store 的 `agentSeed`） | `DetailView.vue`、`nav.js`、`AgentView.vue` |
| 沙箱加固 | 优先 bubblewrap：根只读、仅工作区可写、`--unshare-all` 无网络；bwrap 不可用回退普通子进程；rlimit 增加 FSIZE/NPROC/CORE | `sandbox.py`、`_child.py` |
| 签名图片 URL | `/files/...` 支持短时签名 `sig`，消息回放重新签发；保留 `?token=` 兼容旧链接 | `agent/media.py`、`api/agent.py` |
| 步数预算 | 无硬上限时第 41 步提示、第 120 步兜底终止；`/info` 暴露 soft/hard steps | `runtime.py`、`api/agent.py` |

---

## 2. 数据库变更

启动 `init_db` 自动幂等迁移（本地库已实际执行）：

```sql
ALTER TABLE agent_sessions ADD COLUMN IF NOT EXISTS model VARCHAR(64);
ALTER TABLE agent_sessions ADD COLUMN IF NOT EXISTS memo TEXT;
ALTER TABLE agent_sessions ADD COLUMN IF NOT EXISTS memo_until BIGINT DEFAULT 0;
ALTER TABLE agent_sessions ADD COLUMN IF NOT EXISTS pinned BOOLEAN DEFAULT false;
CREATE TABLE IF NOT EXISTS agent_feedback (...);
```

`agent_feedback`：`session_id/message_id/rating/comment/question/created_at`。删除会话级联删除反馈。

---

## 3. 测试结果

### 3.1 自动化测试

| 范围 | 结果 |
|---|---|
| `pytest tests/unit` 全量 | **315 passed** |
| `pytest tests/integration` 全量 | **7 passed** |
| Agent 专项（既有 + 新增） | **59 passed** |
| 前端 `npm run build` | **构建成功**（仅既有 chunk 体积告警） |

新增测试文件：

- `tests/unit/test_agent_v2.py`（13 项）：流式 delta/usage/reasoning、停止中断流、图片 meta 持久化与签名、沙箱进程组终止、上下文按轮裁剪与工具结果压缩、SQL 结果裁剪、签名 URL、上传名清洗/大小限制、新工具注册、新鲜度提示。
- `tests/unit/test_agent_sessions.py`（6 项）：会话搜索/置顶/模型、分支与回滚、反馈、memo 压缩、SSE 包装器、运行状态。

### 3.2 真实链路冒烟（本地后端 + 真实模型）

| 场景 | 结果 |
|---|---|
| 流式对话 | 收到 `start → reasoning_delta ×N → assistant → done`；落库 `usage: prompt 4557 / completion 30 / total 4587` |
| 附件上传 | `POST /upload?name=attsmoke.csv` 返回 200，用户消息 meta 记录 `attachments` |
| 沙箱画图 | `run_python`（bwrap）生成 `sine.png`，`tool_result.images` 带 `sig`；无 Token 直接 GET 返回 **200 / 20547 bytes / PNG 634×354** |
| 停止长任务 | `run_python: time.sleep(60)` 执行中 POST `/stop`，**0s 内**收到 `tool_result → stopped`，进程组被杀，流结束 |
| SQL 结构保留 | 500 行查询按体积裁剪为 184 行，保留 `columns/rows`，JSON 11.2KB，`truncated=true`；`LIMIT 3 OFFSET 2` 未被二次追加 LIMIT |
| 新高阶工具 | `portfolio_summary` / `recent_signals` / `model_performance` / `get_dag_run_status` 均返回真实库数据 |
| 后端重启 | `init_db` 幂等迁移无 error/traceback，`/health=200`，会话列表接口正常 |
| 浏览器粘贴截图 | Playwright 在输入框派发带 `image/png` 的 paste 事件，自动出现 `paste-<ts>-1.png` 附件 chip，上传文件落 `uploads/` |
| 多模态读图 | 上传含 `600519 SINCE 09:30 VOLUME SPIKE` 的 PNG 并提问「图片里写了什么」，模型回答原文 `600519 SINCE 09:30 VOLUME SPIKE`，无降级 notice |

---

## 4. 兼容性与回滚

- 旧网关不支持流式：运行时检测返回对象是否含 `choices`，自动走非流式路径。
- 旧图片链接：`/files/...` 仍接受 `?token=` 或 Bearer 头。
- 关闭 bwrap：`AGENT_SANDBOX=none`（退回 rlimit 约束的子进程）。
- 关闭压缩：调整 `runtime.COMPACT_TRIGGER`（默认 60）。
- 回滚代码：本分支未提交，直接 `git checkout main` 即可；数据库新增列/表为向后兼容增量。

---

## 5. 已知限制与后续建议

1. **同一标签页同时只跑一个会话**：服务端按会话加锁、不同会话可并行，但前端一次只维护一个 SSE 流；跨会话并行目前靠多标签页。若需要单页多流，需要把 `runSid/liveBlock` 改为按会话的 Map。
2. **bwrap 依赖内核命名空间**：WSL/部分容器内核可能禁用 user namespace；不可用时自动退回普通子进程，仍受 rlimit 限制但无网络隔离。
3. **附件读取在内存完成**：20MB 上限内可接受；更大文件应改分块落盘。
4. **视觉输入依赖网关能力**：当前模型可读图；若换成纯文本模型，首次报错会自动降级纯文本并提示。也可用 `AGENT_VISION=0` 强制关闭。历史图片只重传最近 2 轮，更早轮次需要用户重发。
5. **memo 压缩与 LLM 标题会额外调用模型**：均在后台/首轮触发，失败静默降级。
6. **反馈只落库、未自动生成评测集**：下一步可把差评 `question` 导出为固定回归用例，跑 `stream_agent` 做夜间冒烟。
7. **Markdown 渲染仍是轻量自研版**：不支持链接、斜体、LaTeX；代码块无语法高亮。已保证先转义再渲染，未引入 XSS 面。

---

## 6. 改动文件

```
app/agent/runtime.py       流式/用量/停止/上下文/会话管理/反馈/压缩
app/agent/sandbox.py       bwrap、进程组停止、上传、rlimit
app/agent/_child.py        FSIZE/NPROC/CORE 限制
app/agent/tools.py         TABLE_DOCS、SQL 治理、4 个高阶工具、结构化截断
app/agent/skills.py        索引上限、解析修复、导入缓存
app/agent/media.py         新增：签名图片 URL
app/api/agent.py           上传/反馈/分支/回滚/状态/心跳 SSE/签名校验
app/db/schema.py           agent 会话增强列 + agent_feedback + 幂等迁移
app/llm_runtime.py         可选模型清单 models
web-v2/src/components/AgentView.vue   问答页 v2
web-v2/src/components/DetailView.vue  「问 AI」入口
web-v2/src/stores/nav.js   agentSeed / askAgent / takeAgentSeed
tests/unit/test_agent_v2.py        新增 13 项
tests/unit/test_agent_sessions.py  新增 6 项
```
