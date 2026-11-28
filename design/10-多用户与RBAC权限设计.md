# 10 — 多用户与 RBAC 权限设计

> 版本：v1.1（P1–P6 已实施，待验收）
> 日期：2026-11-22
> 范围：单库多用户改造、功能节点 RBAC、数据范围隔离、ABAC 策略钩子、迁移与验证方案
> 原则：**共享数据全租户共用；个人持仓/自选/交易/告警按用户隔离；权限默认拒绝；管理员默认拥有全部非 public 权限节点**
>
> 实施分支：`feat/multiuser-rbac`（独立工作树 `stone-multiuser`）。截至 2026-11-22，P1–P6 代码、单元/集成测试与双用户冒烟已完成；生产库迁移、备份/回滚演练与用户验收尚未执行。§0.2 表格中“未进入开发/待执行”是评审时的历史状态，实施结果见 §0.3。

---

## 0. 文档使用说明

### 0.1 读者

- 后端开发：第 4~8 章（权限模型、数据模型、业务规则、API）。
- 前端开发：第 5、9 章（功能节点、前端权限）。
- 运维/DBA：第 7、10 章（迁移、回滚、验证）。
- 产品/评审：第 1~3、6、11 章（目标、边界、业务规则、原型、风险）。

### 0.2 评审状态

| 轮次 | 日期 | 范围 | 结论 | 记录 |
|---|---|---|---|---|
| R1 | 2026-11-21 | 现状边界与隐式单用户耦合 | 通过，15 项已回写 | §16.1 |
| R2 | 2026-11-21 | 权限节点/角色矩阵/数据范围 | 通过，12 项已回写 | §16.2 |
| R3 | 2026-11-21 | 字段/迁移/回滚/写入路径 | 通过，24 项已回写 | §16.3 |
| R4 | 2026-11-21 | 路由/权限/ABAC/Agent | 通过，20 项已回写 | §16.4 |
| R5 | 2026-11-21 | 文档内部一致性 + 第二轮复核 | 通过，28 项已回写 | §16.5 |
| S1 | 2026-11-21 | 自洽审查 1：导航/权限节点 | 通过，19 个 tab / 4 个后台 tab / 0 冲突 | §16.6 |
| S2 | 2026-11-21 | 自洽审查 2：API/数据范围 | 通过，后台节点仅 admin / 0 越权 | §16.6 |
| S3 | 2026-11-21 | 自洽审查 3：原型/交互 | 通过，\| 分隔/移动端/403 已定义 | §16.6 |
| R6 | 开发前 | 代码修复 + 路由覆盖 + 跨用户测试 | 待执行（C-01~C-25） | §13.4 / §14 |

> **重要**：上表“已回写”只表示**设计文档**已修正，不代表代码已修复。§13.4 的 C-01~C-25 仍是现状代码缺陷；未全部关闭前不得上线多用户，也不得把设计关闭当作安全验收通过。

> 评审结论必须落到本文档；未关闭的“待确认”项不得进入开发。

### 0.3 实施结果（2026-11-22）

| 阶段 | 实施内容 | 自测结果 |
|---|---|---|
| P1 认证/用户/RBAC | `users/roles/permissions/role_permissions/user_roles/user_permission_overrides/support_sessions/audit_log`；bcrypt；JWT `sub/uid/username/token_version/perm_version/jti`；登录失败锁定；`/api/me*`；用户/角色/审计/支持会话 API；集中路由权限注册表 + 启动覆盖自检 | 登录/禁用/改密/旧 token 失效/权限覆盖/最后管理员保护通过 |
| P2 个人数据 schema | 持仓/自选/交割单/盘中/现金/计划/日志/告警/个人配置/个人信号/Agent 会话加 `user_id`；复合主键与唯一索引；`v_focus_pool(user_id)` + `v_focus_pool_all`；旧数据归种子管理员 | `init_db` 幂等；双用户数据互不可见 |
| P3 个人 API | portfolio/watch/trades/risk/radar/news/stock/buy_check/execution_quality 全部按 `principal.data_user_id` 查询与写入 | 跨用户读写/删除/告警确认均被拒绝 |
| P4 监测/信号/WS | 盘中 watcher 一次行情拉取、按用户规则评估/冷却/每日上限；个人卖出信号 `user_signal_history`；WS 按 owner 过滤，模型/DAG/系统消息按权限 | 双用户并行 tick、个人信号、WS 隔离通过 |
| P5 前端 | auth store 权限缓存；导航按权限过滤；后台组 `[用户][角色权限][审计][系统设置]` 放在【数据】后并用 `\|` 分隔；`y/z/h/o` 页面；403/空状态；改密/登录 | `npm run build` 通过；管理员后台导航与 viewer 重定向冒烟通过 |
| P6 Agent/飞书 | 会话 owner；沙箱 `data/agent_ws/<uid>/<sid>`；技能 `data/skills/u<uid>/`（全局 `data/skills/global/` 只读）；管理员工具过滤与执行二次校验；`query_sql` 共享表白名单（个人/敏感表、非 public schema、危险函数拒绝）；飞书 `open_id` 一次性码绑定、按用户历史/持仓/偏好、卡片回调按 open_id 落库 | 双用户 Agent 会话/技能/工具门；SQL 白名单（portfolio/watch/risk/users 等拒绝）；未绑定飞书拒绝；绑定/换绑/卡片写持仓/解绑全链路通过 |

**测试**：`pytest tests/unit tests/integration` → 609 passed / 4 skipped（跳过项为测试库未回补的市场数据）；`tests/e2e` 因当前机器 Playwright 浏览器版本与依赖不匹配未执行，改用脚本化 Playwright 冒烟验证登录、后台导航与飞书绑定弹窗。

**与设计的实现差异**：
1. 个人技能目录使用 `data/skills/u<uid>/`（避免纯数字技能名与用户 id 目录冲突）；管理员 GitHub 导入写入 `data/skills/global/`，所有用户只读。
2. 飞书绑定采用 Web 端生成 6 位一次性码（10 分钟、只存 sha256、一码一用）；已绑定的 open_id 发送绑定命令只提示先解绑，不进入 LLM。
3. `admin` 角色显式授予全部非 public 权限节点（`agent.exec`/`agent.sql.raw` 等也包含），满足“admin 默认拥有所有功能权限”。

### 0.4 术语

| 术语 | 含义 |
|---|---|
| 共享域 | 全市场/全系统共享、无用户归属的数据：行情、特征、模型、买入信号、树图、新闻原文/摘要、宏观、可转债、DAG、配额 |
| 个人域 | 有明确用户归属的数据：持仓、自选分组、交割单、实时持仓、现金、交易计划/日志、个人告警、个人配置、Agent 会话 |
| 功能节点 | 一个可授权的功能单元：菜单、页面、操作、敏感动作；对应权限码 |
| 数据范围 | 对“有归属资源”的行级可见范围：`self` / `team`（预留）/ `all` |
| ABAC 钩子 | 在 RBAC 判定之后执行的动态条件检查；只用于少量上下文条件，不做通用策略引擎 |
| 模型账本 | `paper_portfolio` / `paper_positions` / `paper_trades`，属于模型版本的影子账户，不是任何用户的实盘 |
| 支持模式 | 管理员在明确授权、填写理由、限时、留痕的前提下查看某个用户个人数据 |

---

## 1. 目标、范围与非目标

### 1.1 目标

1. **多用户登录**：用户表、密码哈希、JWT、禁用/改密/角色变更即时生效。
2. **共享数据共用**：行情、特征、模型、训练、回测、买入信号、树图、新闻、宏观、可转债、DAG、配额不按用户复制。
3. **个人数据隔离**：每个用户只能读写自己的持仓、自选分组、交割单、实时持仓、现金、交易计划/日志、个人告警、个人配置、Agent 会话。
4. **监测隔离**：盘中提醒、雷达、买点候选、日频风控、告警推送按用户独立评估与投递。
5. **功能节点 RBAC**：菜单/页面/操作/敏感动作全部有权限码；角色可组合；以后可给用户开不同权限。
6. **数据范围**：个人资源默认 `self`；只有显式授权 `all` 且具备支持模式权限时，才能跨用户查看；所有跨用户访问留痕。
7. **ABAC 钩子**：对时间窗、配额、导出上限、二次认证、支持模式理由等少量动态条件做策略检查。
8. **可迁移、可回滚、可验证**：现有单用户数据完整归到种子管理员；迁移幂等；有验证 SQL 与跨用户隔离测试。

### 1.2 非目标（本期不做）

- 多租户 SaaS 的计费、套餐、租户隔离、租户级自定义策略。
- 用户之间共享持仓/自选/告警（未来如需要，另做 ReBAC 关系模型）。
- 团队/部门数据范围（`team` 只预留字段，不实现）。
- 字段级加密、HSM、SSO/OIDC、LDAP。
- 多活/多 worker 的分布式权限缓存（本期单 uvicorn worker；扩容前再上 Redis）。
- 移动端/小程序权限适配。
- 管理员“模拟登录”任意用户（不做；只做只读支持模式）。

### 1.3 前置假设

| # | 假设 | 影响 |
|---|---|---|
| A1 | 用户规模 2~50，单部署、单 PostgreSQL、单 uvicorn worker | 可用进程内缓存；无需分库分表 |
| A2 | 行情/特征/模型/新闻采集仍是一份全局数据 | 共享域不改，成本不随用户数线性增长 |
| A3 | 每个用户一个默认组合（可含多个券商资金账号，但合并为一个组合视图） | 不做多组合/多账户切换 |
| A4 | 管理员可以管理全局数据，但默认不能看用户个人数据 | 支持模式单独授权 + 审计 |
| A5 | 现有单用户数据全部归到种子管理员 | 迁移不丢数据 |
| A6 | 盘中监测仍是单进程后台循环 | 采用“一次行情拉取 + 多用户规则评估” |
| A7 | 管理员的 dbexplorer/原始 SQL 只能看共享/系统表；个人数据必须走支持会话专用 API；`agent.exec` 沙箱禁网/禁 DB socket | DB 浏览/Agent SQL 表白名单；避免“admin 默认全知” |

### 1.4 成功标准（验收口径）

| # | 标准 | 验证方式 |
|---|---|---|
| S1 | 用户 A 无法读取/修改用户 B 的持仓、自选、交割单、告警、Agent 会话 | 跨用户隔离测试矩阵 §14.2 |
| S2 | 用户 A 的盘中提醒只推送给 A；B 的提醒不出现 | WS 双客户端测试 §14.2/§14.9 |
| S3 | 普通用户无法访问模型训练/DAG/补数/DB 浏览/LLM Key | 权限矩阵测试 §14.3 |
| S4 | 管理员默认看不到用户个人数据；支持模式需理由、限时、审计 | 支持模式测试 §14.4 |
| S5 | 现有单用户数据迁移后 100% 归属种子管理员，无 NULL owner | 迁移验证 SQL §7.7 |
| S6 | 所有 `/api` 路由都有权限码或 public 标记 | 路由覆盖自检 §10.3 |
| S7 | 权限变更后下一次请求即生效，旧权限不残留 | 缓存失效测试 §14.9 |

---

## 2. 现状边界与改造风险

### 2.1 三层数据域

```
┌──────────────────────────── 共享域（全租户共用，管理员写）────────────────────────────┐
│ daily_quote / index_daily_quote / stock_master / stock_fundamentals / feature_values     │
│ features / functions / model_versions / training_trials / backtest_* / model_health      │
│ signal_history(buy) / stock_treemap_* / news_item / news_summary / macro_* / cb_*        │
│ dag_* / tushare_quota / paper_positions / paper_trades                                  │
└──────────────────────────────────────────┬───────────────────────────────────────────────┘
                                           │ 只读（有功能权限即可）
        ┌──────────────────────────────────┼──────────────────────────────────┐
        │                                  │                                  │
┌───────▼─────────┐              ┌─────────▼─────────┐              ┌─────────▼─────────┐
│ 用户 A 个人域    │              │ 用户 B 个人域      │              │ 系统/模型派生      │
│ portfolio       │              │ portfolio         │              │ paper_portfolio   │
│ watch_groups    │              │ watch_groups      │              │ model_health      │
│ trade_*         │              │ trade_*           │              │ 模型观察告警       │
│ risk_alerts     │              │ risk_alerts       │              └───────────────────┘
│ user_config     │              │ user_config       │
│ agent_sessions  │              │ agent_sessions    │
└───────┬─────────┘              └─────────┬─────────┘
        │                                  │
        └──────────────┬───────────────────┘
                       ▼
             按用户运行的监测/分析
        盘中提醒 · 雷达 · 买点 · 日频风控 · 新闻过滤
```

### 2.2 现状中最容易漏掉的隐式单用户耦合

| # | 位置 | 现状 | 多用户后果 | 处理 |
|---|---|---|---|---|
| C1 | `app/api/portfolio.py` | 全部读写无 `user_id` | 所有人共享持仓 | 个人表加 owner，查询加 helper |
| C2 | `app/api/watch.py` | 分组全局；`group_name UNIQUE` | 分组串户、重名冲突 | `(user_id, group_name)` 唯一 |
| C3 | `app/trade_slip.py` / `app/trade_intraday.py` | 交割单、现金、实时持仓全局 | 盘中监测按别人的持仓发提醒 | 全链路 `user_id` |
| C4 | `app/intraday_watch.py` | 全局 `_ctx`、`_load_pool`、`_load_live`、`_persist` | 关注池/成本/冷却/告警全部串户 | 单行情循环 + 每用户评估 |
| C5 | `app/db/schema.py` `v_focus_pool` | `portfolio ∪ watch_group_items` 无用户 | 一个用户关注池影响所有人 | 带 `user_id` 的个人视图 + 采集并集视图 |
| C6 | `app/buy_check.py` / `app/api/stock.py` | 雷达/买点读全局关注池与全局实时持仓 | 推荐别人持仓的票、剔除别人卖出的票 | 按当前用户 |
| C7 | `crawler/news/collect.py` | 采集范围读全局关注池 | 采集范围被单个用户影响 | 采集用并集，读取按用户 |
| C8 | `scripts/pipeline.py:2017` | 卖出信号读全局 `portfolio` | 卖出信号串户、污染模型健康 | 全局买入保留；卖出写个人信号表 |
| C9 | `app/risk.py` | 日频风控评 `paper_positions`（模型账本） | 用户以为在监控自己的持仓 | 新增用户实盘风控；模型告警单独标记 |
| C10 | `app/api/status.py:522` | `risk_alerts` 全局广播给所有 WS | 告警泄露 | WS 连接绑定用户，按 owner 推送 |
| C11 | `app/agent/tools.py:134` | `tool_query_sql` 可查任意表 | 普通用户可查别人数据 | 非管理员禁用原始 SQL |
| C12 | `app/agent/runtime.py:338` | `agent_sessions` 无 owner | 任意登录用户可访问/删除他人会话 | 会话 owner + 运行时校验 |
| C13 | `app/feishu/*` | 忽略 `open_id/chat_id` | 所有飞书用户共享上下文/持仓 | open_id 绑定用户 |
| C14 | `app/api/settings.py` | 全局配置、LLM Key；部分 GET 无鉴权 | 一个用户改所有人配置/泄露 Key | 个人/系统设置拆分 + admin-only |
| C15 | `app/api/models.py` | `paper_portfolio` 是模型账本 | 用户误以为“我的模拟持仓” | 前端明确标注“模型纸面” |
| C16 | 进程内缓存 | `_watch_cache`、`_risk_heat_cache`、`_ctx`、雷达缓存全局 | 用户 A 看到 B 的缓存 | 缓存 key 加 `user_id` |
| C17 | 认证 | 单账号环境变量、默认密钥可回退 | 多用户暴露即风险 | users 表 + 强制密钥 + 全局鉴权 |
| C18 | 路由 | 大量 GET 未保护 | 匿名/越权读取 | 默认拒绝 + 路由覆盖自检 |

### 2.3 改造风险分级

| 风险 | 概率 | 影响 | 等级 | 缓解 |
|---|---|---|---|---|
| 漏改后台链路导致串户 | 高 | 高 | **P0** | 以“数据产生方”而不是“页面”为单位做清单；跨用户测试 |
| Agent 原始 SQL 泄露 | 中 | 高 | **P0** | 非管理员禁用；管理员审计 |
| WS 告警泄露 | 高 | 高 | **P0** | 每连接绑定用户；按 owner 过滤 |
| 权限默认放行/路由漏配 | 高 | 高 | **P0** | 全局默认拒绝 + 启动自检 + 测试 |
| 迁移约束切换失败 | 中 | 高 | **P0** | 备份、测试库演练、分步迁移、验证 SQL |
| 盘中循环 N 倍外部请求 | 中 | 中 | **P1** | 并集拉取 + 每用户内存评估 |
| 个人规则覆盖全局信号 | 中 | 中 | **P1** | `global_preference` 保持全局；个人只做展示层 |
| 权限缓存不失效 | 中 | 中 | **P1** | `perm_version` + 短 TTL |
| 管理员默认看个人数据 | 中 | 中 | **P1** | 支持模式单独授权 + 审计 |
| 角色爆炸/难维护 | 中 | 低 | **P2** | 角色模板 + override，不做一人一角色 |

---

## 3. 设计决策记录（ADR）

| # | 决策 | 选项 | 结论 | 理由 |
|---|---|---|---|---|
| D1 | 隔离方式 | 分库 / schema-per-user / 单库行级 | **单库行级 `user_id`** | 共享域与个人域同库；外键/视图/采集简单；用户量小 |
| D2 | 权限主干 | RBAC / ABAC / 混合 | **RBAC + 数据范围 + ABAC 钩子** | 功能与归属是静态的；动态条件很少 |
| D3 | 数据范围 | 权限码内嵌 / 独立 scope | **独立 scope：self/team/all** | 避免 `view.self`/`view.all` 权限码爆炸 |
| D4 | 管理页 | 所有登录用户 / 管理员 | **功能权限控制，默认仅 admin/operator** | 训练/DAG/补数/DB 浏览风险高 |
| D5 | 管理员看个人数据 | 自动 / 显式授权 | **默认不能；支持模式显式授权 + 审计** | 隐私与最小权限 |
| D6 | 全局买入信号 | 全局 / 按用户偏好生成 | **全局一份** | 计算共享；避免 N 倍预测；保证模型健康口径 |
| D7 | 卖出信号 | 写全局 `signal_history` / 个人表 | **个人 `user_signal_history`** | 不污染模型健康；按用户隔离 |
| D8 | 纸面组合 | 用户模拟盘 / 模型账本 | **模型账本，全局只读** | 现有语义；避免与实盘混淆 |
| D9 | 新闻采集范围 | 每用户各采 / 并集采集 | **并集采集，读取按用户过滤** | 控制外部请求与 LLM 成本 |
| D10 | 盘中监测 | 每用户独立循环 / 单循环多用户 | **单行情循环 + 每用户规则评估** | 行情只拉一次；规则按用户隔离 |
| D11 | 配置拆分 | 全部个人 / 全部全局 / 拆分 | **个人规则进 `user_config`；系统配置留 `strategy_config`** | 现有全局键继续被后台任务读取 |
| D12 | 权限缓存 | 每请求查库 / JWT 塞权限 / 缓存 | **缓存 + `perm_version`** | 用户量小但请求频繁；变更即时生效 |
| D13 | 角色模型 | 固定角色 / 自由角色 / 模板+override | **系统角色模板 + 用户 override** | 可开不同权限，又避免角色爆炸 |
| D14 | 支持模式 | 永久 admin 全知 / 限时只读 | **限时只读 + 理由 + 审计** | 运维需要与隐私平衡 |
| D15 | 多 worker | 现在支持 / 以后支持 | **一期单 worker，明确约束** | 现有 TaskManager/WS/缓存均为进程内 |

---

## 4. 目标架构与请求链路

### 4.1 系统分区

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                              浏览器 / 飞书 / 桌面浮窗                          │
└───────────────────────────────┬──────────────────────────────────────────────┘
                                │ HTTPS / WSS
┌───────────────────────────────▼──────────────────────────────────────────────┐
│ FastAPI 接入层                                                                │
│  1) 认证：JWT → CurrentUser                                                   │
│  2) 功能权限：route permission → require_perm()                               │
│  3) 数据范围：scope self/all → personal_scope()                               │
│  4) ABAC 钩子：时间/配额/二次认证/支持模式理由                                  │
│  5) 审计：敏感动作/跨用户访问                                                   │
└───────────────┬───────────────────────────────┬──────────────────────────────┘
                │                               │
┌───────────────▼──────────────┐  ┌─────────────▼──────────────────────────────┐
│ 共享域服务                    │  │ 个人域服务                                  │
│ 行情/特征/模型/信号/树图/新闻  │  │ 持仓/自选/交割单/实时持仓/告警/配置/会话      │
│ 只读 + 管理端写                │  │ owner 过滤 + 个人写                          │
└───────────────┬──────────────┘  └─────────────┬──────────────────────────────┘
                │                               │
┌───────────────▼───────────────────────────────▼──────────────────────────────┐
│ PostgreSQL 单库：共享表 + 个人表(user_id) + 权限表 + 审计表                    │
└──────────────────────────────────────────────────────────────────────────────┘
```

### 4.2 请求鉴权链路

```
Request
  │
  ├─ 1. 提取 Bearer Token
  │      └─ 无/无效 → 401
  │
  ├─ 2. 解析 JWT：user_id, token_version, perm_version
  │      └─ 用户不存在/禁用/token_version 不符 → 401
  │
  ├─ 3. 加载 Principal（缓存）
  │      ├─ roles
  │      ├─ effective permissions（role grants + allow override - deny override）
  │      └─ scopes / conditions
  │
  ├─ 4. 路由权限码
  │      ├─ public → 放行
  │      ├─ 无权限码 → 启动自检失败（生产拒绝启动）
  │      └─ 无授权 → 403
  │
  ├─ 5. ABAC 钩子（按权限码注册）
  │      ├─ 条件失败 → 403 / 429
  │      └─ 条件通过 → 继续
  │
  ├─ 6. 数据范围
  │      ├─ scope=self → 查询强制 user_id = principal.data_user_id
  │      ├─ support_mode → 主 JWT + 支持令牌；只读；资源级 support_readable
  │      └─ 全局资源 → 无行级 scope
  │
  ├─ 7. 业务处理
  │
  └─ 8. 审计（敏感动作 / 支持模式 / 权限变更）
```

### 4.3 权限评估流程图

```
                    ┌─────────────────────┐
                    │ 需要权限码 P 的请求   │
                    └──────────┬──────────┘
                               ▼
                 ┌──────────────────────────┐
                 │ Principal 是否有 P 的授权？│
                 └──────┬───────────────┬───┘
                        │否             │是
                        ▼               ▼
                    ┌───────┐   ┌──────────────────┐
                    │ 403   │   │ 是否有 deny 覆盖？│
                    └───────┘   └────┬─────────┬───┘
                                     │是       │否
                                     ▼         ▼
                                 ┌───────┐  ┌──────────────────┐
                                 │ 403   │  │ ABAC 条件是否通过？│
                                 └───────┘  └────┬─────────┬───┘
                                                 │否       │是
                                                 ▼         ▼
                                             ┌───────┐  ┌──────────────┐
                                             │403/429│  │ 计算有效 scope │
                                             └───────┘  └──────┬───────┘
                                                               ▼
                                                    ┌─────────────────────────┐
                                                    │ self → 强制 owner 过滤    │
                                                    │ support → 目标用户 + 只读 │
                                                    └─────────────────────────┘
```

---

## 5. 认证与用户生命周期

### 5.1 用户状态机

```
创建(active) ──禁用──> disabled ──启用──> active
     │                    │
     │                    └── 旧 token 失效（token_version+1）
     │
     └── 改密 ──> active（token_version+1，强制重新登录）
```

### 5.2 认证规则

| 规则 | 内容 |
|---|---|
| AUTH-01 | 密码使用 bcrypt/argon2 哈希；禁止明文、禁止环境变量直接比对 |
| AUTH-02 | 密码最少 8 位，建议 12 位；禁止与用户名相同；禁止默认密码 |
| AUTH-03 | 登录失败按 IP + 用户名限流；连续失败 N 次锁定 M 分钟（可配置） |
| AUTH-04 | JWT 只放 `sub(user_id)`、`username`、`token_version`、`perm_version`、`exp`、`iat`、`jti`；不放权限列表 |
| AUTH-05 | 禁用用户、改密、强制下线：`token_version+1`，旧 token 立即 401 |
| AUTH-06 | 权限/角色变更：`perm_version+1`，不强制重新登录，但 Principal 缓存失效 |
| AUTH-07 | 生产环境必须显式设置 `APP_SECRET_KEY`；默认值/长度不足拒绝启动 |
| AUTH-08 | 登出：前端清 token + 断开 WS；后端可选 `jti` 黑名单（本期不做，靠短过期 + token_version） |
| AUTH-09 | WS 鉴权使用短期 ticket（推荐）或 `Sec-WebSocket-Protocol`；不再把 JWT 放 URL query |
| AUTH-10 | 飞书 webhook 必须校验 Encrypt Key 签名或 Verification Token；两者都未配置时生产拒绝接收；拒绝超时重放并按事件去重 |

### 5.3 用户生命周期操作

| 操作 | 接口 | 权限 | 规则 |
|---|---|---|---|
| 登录 | `POST /api/login` | public | 限流；返回 token + user + permissions |
| 当前用户 | `GET /api/me` | 登录 | 返回 id/username/display_name/roles/permissions/scopes |
| 修改自己密码 | `POST /api/me/password` | 登录 | 校验旧密码；改后 token_version+1 |
| 二次认证 | `POST /api/me/reauth` | 登录 | 签发短期 step-up 票据；用于 `model.approve` |
| 个人设置 | `GET/PUT /api/me/settings` | 登录 | 只读写 `user_config` 个人键；不碰全局 `strategy_config` |
| 管理员创建用户 | `POST /api/admin/users` | `system.user.manage` | 用户名唯一；初始密码一次性；默认角色 |
| 管理员禁用/启用 | `PATCH /api/admin/users/{id}/status` | `system.user.manage` | 不能禁用最后一个 active admin |
| 管理员重置密码 | `POST /api/admin/users/{id}/reset-password` | `system.user.manage` | 返回一次性密码；token_version+1 |
| 管理员分配角色 | `PUT /api/admin/users/{id}/roles` | `system.user.manage` | 不能授予自己没有的权限（防提权）；支持 `expires_at` |
| 管理员用户 override | `/api/admin/users/{id}/permissions` | `system.user.manage` | allow/deny + scope/conditions/expires_at；写审计 |
| 管理员用户列表 | `GET /api/admin/users` | `system.user.manage` | 分页；不返回密码哈希 |
| 支持会话 | `/api/admin/support/sessions` | `admin.support.view` | 目标用户/理由/TTL；只读；撤销；审计 |
| 角色管理 | `/api/admin/roles` | `system.role.manage` | 系统角色不可删；权限变更 bump perm_version |
| 权限目录 | `GET /api/admin/permissions` | `system.role.manage` | 返回功能树；前端角色编辑器使用 |
| 审计查询 | `GET /api/admin/audit` | `system.audit.view` | 只读；不可删除 |

---

## 6. RBAC 权限模型

### 6.1 模型定义

```
User ──< UserRole >── Role ──< RolePermission >── Permission
  │                      │                         │
  │                      └── data_scope            └── parent_id（功能树）
  │
  └──< UserPermissionOverride >── Permission（allow/deny + 过期时间）

Permission ──< ABAC Hook（按权限码注册）>
SupportSession（管理员 → 目标用户，限时只读，带理由）
```

四个判定对象：

| 对象 | 作用 | 关键字段 |
|---|---|---|
| User | 登录主体 | `id/username/password_hash/status/token_version/perm_version` |
| Role | 权限模板 | `code/name/is_system/data_scope` |
| Permission | 功能节点 | `code/type/parent_id/route/api_pattern` |
| Grant | 角色/用户与权限的关系 | `scope/conditions/effect/expires_at` |

**关键约束**：

1. 权限码是唯一事实源；前端菜单、后端路由、审计日志都用同一个码。
2. 没有“超级管理员绕过”。`admin` 也是一组显式授权；便于审计和最小权限。
3. `deny` 覆盖 `allow`；已过期的 override 不参与计算。
4. 个人数据默认 `self`；跨用户读取只能通过**支持会话**（见 §6.6），不能靠静态 `all` 角色。
5. 后台任务、cron、飞书回调没有登录用户时，必须显式使用“系统主体”，不能复用某个用户。

### 6.2 功能节点总表

> 类型：`menu` 菜单/页面；`action` 操作；`sensitive` 敏感动作；`data` 共享数据只读；`admin` 系统管理；`public` 公开；`system` 连接/系统节点。
> 默认角色：A=admin，O=operator，T=trader，N=analyst，V=viewer，S=support（支持会话角色）。`—` 表示默认不授予，按需组合。
> 数据范围：`self` 只能操作自己的数据；`—` 表示共享数据无行级范围。`S` 表示 `support_only=true` 的授权，只在支持会话中生效，见 §6.6。
> **约定**：节点表为节省篇幅，个人域的“默认角色”省略 admin 的 `self` 授权；admin 的个人域权限以 §6.4 矩阵为准（默认 ✔）。矩阵是唯一事实源，种子生成器从矩阵导出。

#### 6.2.1 个人交易域

| 节点码 | 名称 | 类型 | 父节点 | 前端入口 | 后端 API | 默认角色 | 数据范围 | 说明 |
|---|---|---|---|---|---|---|---|---|
| `portfolio.position.view` | 持仓查看 | menu | — | tab `p` 持仓 | `GET /api/portfolio`、`GET /api/portfolio/risk-heat`、`GET /api/portfolio/watch-window` | A/T/S | self | 只返回当前用户（或支持目标）的持仓 |
| `portfolio.position.edit` | 持仓编辑 | action | `portfolio.position.view` | 持仓页新增/编辑/删除 | `POST /api/portfolio`、`PUT/DELETE /api/portfolio/{stock_code}` | T | self | 支持模式只读，禁止写 |
| `portfolio.history.view` | 持仓变更历史 | action | `portfolio.position.view` | 持仓历史弹窗 | `GET /api/portfolio/{stock_code}/history` | A/T/S | self | 只查当前用户历史 |
| `portfolio.watch_window.launch` | 桌面浮窗 | action | `portfolio.position.view` | 浮窗按钮 | `POST /api/portfolio/watch/prepare`、`POST /api/portfolio/watch/launch` | T | self | 浮窗 token 绑定 user_id |
| `portfolio.trades.view` | 交割单/复盘查看 | menu | — | tab `t` 交易复盘 | `GET /api/portfolio/trades`、`/intraday/state`、`/intraday/quote`、`/journal`、`/reduction-plan`、`/execution-quality`、`/account-summary`、`/review`、`/batches` | A/T/S | self | 交割单是盘中监测的成本来源，必须隔离 |
| `portfolio.trades.import` | 交割单导入 | action | `portfolio.trades.view` | 导入/同步按钮 | `POST /api/portfolio/trades/import`、`POST /api/portfolio/trades/apply-positions`、`DELETE /api/portfolio/trades/batches/{batch_id}` | T | self | 导入幂等键含 user_id |
| `portfolio.trades.intraday` | 盘中临时操作 | action | `portfolio.trades.view` | 盘中报备/现金 | `POST /intraday/check`、`POST /intraday/record`、`PUT /intraday/cash`、`DELETE /intraday/{action_id}` | T | self | 只影响自己的实时持仓/现金 |
| `portfolio.trades.journal.edit` | 交易日志 | action | `portfolio.trades.view` | 盘前计划/收盘复盘 | `PUT /api/portfolio/trades/journal` | T | self | 按用户+日期 |
| `portfolio.discipline.view` | 纪律闸门查看 | action | `portfolio.trades.view` | 今日纪律卡片 | `GET /api/portfolio/trades/discipline/rules`、`GET /api/portfolio/trades/discipline/today` | T | self | 读取个人配置 |
| `portfolio.discipline.edit` | 纪律规则编辑 | action | `portfolio.discipline.view` | 纪律设置 | `PUT /api/portfolio/trades/discipline/rules` | T | self | 写入 `user_config` |
| `risk.rules.view` | 个人风控/盘中规则查看 | action | — | 风控设置/状态 | `GET /api/risk/rules`、`GET /api/risk/intraday-rules` | T | self | 只读个人规则；模型统计见 `research.signal.view` |
| `risk.rules.edit` | 个人风控/盘中规则编辑 | action | `risk.rules.view` | 规则表单 | `POST /api/risk/rules`、`POST /api/risk/intraday-rules`、`POST /api/risk/evaluate-personal` | T | self | 写 `user_config`；手动评估只影响自己 |
| `risk.alerts.view` | 个人告警查看 | action | — | 顶栏/详情页 | `GET /api/risk/alerts`（`ack=false`） | A/T/S | self | 只返回自己的告警；模型告警另见 `model.alerts.view` |
| `risk.alerts.ack` | 个人告警确认 | action | `risk.alerts.view` | 确认按钮 | `POST /api/risk/alerts/ack`（新增；旧 `GET ?ack=true` 仅兼容） | T | self | 只确认自己的告警；支持模式禁止 |
| `watch.group.view` | 自选分组查看 | menu | — | 持仓页 tab/详情页标签 | `GET /api/watch/groups`、`GET /api/watch/groups/{id}/stocks`、`GET /api/watch/stock-groups` | A/T/N/S | self | 分组名按用户唯一 |
| `watch.group.edit` | 自选分组编辑 | action | `watch.group.view` | 新建分组/加组/移除 | `POST/DELETE /api/watch/groups`、`PUT /api/watch/stock-groups` | T/N | self | 默认组不可删；成员通过 group owner 校验 |

#### 6.2.2 共享研究域

| 节点码 | 名称 | 类型 | 父节点 | 前端入口 | 后端 API | 默认角色 | 数据范围 | 说明 |
|---|---|---|---|---|---|---|---|---|
| `research.realtime.view` | 实时（盘中作战台） | menu | — | tab `rt` 实时 | 无独立端点（容器页）；分区沿用 `GET /api/market/realtime*`、`/treemap_intraday`、`/stock/radar`、`/api/buy/*` | A/O/T/N/V | — | 全市场共享容器页；盘面区与分析带=`research.market.view`、资金地图=`research.treemap.view`、持仓/机会区=`research.radar.view`，缺节点则该分区不渲染（design/14） |
| `research.treemap.view` | 树图/市场情绪 | menu | — | tab `m` 选股、实时页 `#/rt` 资金地图 | `GET /api/treemap_data`、`/treemap_intraday`、`/treemap_latest_date`、`/market_sentiment`、`/sector_flow_history` | A/O/T/N/V | — | 全市场共享；分时市值模式（`/treemap_intraday`）在实时页，受本码二次控制 |
| `research.market.view` | 市场概览/估值/资金/宏观 | data | `research.treemap.view` | 市场卡片 + 实时页 `#/rt` 盘面区与分析带 | `GET /api/market/*`（含 realtime、realtime/zt-history） | A/O/T/N/V | — | 全市场共享；实时大盘聚合在实时页受本码二次控制；盘面异动告警（mk% → WS 广播）无独立权限码（design/13） |
| `research.stock.view` | 个股研究 | menu | — | tab `l` 个股/详情 | `GET /api/stocks`、`/stocks/suggest`、`/stock/{code}/detail`、`/history`、`/kline`、`/pe_history`、`/moneyflow`、`/signals`、`/margin`、`/top_list`、`/chip`、`/xueqiu`、`/quote`、`/ai-intraday*`、`/tape`、`/sector-rs`、`/minkline` | A/O/T/N/V | — | `/quote` 的个人成本字段由 `portfolio.position.view` 二次控制；详情页 K 线卖出标记/策略信号卡里的个人卖出由 `signal.personal.view` 二次控制（无权限不请求） |
| `research.signal.view` | 全市场买入信号/模型统计 | data | — | tab `s` 信号 | `GET /api/buy_signals`、`GET /api/signal/stats`、`GET /api/risk/signal-stats`、`GET /api/risk/signal-outcomes` | A/O/T/N/V | — | 全局模型买入信号与共享统计；个人卖出信号另见 `signal.personal.view` |
| `signal.personal.view` | 我的卖出/个人信号 | action | `portfolio.position.view` | 持仓页/详情页 | `GET /api/signals/personal`（支持 `stock_code`/`days` 过滤） | A/T/S | self | 读 `user_signal_history`；持仓页「今日信号」与个股详情页（K 线卖出标记/策略信号卡/信号摘要）合并展示。详情页入口在 `research.stock.view` 下、受本码二次控制——个人卖出只经本端点下发，不得并入全局 `/stock/{code}/signals` |
| `research.cb.view` | 可转债 | menu | — | tab `b` 可转债 | `GET /api/cb/*`、`GET /api/stock/{code}/convertible-bonds`、`/cb-linkage` | A/O/T/N/V | — | 全市场共享 |
| `research.news.view` | 资讯 | menu | — | tab `n` 资讯 | `GET /api/news`、`/news/health`、`/news/{id}`、`/stock/{code}/news` | A/O/T/N/V | — | 原文/总结全局共享；`only_focus` 按用户过滤 |
| `research.radar.view` | 盘中雷达/买点 | action | `research.stock.view` | 实时页 `#/rt` 持仓区 / 机会区（原个股页雷达开关已迁走） | `GET /api/stock/radar`、`GET /api/buy/check`、`GET /api/buy/candidates` | A/T/N | self | 关注池/实时持仓/纪律按用户；admin 仅看自己的池 |
| `research.kepl.view` | KEPL 算子/解析 | data | — | 函数页/表达式提示 | `GET /api/kepl/functions`、`POST /api/kepl/parse` | A/O/T/N | — | 解析是只读计算 |
| `research.lineage.view` | 数据血缘 | data | — | 状态页/模型页 | `GET /api/lineage`、`/lineage/model/{version}` | A/O/N | — | 元数据共享 |
| `model.view` | 模型只读 | menu | — | tab `a` 模型 | `GET /api/v1/models`、`/{version}`、`/signals`、`/paper-portfolio`、`/paper-trades`、`/trade-rules`、`/health`、`/diagnosis`、`/delete-check`、`/feature-check`、`/quality-dashboard`、`/permutation`、`/walk-forward`、`/strategy-scan/{task_id}` | A/O/T/N/V | — | 纸面组合明确标注“模型账本” |
| `feature.view` | 特征只读 | menu | — | tab `e` 特征 | `GET /api/features`、`/fields`、`/groups`、`/tags`、`/dependency-graph`、`/ic/board`、`/ic/correlation`、`/{id}`、`/{id}/quality`、`/{id}/data`、`/{id}/missing-heatmap`、`/{id}/compute-status`、`/{id}/ic`、`/{id}/ic/task/{task_id}` | A/O/N | — | 特征库共享 |
| `function.view` | 函数只读 | menu | — | tab `f` 函数 | `GET /api/functions`、`/{id}`、`/{id}/versions` | A/O/N | — | 函数库共享 |
| `dag.view` | DAG 只读 | menu | — | tab `g` DAG | `GET /api/dag/flows`、`/{id}`、`/{id}/task-status`、`/{id}/versions`、`/dag/logs`、`/dag/node-types*`、`GET /api/dag_status`、`/dag_logs`、`/dag_config` | A/O | — | 流程定义共享；日志可能含业务信息 |
| `agent.use` | 站内问答 | menu | — | tab `i` 问答 | `/api/agent/sessions*`、`/messages`、`/chat`、`/upload`、`/stop`、`/files/*`、`/info`、`GET /skills`、`GET /skills/{name}`、`POST /feedback` | A/O/T/N | self | 会话 owner；工具按底层权限逐项校验（见 §6.2.5） |
| `agent.feedback.view` | Agent 反馈查看 | admin | `agent.use` | 反馈列表 | `GET /api/agent/feedback` | A/O | — | 只读；建议按 owner 过滤或仅 admin |
| `agent.skill.edit` | 个人技能编辑 | action | `agent.use` | 技能保存 | `POST/DELETE /api/agent/skills` | A/O/T/N | self | 写入 `data/skills/<user_id>/`，不污染全局 |
| `agent.skill.import` | 全局技能导入 | sensitive | `agent.skill.edit` | GitHub 导入 | `POST /api/agent/skills/import/preview`、`/confirm` | A | — | 写入全局技能；需管理员二次审核 |
| `agent.exec` | 代码执行 | sensitive | `agent.use` | 沙箱执行 | 无独立 HTTP；`run_python/run_node/pip/npm` | A | — | 默认仅 admin；清空环境变量；bwrap 不可用 fail-closed |
| `agent.sql.raw` | 原始 SQL 工具 | sensitive | `agent.use` | Agent 工具能力 | 无独立 HTTP；`tool_query_sql` | A | — | 仅共享表白名单；个人表必须走支持会话专用 API；审计 |

#### 6.2.3 管理域

| 节点码 | 名称 | 类型 | 父节点 | 前端入口 | 后端 API | 默认角色 | 数据范围 | 说明 |
|---|---|---|---|---|---|---|---|---|
| `model.train` | 训练/重训/停止 | action | `model.view` | 模型页训练 | `POST /v1/models/{v}/train`、`/retrain`、`/stop` | A/O | — | 长任务，受并发限制 |
| `model.approve` | 审批/拒绝/提升 | sensitive | `model.view` | 模型页审批 | `POST /v1/models/{v}/approve`、`/reject`、`/promote` | A（O 可选） | — | 默认 admin 单独审批；operator 需显式授权（Q3） |
| `model.archive` | 归档/删除 | sensitive | `model.view` | 模型页归档 | `POST /v1/models/{v}/archive`、`DELETE /v1/models/{v}` | A/O | — | 删除前 `delete-check` |
| `model.config.edit` | 模型创建/配置 | action | `model.view` | 新建/配置 | `POST /v1/models`、`PUT /v1/models/{v}/config` | A/O | — | 配置影响全局信号 |
| `model.evaluate` | 评估/归因/走查/扫描 | action | `model.view` | 评估页 | `POST /v1/models/{v}/attribution`、`/permutation`、`/walk-forward`、`/strategy-scan` | A/O | — | 只读研究，但算力高 |
| `model.regenerate` | 重算当日信号 | sensitive | `model.view` | 信号页生成 | `POST /v1/models/{v}/regenerate-day` | A/O | — | 重写全局信号/纸面数据 |
| `model.scan.apply` | 应用扫描结果 | sensitive | `model.evaluate` | 扫描结果应用 | `POST /v1/models/{v}/strategy-scan/{task_id}/apply` | A/O | — | 影响生产配置 |
| `feature.edit` | 特征编辑 | action | `feature.view` | 特征页 CRUD | `POST /api/features`、`PUT/DELETE /features/{id}`、`PATCH /features/{id}/status`、`PUT /features/{id}/ic-status` | A/O | — | 影响全局特征库 |
| `feature.compute` | 特征计算/IC | action | `feature.view` | 计算按钮 | `POST /features/validate`、`/{id}/stats`、`/{id}/compute-range`、`/{id}/recompute-stats`、`/check-stats-integrity`、`/{id}/ic` | A/O | — | 可能重算大量数据 |
| `function.edit` | 函数编辑 | action | `function.view` | 函数页 CRUD | `POST /api/functions`、`PUT/DELETE /functions/{id}`、`POST /{id}/rollback/{version}` | A/O | — | 影响 KEPL 全局 |
| `function.test` | 函数试运行 | action | `function.view` | 试运行 | `POST /functions/test-run-temp`、`/{id}/test-run` | A/O | — | 沙箱执行 |
| `function.ai_chat` | 函数 AI 助手 | sensitive | `function.edit` | AI 助手 | `POST /functions/ai-chat` | A/O | — | 产生 LLM 费用 |
| `dag.edit` | 流程编辑 | action | `dag.view` | DAG 编辑页 | `POST/PUT/DELETE /api/dag/flows`、`POST /api/dag/flows/validate` | A/O | — | 流程定义影响生产 |
| `dag.publish` | 流程发布/下线 | sensitive | `dag.edit` | 发布按钮 | `POST /api/dag/flows/{id}/publish`、`/unpublish` | A（O 可选） | — | 发布即生效；operator 默认不授予，按需显式加 |
| `dag.execute` | 流程执行 | sensitive | `dag.view` | 执行按钮/旧接口 | `POST /api/dag/flows/{id}/execute`、`POST /api/dag_trigger` | A/O | — | 受 TaskManager 并发/互斥；任务记录 `created_by` |
| `dag.terminate` | 流程终止 | sensitive | `dag.execute` | 终止按钮 | `POST /api/dag_terminate` | A/O | — | 只能终止自己发起的任务或 admin；需 `dag_run_log.created_by` |
| `system.status.view` | 数据/系统状态 | menu | — | tab `x` 状态 | `GET /api/data_status`、`/trade_calendar`、`/data_status/sync_status`、`/data-sources/health`、`/tushare_quota`、`/minute-data`、`/xueqiu`、`/system/metrics`、`/stock_fund_list` | A/O | — | 运维信息；普通用户不默认授予 |
| `system.settings.view` | 系统设置查看 | admin | — | tab `o` 系统设置（后台组） | `GET /api/settings`、`GET /api/settings/preference`、`/llm_config`、`/llm_config/metrics` | A | — | 仅 admin；序列化层强制脱敏 `api_key/deepseek_key` |
| `system.settings.edit` | 系统设置编辑 | sensitive | `system.settings.view` | 设置表单 | `POST /settings/toggle_strategy`、`/preference`、`/update_params`、`/deepseek_key`、`POST /llm_config`、`/llm_config/test` | A | — | LLM Key/全局偏好仅 A；`base_url` 域名白名单 |
| `system.backfill.execute` | 历史补数 | sensitive | `system.status.view` | 补数按钮 | `POST /api/data_status/backfill`、`POST /api/data_status/backfill/{id}/cancel` | A/O | — | 消耗 TuShare 配额 |
| `system.backfill.view` | 补数历史/日志 | action | `system.backfill.execute` | 补数日志 | `GET /api/data_status/backfill/history`、`/logs` | A/O | — | 只读 |
| `system.stats.refresh` | 刷新统计 | action | `system.status.view` | 刷新按钮 | `POST /api/refresh_stats` | A/O | — | 触发全库统计 |
| `system.sync_date` | 同步指定日期 | sensitive | `system.status.view` | 同步按钮 | `POST /api/data_status/sync_date` | A/O | — | 可能触发采集 |
| `system.dbexplorer.view` | 数据库浏览 | sensitive | `system.status.view` | tab `w` 数据 | `GET /api/dbex/*` | A | — | 仅**共享/系统表**白名单；个人表须支持会话专用 API；敏感列永不返回 |
| `model.alerts.view` | 模型告警查看 | action | `model.view` | 模型观察 | `GET /api/risk/alerts?scope=model`（新增参数） | A/O | — | `user_id IS NULL` 的模型/系统告警；operator 可见 |
| `model.risk.evaluate` | 模型风控评估 | action | `model.view` | 模型页手动评估 | `POST /api/risk/evaluate` | A/O | — | 只评 `paper_positions`，写模型告警；个人风控见 `risk.rules.*` |
| `model.alerts.ack` | 模型告警确认 | action | `model.alerts.view` | 模型观察 | `POST /api/risk/alerts/ack?scope=model` | A/O | — | 模型告警是**全局确认**：任一 A/O 确认即对所有 A/O 隐藏；个人告警仍按 user |
| `system.user.manage` | 用户管理 | admin | — | tab `y` 用户（后台组） | `/api/admin/users*` | A | — | 不能禁用最后一个 admin |
| `system.role.manage` | 角色/权限管理 | admin | — | tab `z` 角色权限（后台组） | `/api/admin/roles*`、`GET /api/admin/permissions` | A | — | 防提权：只能授予自己拥有的权限 |
| `system.audit.view` | 审计日志 | admin | — | tab `h` 审计（后台组） | `GET /api/admin/audit` | A | — | 只读、不可删 |
| `admin.support.view` | 支持模式 | sensitive | — | 用户页内“支持会话” | `POST /api/admin/support/sessions`、`DELETE .../{id}` | A（默认 ○）/S | support（运行时） | 限时只读、理由、审计；`support` 不是持久化 scope，见 §6.6 |

> **后台管理分组（导航约定）**：`用户 y`、`角色权限 z`、`审计 h`、`系统设置 o` 属于纯后台能力，统一放在现有【数据 w】之后，并用 `|` 与前面的管理组（模型/函数/特征/DAG/数据）分隔。仅 admin 可见；如果当前用户没有任何后台 tab，则分隔符不渲染。移动端“管理”抽屉用分组标题代替字面 `|`。

#### 6.2.4 公共节点

| 节点码 | 名称 | 类型 | 说明 |
|---|---|---|---|
| `public.login` | 登录 | public | `POST /api/login`；限流 |
| `public.health` | 健康检查 | public | `GET /health` |
| `public.version` | 版本自检 | public | `GET /api/version`；只返回构建哈希，无业务信息 |
| `public.feishu` | 飞书回调 | public | `POST /webhook/feishu`；生产强制签名校验 |
| `ws.connect` | WebSocket 连接 | system | `GET /api/ws/dag`；连接后按消息类型再做权限过滤 |

#### 6.2.5 Agent 工具级权限映射

> 下表的左列是 **Agent 工具名，不是权限码**；右列才是需要校验的权限码。
> `agent.use` 只表示“能用问答入口”，**不隐含**任何底层数据/操作权限。每次工具调用必须由 `execute_tool(principal, tool, args)` 校验底层权限；未知工具直接拒绝。

| Agent 工具 | 需要的底层权限 | 备注 |
|---|---|---|
| `query_sql` | `agent.sql.raw` | 仅 admin；只允许共享表/视图白名单；个人表禁止 |
| `list_tables` / `describe_table` | `agent.sql.raw` | 仅 admin；元数据也只暴露白名单表 |
| `list_workspace` | `agent.use` | 只看自己的会话工作区 |
| `portfolio_summary` | `portfolio.position.view` | 只查 `principal.data_user_id`；支持模式只读 |
| `get_my_portfolio`（飞书/AI） | `portfolio.position.view` | 同上 |
| `confirm_add_portfolio` | `portfolio.position.edit` | 确认卡片落库时再校验；支持模式禁止 |
| `recent_signals` | `research.signal.view` + `signal.personal.view` | 全局买入；个人卖出需个人信号权限 |
| `stock_overview` | `research.stock.view` | 分组标签按 owner 过滤 |
| `get_signal_stats` | `research.signal.view` | 全局统计 |
| `get_news` | `research.news.view` | `only_focus` 按 owner 过滤 |
| `get_market` | `research.market.view` | 共享 |
| `list_dag_flows` / `get_dag_flow` | `dag.view` | 共享定义 |
| `get_dag_run_status` | `dag.view` | 运行状态/日志 |
| `create_dag_flow` | `dag.edit` | 不能通过 `agent.use` 绕过 |
| `run_dag_flow` | `dag.execute` | 任务记录 `created_by` |
| `model_performance` | `model.view` | 只读模型/纸面指标 |
| `search_news` | `research.news.view` | `only_focus` 按 owner 过滤 |
| `run_python` / `run_node` | `agent.exec` | 默认仅 admin；清空环境变量 |
| `pip_install` / `npm_install` | `agent.exec` | 默认禁用；如开启需容器/网络限制 |
| `read_skill` / `list_skills` | `agent.use` | 只读内置 + 自己的技能 |
| `save_skill` | `agent.skill.edit` | 写个人命名空间 |
| `import_skill` | `agent.skill.import` | 仅 admin；二次审核 |
| `get_strategy_config` | `system.settings.view` | 强制字段脱敏；operator 默认无此权限 |

**强制规则**：

1. 工具执行前先校验权限，再执行；不能先执行后过滤结果。
2. 工具内部不得自行拼接 `user_id` 参数；只能使用 `principal.data_user_id`。
3. 工具返回结果按权限裁剪；例如 `stock_overview` 的分组标签只返回当前用户的组。
4. 工具调用写审计（至少 admin/sensitive 工具）；失败/拒绝记 WARN。
5. 代码执行工具不继承服务端环境变量；`DATABASE_URL/APP_SECRET_KEY/TUSHARE_TOKEN/LLM Key` 全部剔除。
6. 沙箱只挂载 `data/agent_ws/<user_id>/<session_id>/` 与必要只读系统库路径；**不得挂载项目根目录/`.env`/源码目录**；pip/npm 默认禁用，如开启必须在容器内且限制网络。
7. `agent.exec` 默认仅 admin；即使 admin 使用也审计。bwrap 不可用时 fail-closed，不能退化为无隔离执行。
8. `agent.exec` 沙箱**禁用网络、禁用本地 PostgreSQL socket、禁用 DB 驱动**（或使用独立只读数据服务）；否则 admin 可通过代码连接数据库，绕过 A7/表白名单。
9. `agent.sql.raw` 必须用 SQL AST 解析所有表引用（CTE/子查询/JOIN），拒绝白名单外表与 `pg_catalog/information_schema`；正则只做初筛。数据库层再用 `stone_ro` 最小权限角色兜底：仅 `SHARED_TABLES` 的 SELECT、默认只读事务，个人/身份表即使绕过解析器也无权限。

### 6.3 角色定义

| 角色码 | 名称 | 类型 | 默认数据范围 | 定位 | 主要权限 |
|---|---|---|---|---|---|
| `admin` | 系统管理员 | 系统 | self（个人）/ 全局管理 | 系统运维、用户/角色、全局数据 | 全部 `system.*`、`model.*`、`feature.*`、`function.*`、`dag.*`、共享研究读；个人域仅 `self`；默认无 `admin.support.view` |
| `operator` | 数据/模型运维 | 系统 | 无个人数据 | 跑批、补数、模型、特征、DAG | `system.status/backfill/stats/sync`、`model.*`、`feature.*`、`function.*`、`dag.edit/execute/terminate`、共享研究读；不含用户管理/LLM Key/DB 浏览/系统设置；`dag.publish` 可选 |
| `trader` | 交易用户 | 业务 | self | 个人持仓/自选/交割单/监测 | `portfolio.*`、`watch.*`、`risk.*`、`signal.personal.view`、`research.*`、`model.view`、`agent.use`（不含 `agent.exec`） |
| `analyst` | 研究用户 | 业务 | self | 研究共享数据 + 个人自选 | `watch.group.*`、`research.*`、`model.view`、`feature.view`、`function.view`、`agent.use`（不含个人持仓/交割单/风控） |
| `viewer` | 只读访客 | 业务 | 无 | 看共享行情/信号/资讯 | `research.*` 只读；无个人数据、无 Agent、无管理 |
| `support` | 支持人员 | 系统 | 运行时 support | 限时只读用户个人数据 | `admin.support.view` + 个人读权限（以 `support_only=true` 授予 position/trades/watch/alerts）；无写权限、无配置/Agent 读取；通过支持会话生效 |

**角色组合原则**：

- 一个用户可以同时拥有多个角色（如 `operator` + `trader`）。
- 功能权限取并集；`deny` override 优先。
- 个人数据权限的 `self` 范围不因角色叠加变成 `all`；跨用户只能通过支持会话。
- 不建议为每个用户建独立角色；用“模板 + 少量 override”。

### 6.4 角色-权限矩阵

> ✔ = 默认授予；○ = 可选/按需；— = 不授予；`S` = `support_only=true`，仅通过支持会话读取。
> 完整矩阵以数据库种子为唯一事实源，本表是评审基线；节点表省略的 admin 个人 `self` 授权以本表为准。

| 权限码 | admin | operator | trader | analyst | viewer | support |
|---|---:|---:|---:|---:|---:|---:|
| `portfolio.position.view` | ✔ | — | ✔ | — | — | S |
| `portfolio.position.edit` | ✔ | — | ✔ | — | — | — |
| `portfolio.history.view` | ✔ | — | ✔ | — | — | S |
| `portfolio.watch_window.launch` | ✔ | — | ✔ | — | — | — |
| `portfolio.trades.view` | ✔ | — | ✔ | — | — | S |
| `portfolio.trades.import` | ✔ | — | ✔ | — | — | — |
| `portfolio.trades.intraday` | ✔ | — | ✔ | — | — | — |
| `portfolio.trades.journal.edit` | ✔ | — | ✔ | — | — | — |
| `portfolio.discipline.view` | ✔ | — | ✔ | — | — | — |
| `portfolio.discipline.edit` | ✔ | — | ✔ | — | — | — |
| `risk.rules.view` | ✔ | — | ✔ | — | — | — |
| `risk.rules.edit` | ✔ | — | ✔ | — | — | — |
| `risk.alerts.view` | ✔ | — | ✔ | — | — | S |
| `risk.alerts.ack` | ✔ | — | ✔ | — | — | — |
| `watch.group.view` | ✔ | — | ✔ | ✔ | — | S |
| `watch.group.edit` | ✔ | — | ✔ | ✔ | — | — |
| `research.treemap.view` | ✔ | ✔ | ✔ | ✔ | ✔ | ○ |
| `research.realtime.view` | ✔ | ✔ | ✔ | ✔ | ✔ | — |
| `research.market.view` | ✔ | ✔ | ✔ | ✔ | ✔ | ○ |
| `research.stock.view` | ✔ | ✔ | ✔ | ✔ | ✔ | ○ |
| `research.signal.view` | ✔ | ✔ | ✔ | ✔ | ✔ | ○ |
| `signal.personal.view` | ✔ | — | ✔ | — | — | S |
| `research.cb.view` | ✔ | ✔ | ✔ | ✔ | ✔ | ○ |
| `research.news.view` | ✔ | ✔ | ✔ | ✔ | ✔ | ○ |
| `research.radar.view` | ✔ | — | ✔ | ✔ | — | — |
| `research.kepl.view` | ✔ | ✔ | ✔ | ✔ | — | — |
| `research.lineage.view` | ✔ | ✔ | — | ✔ | — | — |
| `model.view` | ✔ | ✔ | ✔ | ✔ | ✔ | ○ |
| `feature.view` | ✔ | ✔ | — | ✔ | — | — |
| `function.view` | ✔ | ✔ | — | ✔ | — | — |
| `dag.view` | ✔ | ✔ | — | — | — | — |
| `agent.use` | ✔ | ✔ | ✔ | ✔ | — | — |
| `agent.feedback.view` | ✔ | ✔ | — | — | — | — |
| `agent.skill.edit` | ✔ | ✔ | ✔ | ✔ | — | — |
| `agent.skill.import` | ✔ | — | — | — | — | — |
| `agent.exec` | ✔ | — | — | — | — | — |
| `agent.sql.raw` | ✔ | — | — | — | — | — |
| `model.train` | ✔ | ✔ | — | — | — | — |
| `model.approve` | ✔ | ○ | — | — | — | — |
| `model.archive` | ✔ | ✔ | — | — | — | — |
| `model.config.edit` | ✔ | ✔ | — | — | — | — |
| `model.evaluate` | ✔ | ✔ | — | — | — | — |
| `model.regenerate` | ✔ | ✔ | — | — | — | — |
| `model.scan.apply` | ✔ | ✔ | — | — | — | — |
| `feature.edit` | ✔ | ✔ | — | — | — | — |
| `feature.compute` | ✔ | ✔ | — | — | — | — |
| `function.edit` | ✔ | ✔ | — | — | — | — |
| `function.test` | ✔ | ✔ | — | — | — | — |
| `function.ai_chat` | ✔ | ✔ | — | — | — | — |
| `dag.edit` | ✔ | ✔ | — | — | — | — |
| `dag.publish` | ✔ | ○ | — | — | — | — |
| `dag.execute` | ✔ | ✔ | — | — | — | — |
| `dag.terminate` | ✔ | ✔ | — | — | — | — |
| `system.status.view` | ✔ | ✔ | — | — | — | — |
| `system.settings.view` | ✔ | — | — | — | — | — |
| `system.settings.edit` | ✔ | — | — | — | — | — |
| `system.backfill.execute` | ✔ | ✔ | — | — | — | — |
| `system.backfill.view` | ✔ | ✔ | — | — | — | — |
| `system.stats.refresh` | ✔ | ✔ | — | — | — | — |
| `system.sync_date` | ✔ | ✔ | — | — | — | — |
| `system.dbexplorer.view` | ✔ | — | — | — | — | — |
| `system.audit.view` | ✔ | — | — | — | — | — |
| `system.user.manage` | ✔ | — | — | — | — | — |
| `system.role.manage` | ✔ | — | — | — | — | — |
| `model.alerts.view` | ✔ | ✔ | — | — | — | — |
| `model.alerts.ack` | ✔ | ✔ | — | — | — | — |
| `model.risk.evaluate` | ✔ | ✔ | — | — | — | — |
| `admin.support.view` | ○ | — | — | — | — | ✔ |

**矩阵复核要点**：

1. `admin` 有个人域 `self` 权限，但没有 `all`；管理员本人也可以正常使用持仓/自选。
2. `operator` 没有个人域权限，避免运维顺手看到业务数据；需要时叠加 `trader` 角色。
3. `viewer` 只有共享研究读；不能使用 Agent（避免 LLM 成本与数据外泄）。
4. `support` 的个人读权限只在支持会话中生效；没有写权限。
5. `model.approve` 对 operator 标 `○`：如果要求审批与训练分离，则只给 admin 或另设 `approver` 角色。
6. `public.*` 与 `ws.connect` 不是角色授权，不进入矩阵：`public.*` 无登录要求；`ws.connect` 对所有已认证用户开放，连接后按消息类型（owner/`dag.view`/`system.status.view`）过滤。
7. 种子生成器：矩阵中的 `S` 必须写成 `role_permissions.support_only=true`，不能写成普通 `self` grant，否则 support 用户无会话也能读自己的数据，语义与矩阵不符。

### 6.5 数据范围设计

#### 6.5.1 范围定义

| 范围 | 含义 | 适用 | 实现 |
|---|---|---|---|
| `self` | 只操作自己的数据 | 所有个人表 | 查询强制 `user_id = principal.data_user_id` |
| `team` | 团队/部门（预留） | 未来 | 增加 `team_id`，本期不实现 |
| `global` | 共享数据无行级范围 | 共享表 | 只有功能权限，无 owner 过滤 |
| **support（运行时）** | 支持会话临时读取目标用户 | 仅 `support_readable=true` 的个人资源 | `principal.support_mode=true`，`data_user_id=target_user_id`；写操作一律 403 |

**关键结论**：

1. **不引入静态 `all` 范围**。管理员跨用户读取只能通过支持会话，避免“拥有 admin 角色就自动全知”。
2. **`role_permissions.scope` 只允许 `self/team/global`**：个人节点只能 `self`（或未来 `team`），共享节点只能 `global`；`support` 不是持久化 scope。
3. **支持模式是运行时状态**，不是角色属性；`support` 角色只拥有 `admin.support.view` 和只读个人权限，实际跨用户读取必须创建支持会话。
4. **多 grant 不取“最宽 scope”**：个人权限只能收敛到 `self`；多个角色叠加不会把 `self` 提升为跨用户。支持模式只能由支持会话显式开启。
5. **资源级 `support_readable`**：`user_config`、Agent 会话/消息、身份信息默认 `false`；支持模式访问返回 403。

#### 6.5.2 资源-归属-执行点矩阵

| 资源/表 | 归属字段 | 默认范围 | 允许支持读取 | 写操作 | 强制点 |
|---|---|---|---|---|---|
| `portfolio` | `user_id` | self | 是（只读） | 仅 owner | `personal_scope()` / repository |
| `portfolio_history` | `user_id` | self | 是 | 仅 owner（由业务写入） | 同上 |
| `watch_groups` | `user_id` | self | 是 | 仅 owner | 同上 |
| `watch_group_items` | 经 `group_id` | self | 是 | 仅 owner | join `watch_groups` 校验 |
| `trade_slip` | `user_id` | self | 是 | 仅 owner | import/apply 全部带 user_id |
| `trade_import_batch` | `user_id` | self | 是 | 仅 owner | 同上 |
| `trade_intraday` | `user_id` | self | 是 | 仅 owner | 同上 |
| `trade_cash_snapshot` | `user_id` | self | 是 | 仅 owner | PK `(user_id, trade_date)` |
| `trade_plan` | `user_id` | self | 是 | 仅 owner | PK `(user_id, trade_date)` |
| `trade_journal` | `user_id` | self | 是 | 仅 owner | PK `(user_id, trade_date)` |
| `risk_alerts`（个人） | `user_id` | self | 是 | 仅 owner ack | `WHERE user_id=:uid` |
| `risk_alerts`（模型） | `user_id IS NULL` | 系统 | 管理员/operator 只读 | 仅系统任务 | `WHERE user_id IS NULL`；`model.alerts.view` |
| `intraday_ai_event` | `user_id`（新增）或 join alert | self | 是（随告警） | 仅系统任务 | 按 owner 查询/推送 |
| `intraday_signal_outcome` | `user_id`（新增）或 join alert | self | 是（随告警） | 仅系统任务 | 按 owner/alert 去重 |
| `user_config` | `user_id` | self | **否** | 仅 owner | 支持模式 403 |
| `user_signal_history` | `user_id` | self | 是 | 仅系统任务 | 按用户生成 |
| `agent_sessions` | `user_id` | self | **否（默认）** | 仅 owner | runtime 全函数校验 |
| `agent_messages` | 经 session | self | **否（默认）** | 仅 owner | 先校验 session owner |
| `agent_feedback` | 经 session | self | **否（默认）** | 仅 owner | 同上 |
| `agent_usage_daily` | `user_id` | self | 否 | 仅系统任务 | 配额计数 |
| `dag_run_log` | `created_by`（新增） | 系统 | admin/operator 只读 | 仅系统任务 | 终止校验 owner/admin |
| `users` | `id` | self（本人） | 管理员管理（不含密码） | 管理员 | 只有 `/api/me` 和管理端 |
| `audit_log` | — | 管理员 | 管理员 | 不可写（仅系统） | 只读 API |
| 共享表 | — | global | — | 管理员/operator 按功能权限 | 无 owner |

### 6.6 支持模式（管理员跨用户只读）

**问题**：如果给 `admin` 静态 `all` 范围，管理员的一次误操作或 token 泄露就会暴露所有用户持仓。  
**方案**：支持模式 = 显式、限时、只读、留痕的会话。

```
管理员 POST /api/admin/support/sessions
  { target_user_id, reason, ttl_minutes<=60 }
        │
        ▼
校验：
  1. 操作者有 admin.support.view
  2. target 用户存在且 active
  3. reason 非空（>=10 字）
  4. ttl <= 60
  5. 不能对自己开支持会话
        │
        ▼
创建 support_sessions 行（只存 token_hash），返回短期 support_token
（含 session_id/target_user_id/admin_user_id/exp；不能单独使用）
        │
        ▼
后续个人读请求必须同时带：
  Authorization: Bearer <管理员主 JWT>
  X-Support-Session: <support_token>
        │
        ▼
require_perm 每次请求查 support_sessions：
  - revoked_at IS NULL AND expires_at > now()
  - admin_user_id = 主 JWT 的 user_id
  - token_hash 匹配（常数时间比较）
  - support_mode = true
  - data_user_id = target_user_id
  - 资源级 support_readable=false → 403
  - 所有写操作 403
  - 每次访问写 audit_log（action=admin.support.access）
        │
        ▼
到期/撤销：DELETE /api/admin/support/sessions/{id} 或 exp 自动失效
```

**支持模式规则**：

| 规则 | 内容 |
|---|---|
| SUP-01 | 必须显式创建会话；不能只靠角色 |
| SUP-02 | 只读；任何写操作（含 `POST /risk/alerts/ack`）一律 403；**不能按 HTTP 方法判断**，必须用端点元数据 `is_write` |
| SUP-03 | 默认 TTL 30 分钟，最长 60 分钟；可提前撤销；每次请求都校验 `expires_at/revoked_at`，不能只依赖缓存 TTL |
| SUP-04 | 每次访问写入审计：操作者、目标用户、资源、时间、IP |
| SUP-05 | 支持模式不适用于 `user_config`、Agent 会话/消息、密码/身份信息；资源级 `support_readable=false` 返回 403 |
| SUP-06 | 目标用户可在自己的安全页查看“谁在何时支持访问过我”（可选） |
| SUP-07 | **双凭证**：请求必须同时带管理员主 JWT + `X-Support-Session`；支持令牌不能单独使用 |
| SUP-08 | 支持令牌绑定 `admin_user_id/token_version/session_id`；令牌只存哈希；`used_at` 记录首次使用（不承诺一次性，如承诺则加消耗字段） |

### 6.7 ABAC 策略钩子

> ABAC 只做“在什么条件下允许”，不替代 RBAC。钩子按权限码注册，集中实现、集中审计；禁止在业务代码里散落条件判断。

#### 6.7.1 钩子清单

| 钩子码 | 绑定权限 | 触发点 | 条件字段 | 失败动作 | 优先级 | 审计 | 示例 |
|---|---|---|---|---|---|---|---|
| `ABAC-TRADES-IMPORT-WINDOW` | `portfolio.trades.import` | 导入/同步前 | `allowed_hours` | 403 | P1 | 是 | 仅 08:00–23:00 允许导入，避免盘中误操作 |
| `ABAC-AGENT-DAILY-QUOTA` | `agent.use` | 新建会话/发消息前 | `daily_messages`、`daily_tokens` | 429 | P0 | 是 | 普通用户每日 200 条/20 万 token；管理员不限 |
| `ABAC-DBEXPLORER-MAX-ROWS` | `system.dbexplorer.view` | 查询前 | `max_rows` | 403/截断 | P1 | 是 | admin 单次最多 5000 行；默认 500 |
| `ABAC-DBEXPLORER-WHITELIST` | `system.dbexplorer.view` | 查询前 | `table_whitelist`、`column_blacklist` | 403 | P0 | 是 | 只允许共享/系统表白名单；`users/portfolio/trade_*/agent_*/audit_log/support_sessions` 禁止 |
| `ABAC-SUPPORT-REASON` | `admin.support.view` | 创建支持会话 | `reason_min_len`、`max_ttl_min` | 403 | P0 | 是 | 理由 >=10 字；TTL <=60 分钟 |
| `SUP-READONLY`（核心校验，非 ABAC） | 所有个人写权限 | `require_perm` | `support_mode`、`ctx.is_write` | 403 | P0 | 是 | 支持模式下禁止写；按端点元数据判断 |
| `SUP-DOUBLE-CREDENTIAL`（核心校验，非 ABAC） | 所有支持读取 | `require_perm` 支持分支 | `session_id`、`admin_user_id`、`expires_at`、`revoked_at` | 401/403 | P0 | 是 | 主 JWT + 支持令牌缺一不可；每次查库 |
| `SUP-RESOURCE-EXCLUDE`（核心校验，非 ABAC） | 所有支持读取 | `personal_scope`/资源元数据 | `support_readable` | 403 | P0 | 是 | `user_config`/Agent 会话/身份信息不可读 |
| `ABAC-MODEL-APPROVE-STEPUP` | `model.approve` | 审批前 | `reauth_minutes` | 403 | P1 | 是 | 需 `POST /api/me/reauth` 签发 step-up 票据；10 分钟内有效 |
| `ABAC-BACKFILL-QUOTA-GUARD` | `system.backfill.execute` | 启动补数前 | `min_quota_remaining` | 403 | P1 | 是 | 剩余配额 <100 次拒绝启动 |
| `ABAC-ROLE-GRANT-NO-ESCALATION` | `system.role.manage` **和** `system.user.manage` | 分配角色/权限前 | `grant_subset_of_actor` | 403 | P0 | 是 | 不能授予自己没有的权限；两条路径都必须检查 |
| `ABAC-LAST-ADMIN-PROTECT` | `system.user.manage` | 禁用/改角色前 | `min_active_admins` | 409 | P0 | 是 | 至少保留 1 个 active admin |
| `ABAC-LOGIN-IP-ALLOWLIST` | `public.login` | 登录前 | `cidrs` | 403 | P2 | 是 | 可选：管理员来源 IP 白名单；条件存 `strategy_config` 或独立表 |
| `ABAC-LOGIN-USERNAME-LOCK` | `public.login` | 登录前 | `max_failures`、`lock_minutes` | 429/403 | P1 | 是 | 按 IP+username 双维度计数；成功只清该用户名 |
| `ABAC-AGENT-SQL-RAW` | `agent.sql.raw` | 工具调用前 | `table_whitelist`、`audit=true` | 403 | P0 | 是 | 仅 admin；只允许共享表白名单；个人表禁止 |
| `ABAC-AGENT-EXEC` | `agent.exec` | 代码执行前 | `role=admin`、`sandbox_required` | 403 | P0 | 是 | 默认仅 admin；bwrap 不可用 fail-closed；清空环境变量 |
| `ABAC-NO-CONDITION-BYPASS` | 所有带 conditions 的权限 | 授权合并 | `strictest_conditions` | 403 | P0 | 是 | 多 grant 时不能用一个“无 conditions”的 allow 绕过受限 grant |

#### 6.7.2 条件 JSON Schema（约定）

```json
{
  "allowed_hours": {"start": "08:00", "end": "23:00", "tz": "Asia/Shanghai"},
  "daily_messages": 200,
  "daily_tokens": 200000,
  "max_rows": 500,
  "reason_min_len": 10,
  "max_ttl_min": 60,
  "reauth_minutes": 10,
  "min_quota_remaining": 100,
  "grant_subset_of_actor": true,
  "min_active_admins": 1,
  "max_failures": 5,
  "lock_minutes": 15,
  "cidrs": ["10.0.0.0/8"],
  "table_whitelist": ["daily_quote", "feature_values", "model_versions"],
  "column_blacklist": ["password_hash", "api_key", "token_hash"]
}
```

**约束**：

- 只允许上面登记的键；未知键启动时报错，避免“写了条件但没实现”。
- 条件值做类型/范围校验；例如 `max_ttl_min <= 60`。
- 条件不是 SQL 表达式，不允许注入；全部由 Python 钩子解释。
- 条件不改变权限码；只影响允许/拒绝。
- **每个钩子必须声明默认值**；缺省时使用默认值，不能因为“没配条件”而放行。
- **多 grant 合并**：同一权限有多个 allow grant 时，conditions 采用 **AND/最严格** 语义；任一受限 grant 的条件不通过则拒绝。禁止“无 conditions 的 grant 覆盖有 conditions 的 grant”。
- **deny override > ABAC deny > allow**；ABAC 只做收紧，不做放宽。

#### 6.7.3 评估顺序与冲突规则

```
1. 认证失败 → 401
2. 无功能权限（含 deny 覆盖）→ 403
3. 支持模式下写操作 → 403
4. ABAC 钩子按注册顺序执行：
   - 任一 deny → 403/429/409
   - 任一 allow 不覆盖 deny
5. 数据范围生效
6. 审计
```

| 场景 | 结果 |
|---|---|
| role allow + user deny | deny（拒绝优先） |
| role allow + user allow | allow |
| 多个角色 allow，个人权限 scope 不同 | **只能收敛到 self**；不允许 `global`；支持模式只能由支持会话开启 |
| 多个 allow grant 的 conditions 不同 | 取 **AND/最严格**；无 conditions 不能绕过有 conditions |
| ABAC deny + role allow | deny |
| ABAC 条件缺省 | 使用钩子声明的默认值；默认值缺失视为配置错误 |
| 钩子异常 | 失败关闭（fail-closed），返回 503/403 并告警，不能放行 |

### 6.8 权限评估伪代码

```python
def authorize(principal: Principal, perm: str, ctx: RequestContext) -> None:
    # 0. 支持会话（必须主 JWT + 支持令牌；每次查库校验）
    if ctx.support_token:
        session = load_valid_support_session(ctx.support_token, principal.user_id)
        if session is None:
            raise HTTPException(401, "支持会话无效或已过期")
        principal.support_mode = True
        principal.support_target_id = session.target_user_id

    # 1. 功能权限
    grant = principal.effective_grant(perm)
    # support_only grant 只在支持会话中生效；普通会话视为无此授权
    if grant is not None and grant.support_only and not principal.support_mode:
        grant = None
    if grant is None:
        raise HTTPException(403, "无权限")
    if grant.deny:
        raise HTTPException(403, "无权限")

    # 2. 支持模式只读
    if principal.support_mode and ctx.is_write:
        raise HTTPException(403, "支持模式只读")

    # 3. ABAC 钩子（fail-closed：钩子异常不得放行）
    try:
        for hook in HOOKS.get(perm, []):
            verdict = hook.evaluate(principal, grant, ctx)
            if verdict.deny:
                audit_abac_deny(principal, perm, hook, ctx)
                raise HTTPException(verdict.status, verdict.message)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("[auth] ABAC hook failed: %s", exc)
        raise HTTPException(503, "权限校验暂时不可用")

    # 4. 数据范围与资源级支持排除
    if principal.support_mode and not ctx.resource_support_readable:
        raise HTTPException(403, "该资源不支持支持模式")
    ctx.data_user_id = principal.support_target_id or principal.user_id

    # 5. 审计
    if perm in SENSITIVE_PERMS or principal.support_mode:
        audit_access(principal, perm, ctx)
```

### 6.9 错误语义

| 状态码 | 场景 | 说明 |
|---|---|---|
| 401 | 无 token / token 过期 / 用户禁用 / token_version 不符 | 不暴露具体原因给前端 |
| 403 | 已登录但无功能权限 / ABAC 拒绝 / 支持模式写 | 前端展示“无权限”，不要暴露资源存在性 |
| 404 | 资源不存在 **或不属于当前用户** | 跨用户访问一律 404，避免枚举 |
| 409 | 用户名冲突 / 最后一个管理员保护 / 状态冲突 | 返回可读原因 |
| 422 | 参数校验失败 | 标准 FastAPI 错误 |
| 429 | 登录限流 / Agent 配额 / 导出超限 | 返回 `Retry-After`（如适用） |
| 503 | 权限缓存/钩子异常（fail-closed） | 告警并拒绝，不放行 |

### 6.10 权限缓存与传播

| 项 | 设计 |
|---|---|
| 缓存键 | `principal:{user_id}:{perm_version}:{support_session_id or 0}` |
| 缓存内容 | roles、effective grants、scopes、conditions；**不缓存支持会话的有效性** |
| 支持会话 | 每次请求查 `support_sessions` 的 `revoked_at/expires_at/admin_user_id`；缓存不能绕过 |
| override 过期 | 缓存 TTL 取 `min(60s, 最近 override 过期时间)`；过期后立即重算 |
| 失效 | 角色/权限/用户角色/override 变更时 `users.perm_version+1`；支持会话创建/撤销单独失效 |
| TTL | 60 秒兜底；变更通过版本号立即生效 |
| 多 worker | 本期单 worker；扩容时用 Redis pub/sub 或共享缓存，`perm_version` 作一致性锚 |
| JWT | 只带 `perm_version`，不带权限列表；避免 token 体积和过期权限 |
| 前端 | `/api/me` 返回 `perm_version` + 权限码；权限变更后前端轮询/重新拉取 |

### 6.11 审计设计

| 事件 | 必记字段 |
|---|---|
| 登录成功/失败 | user_id/username、IP、UA、结果、原因 |
| 用户创建/禁用/启用/改密 | actor、target、before/after（脱敏） |
| 角色创建/修改/删除 | actor、role、before/after |
| 角色分配/回收 | actor、target、role、有效期 |
| 权限授予/回收 | actor、role/user、permission、scope、conditions |
| 支持会话创建/访问/撤销 | actor、target、reason、资源、IP、时间 |
| 个人数据写 | actor、resource、resource_id、before/after |
| 敏感操作 | 训练/审批/归档/发布/执行/终止/补数/LLM 配置 |
| 权限拒绝 | 可选：高频 403 聚合告警，避免日志爆炸 |

**审计表规则**：

- 只插入，不更新/删除；管理端只读。
- `before/after` 对密码、token、API Key 做脱敏。
- 建议按月分区或定期归档；本期先普通表 + 索引。

---

## 7. 数据模型设计

### 7.0 表改造总览

| 类别 | 表/视图 | 改动 |
|---|---|---|
| **共享不动** | `daily_quote`、`index_daily_quote`、`stock_master`、`stock_fundamentals`、`stock_fundamentals_history`、`stock_moneyflow`、`stock_margin_detail`、`stock_top_list`、`stock_holder_number`、`stock_share_float`、`index_valuation`、`macro_indicator`、`feature_values`、`features`、`functions`、`function_versions`、`model_versions`、`training_trials`、`model_health`、`backtest_records`、`backtest_trades`、`backtest_daily_records`、`paper_positions`、`paper_trades`、`stock_treemap_cache`、`stock_treemap_intraday`、`news_item`、`news_summary`、`news_stock`、`news_crawl_state`、`news_poll_state`、`cb_basic`、`cb_daily`、`cb_share`、`cb_metrics`、`trade_calendar`、`daily_completeness`、`entity_stats`、`data_stats_cache`、`data_lineage`、`dag_flows`、`backfill_tasks`、`tushare_quota`、`xueqiu_status`、`xueqiu_sentiment` | 不加 owner；仅加权限码；`signal_history` 只保留全局买入 |
| **加 owner** | `portfolio`、`portfolio_history`、`watch_groups`、`trade_slip`、`trade_import_batch`、`trade_intraday`、`trade_cash_snapshot`、`trade_plan`、`trade_journal`、`agent_sessions` | `user_id NOT NULL`；唯一约束/主键按用户拆分 |
| **加 owner（可空）** | `risk_alerts` | `user_id NULL`=模型/系统告警；个人告警非空 |
| **加 owner（回填）** | `intraday_ai_event`、`intraday_signal_outcome` | 先回填后 NOT NULL；按 alert owner |
| **加发起者** | `dag_run_log` | `created_by`；终止校验 owner/admin |
| **继承归属** | `watch_group_items`（via `group_id`）、`agent_messages`/`agent_feedback`（via `session_id`）、`news_poll_state`（全局采集） | 不冗余加 `user_id`；查询 join 校验 |
| **新增** | `users`、`roles`、`permissions`、`role_permissions`、`user_roles`、`user_permission_overrides`、`support_sessions`、`audit_log`、`user_config`、`user_signal_history`、`agent_usage_daily` | 权限/个人配置/个人信号/审计/配额 |
| **新增（可选二期）** | `model_score_daily` | 全市场分数复用；一期可不建 |
| **视图** | `v_focus_pool`（带 `user_id`）、`v_focus_pool_all`（并集） | DROP + CREATE；个人读取/全局采集分离 |
| **配置拆分** | `strategy_config`（全局）+ `user_config`（个人） | `model_risk_rules` 全局；`risk_rules/intraday_rules/trade_discipline` 个人 |

> **不是 owner 的 `user_id`**：`xueqiu_status.user_id` 是雪球平台用户 ID，禁止当成本站用户；`strategy_config.updated_by` 是来源标签（web/feishu/manual），不是用户 ID。

### 7.1 设计原则

1. **共享表不改归属**：行情、特征、模型、回测、买入信号、树图、新闻、宏观、可转债、DAG、配额保持全局。
2. **个人表加 owner**：所有个人资源必须有 `user_id`；不允许“靠代码约定”隔离。
3. **唯一约束按用户拆分**：同一股票可以同时出现在不同用户的持仓；同一分组名可以不同用户各有一个。
4. **主键包含 owner**：现金/计划/日志这类“每天一条”的表，主键改为 `(user_id, trade_date)`。
5. **个人信号不写全局表**：卖出/个人信号写 `user_signal_history`，不污染 `signal_history` 与 `model_health`。
6. **模型账本不动**：`paper_*` 继续按 `model_version` 隔离，属于全局模型观察。
7. **迁移可验证**：每个个人表迁移后 `user_id IS NULL` 计数必须为 0（`risk_alerts` 除外）。
8. **软删除用户**：不物理删除用户；个人数据 `ON DELETE RESTRICT`，需要注销时走单独 purge 流程。

### 7.2 新增表 DDL

#### 7.2.1 `users`

```sql
CREATE TABLE IF NOT EXISTS users (
    id                  SERIAL PRIMARY KEY,
    username            VARCHAR(32) NOT NULL,
    password_hash       VARCHAR(255) NOT NULL,
    display_name        VARCHAR(32) NOT NULL DEFAULT '',
    email               VARCHAR(128),
    phone               VARCHAR(32),
    status              VARCHAR(16) NOT NULL DEFAULT 'active',
    must_change_password BOOLEAN NOT NULL DEFAULT false,
    token_version       INT NOT NULL DEFAULT 0,
    perm_version        INT NOT NULL DEFAULT 0,
    failed_login_count  INT NOT NULL DEFAULT 0,
    locked_until        TIMESTAMP,
    last_login_at       TIMESTAMP,
    last_login_ip       INET,
    feishu_open_id      VARCHAR(64),
    feishu_union_id     VARCHAR(64),
    created_by          INT REFERENCES users(id) ON DELETE SET NULL,
    created_at          TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at          TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT ck_users_status CHECK (status IN ('active','disabled','locked')),
    CONSTRAINT ck_users_username CHECK (username ~ '^[A-Za-z0-9_.-]{3,32}$')
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_users_username ON users (username);
CREATE UNIQUE INDEX IF NOT EXISTS uq_users_feishu_open_id
    ON users (feishu_open_id) WHERE feishu_open_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_users_status ON users (status);
```

#### 7.2.2 `roles`

```sql
CREATE TABLE IF NOT EXISTS roles (
    id          SERIAL PRIMARY KEY,
    code        VARCHAR(32) NOT NULL,
    name        VARCHAR(32) NOT NULL,
    description VARCHAR(200) NOT NULL DEFAULT '',
    is_system   BOOLEAN NOT NULL DEFAULT false,
    data_scope  VARCHAR(16) NOT NULL DEFAULT 'self',
    status      VARCHAR(16) NOT NULL DEFAULT 'active',
    created_at  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT ck_roles_scope CHECK (data_scope IN ('self','team')),
    CONSTRAINT ck_roles_status CHECK (status IN ('active','disabled'))
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_roles_code ON roles (code);
```

> `data_scope` 只影响个人资源的默认范围；`all` 不支持静态角色，跨用户读取必须走支持会话（§6.6）。

#### 7.2.3 `permissions`

```sql
CREATE TABLE IF NOT EXISTS permissions (
    id          SERIAL PRIMARY KEY,
    code        VARCHAR(64) NOT NULL,
    name        VARCHAR(64) NOT NULL,
    type        VARCHAR(16) NOT NULL,
    parent_id   INT REFERENCES permissions(id) ON DELETE RESTRICT,
    route       VARCHAR(128),
    api_pattern VARCHAR(256),
    sort_order  INT NOT NULL DEFAULT 0,
    is_system   BOOLEAN NOT NULL DEFAULT false,
    is_public   BOOLEAN NOT NULL DEFAULT false,
    description VARCHAR(200) NOT NULL DEFAULT '',
    created_at  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT ck_permissions_type CHECK (type IN ('menu','action','sensitive','data','admin','public','system'))
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_permissions_code ON permissions (code);
CREATE INDEX IF NOT EXISTS idx_permissions_parent ON permissions (parent_id, sort_order);
CREATE INDEX IF NOT EXISTS idx_permissions_type ON permissions (type);
```

#### 7.2.4 `role_permissions`

```sql
CREATE TABLE IF NOT EXISTS role_permissions (
    role_id       INT NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
    permission_id INT NOT NULL REFERENCES permissions(id) ON DELETE CASCADE,
    scope         VARCHAR(16) NOT NULL DEFAULT 'self',
    support_only  BOOLEAN NOT NULL DEFAULT false,
    conditions    JSONB NOT NULL DEFAULT '{}',
    created_at    TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (role_id, permission_id),
    CONSTRAINT ck_role_permissions_scope CHECK (scope IN ('self','team','global'))
);
CREATE INDEX IF NOT EXISTS idx_role_permissions_perm ON role_permissions (permission_id);
```

> `scope=global` 只用于共享数据；个人资源必须 `self`/`team`。**写入角色权限时立即校验**（不只启动校验）：个人节点禁止 `global`，共享节点只允许 `global`；默认值 `self`，避免漏填导致个人权限变全局。`support` 不是持久化 scope；`support_only=true` 表示该 grant 只在 `principal.support_mode=true` 时生效（support 角色的个人读权限用它）。

#### 7.2.5 `user_roles`

```sql
CREATE TABLE IF NOT EXISTS user_roles (
    user_id     INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role_id     INT NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
    assigned_by INT REFERENCES users(id) ON DELETE SET NULL,
    assigned_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    expires_at  TIMESTAMP,
    PRIMARY KEY (user_id, role_id)
);
CREATE INDEX IF NOT EXISTS idx_user_roles_role ON user_roles (role_id);
CREATE INDEX IF NOT EXISTS idx_user_roles_expires ON user_roles (expires_at)
    WHERE expires_at IS NOT NULL;
```

#### 7.2.6 `user_permission_overrides`

```sql
CREATE TABLE IF NOT EXISTS user_permission_overrides (
    user_id       INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    permission_id INT NOT NULL REFERENCES permissions(id) ON DELETE CASCADE,
    effect        VARCHAR(8) NOT NULL,
    scope         VARCHAR(16) NOT NULL DEFAULT 'self',
    conditions    JSONB NOT NULL DEFAULT '{}',
    reason        VARCHAR(200) NOT NULL DEFAULT '',
    expires_at    TIMESTAMP,
    created_by    INT REFERENCES users(id) ON DELETE SET NULL,
    created_at    TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id, permission_id),
    CONSTRAINT ck_user_overrides_effect CHECK (effect IN ('allow','deny')),
    CONSTRAINT ck_user_overrides_scope CHECK (scope IN ('self','team','global'))
);
CREATE INDEX IF NOT EXISTS idx_user_overrides_expires ON user_permission_overrides (expires_at)
    WHERE expires_at IS NOT NULL;
```

> **个人权限 override 校验**：即使 `scope` CHECK 允许 `global`（共享权限需要），写入时也必须校验——个人节点只能 `self`/`team`，`effective_grant` 对个人权限强制收敛到 `self`；§7.4 #13 做数据校验。支持会话不是 override 的 scope，`support_only` 只用于角色 grant。

#### 7.2.7 `support_sessions`

```sql
CREATE TABLE IF NOT EXISTS support_sessions (
    id             BIGSERIAL PRIMARY KEY,
    admin_user_id  INT NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    target_user_id INT NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    reason         VARCHAR(200) NOT NULL,
    token_hash     CHAR(64) NOT NULL,
    expires_at     TIMESTAMP NOT NULL,
    revoked_at     TIMESTAMP,
    revoked_by     INT REFERENCES users(id) ON DELETE SET NULL,
    used_at        TIMESTAMP,
    last_access_at TIMESTAMP,
    access_count   INT NOT NULL DEFAULT 0,
    created_ip     INET,
    created_at     TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT ck_support_not_self CHECK (admin_user_id <> target_user_id),
    CONSTRAINT ck_support_expiry CHECK (expires_at > created_at)
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_support_token_hash ON support_sessions (token_hash);
CREATE INDEX IF NOT EXISTS idx_support_admin ON support_sessions (admin_user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_support_target ON support_sessions (target_user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_support_expires ON support_sessions (expires_at)
    WHERE revoked_at IS NULL;
```

#### 7.2.8 `audit_log`

```sql
CREATE TABLE IF NOT EXISTS audit_log (
    id             BIGSERIAL PRIMARY KEY,
    actor_user_id  INT REFERENCES users(id) ON DELETE SET NULL,
    target_user_id INT REFERENCES users(id) ON DELETE SET NULL,
    action         VARCHAR(64) NOT NULL,
    resource_type  VARCHAR(32) NOT NULL,
    resource_id    VARCHAR(64),
    before         JSONB,
    after          JSONB,
    ip             INET,
    user_agent     VARCHAR(256),
    request_id     VARCHAR(64),
    created_at     TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_audit_actor ON audit_log (actor_user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_target ON audit_log (target_user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_resource ON audit_log (resource_type, resource_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_action ON audit_log (action, created_at DESC);
```

#### 7.2.9 `user_config`

```sql
CREATE TABLE IF NOT EXISTS user_config (
    user_id    INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    config_key VARCHAR(40) NOT NULL,
    params     JSONB NOT NULL DEFAULT '{}',
    enabled    BOOLEAN NOT NULL DEFAULT true,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id, config_key)
);
```

个人键（迁入）：`risk_rules`（个人实盘风控）、`intraday_rules`、`trade_discipline`、`ui_preference`（可选）。
全局键（继续留 `strategy_config`）：`global_preference`、`paper_portfolio`、`model_risk_rules`（模型纸面风控）、`model_freshness`、`news_collect`、`margin_collect`、`llm_config`、`model_signal`、指标参数。

#### 7.2.10 `user_signal_history`

```sql
CREATE TABLE IF NOT EXISTS user_signal_history (
    id                 BIGSERIAL PRIMARY KEY,
    user_id            INT NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    signal_date        DATE NOT NULL,
    stock_code         VARCHAR(6) NOT NULL,
    stock_name         VARCHAR(30) NOT NULL DEFAULT '',
    direction          VARCHAR(7) NOT NULL,
    strength           SMALLINT NOT NULL DEFAULT 0,
    reason             TEXT NOT NULL DEFAULT '',
    model_version      VARCHAR(20) NOT NULL DEFAULT '',
    preference         VARCHAR(10) NOT NULL DEFAULT 'balanced',
    combined_signal    BOOLEAN NOT NULL DEFAULT false,
    suggested_action   VARCHAR(20),
    source_strategies  TEXT,
    params_snapshot    TEXT NOT NULL DEFAULT '',
    predict_5d_return  NUMERIC(8,4),
    predict_10d_return NUMERIC(8,4),
    predict_20d_return NUMERIC(8,4),
    predict_score      NUMERIC(8,4),
    price              NUMERIC(10,2),
    status             VARCHAR(10),
    actual_return      NUMERIC(8,4),
    close_reason       VARCHAR(30),
    closed_at          TIMESTAMP,
    created_at         TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT ck_user_signal_direction CHECK (direction IN ('buy','sell'))
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_user_signal_day
    ON user_signal_history (user_id, signal_date, stock_code, direction, model_version);
CREATE INDEX IF NOT EXISTS idx_user_signal_date ON user_signal_history (user_id, signal_date);
CREATE INDEX IF NOT EXISTS idx_user_signal_stock
    ON user_signal_history (user_id, stock_code, signal_date DESC);
CREATE INDEX IF NOT EXISTS idx_user_signal_status
    ON user_signal_history (user_id, status) WHERE status IS NULL;
```

> **迁移注意**：现有 `signal_history` 的 `model_version/created_at` 可空，迁移时必须 `COALESCE(model_version,'')`、`COALESCE(created_at, now())`；同股同日同模型重复卖出行用 `DISTINCT ON (user_id, signal_date, stock_code, direction, model_version)` 去重并记录被丢弃行；`preference/combined_signal/suggested_action/source_strategies/params_snapshot/predict_*` 显式映射，不能 `INSERT ... SELECT *`。

#### 7.2.11 `agent_usage_daily`

```sql
CREATE TABLE IF NOT EXISTS agent_usage_daily (
    user_id       INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    usage_date    DATE NOT NULL,
    messages      INT NOT NULL DEFAULT 0,
    llm_calls     INT NOT NULL DEFAULT 0,
    prompt_tokens BIGINT NOT NULL DEFAULT 0,
    completion_tokens BIGINT NOT NULL DEFAULT 0,
    tool_calls    INT NOT NULL DEFAULT 0,
    updated_at    TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id, usage_date)
);
```

> 用途：`ABAC-AGENT-DAILY-QUOTA` 的计数来源；每次 LLM 调用/工具调用累加。`ai-intraday`、`function.ai-chat` 也计入统一配额。

#### 7.2.12 `model_score_daily`（建议，二期可后置）

```sql
CREATE TABLE IF NOT EXISTS model_score_daily (
    model_version VARCHAR(20) NOT NULL,
    trade_date    DATE NOT NULL,
    stock_code    VARCHAR(6) NOT NULL,
    score         NUMERIC(12,6),
    rank_pct      NUMERIC(8,6),
    created_at    TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (model_version, trade_date, stock_code)
);
CREATE INDEX IF NOT EXISTS idx_model_score_date ON model_score_daily (trade_date, model_version);
```

> 用途：全市场预测只跑一次，个人卖出信号/持仓分析/回测复用。MVP 可以先不建，直接在模型信号任务里按用户持仓生成个人信号；建表后更干净。

### 7.3 现有表结构变更

#### 7.3.1 字段变更总表

| 表 | 变更 | 字段/约束 | 类型 | NULL | 默认 | 迁移/说明 |
|---|---|---|---|---|---|---|
| `portfolio` | 新增 | `user_id` | INT | 先 NULL → NOT NULL | — | 回填 admin；`REFERENCES users(id) ON DELETE RESTRICT` |
| | 删除 | `idx_portfolio_active_stock` | — | — | — | 原全局唯一索引 |
| | 新增 | `uq_portfolio_user_stock_active` | — | — | — | `UNIQUE (user_id, stock_code) WHERE is_active=true` |
| | 新增 | `idx_portfolio_user_active` | — | — | — | `(user_id, is_active, updated_at DESC)` |
| `portfolio_history` | 新增 | `user_id` | INT | NOT NULL | — | 回填 admin |
| | 替换索引 | `idx_ph_stock` → `idx_ph_user_stock` | — | — | — | `(user_id, stock_code, created_at DESC)` |
| `watch_groups` | 新增 | `user_id` | INT | NOT NULL | — | 回填 admin |
| | 删除约束 | `watch_groups_group_name_key` | — | — | — | 原全局唯一 |
| | 新增 | `uq_watch_user_group_name` | — | — | — | `UNIQUE (user_id, group_name)` |
| | 新增 | `idx_watch_user_sort` | — | — | — | `(user_id, is_default DESC, sort_order, id)` |
| `watch_group_items` | 不变 | 无 | — | — | — | 归属通过 `group_id` 的 `watch_groups.user_id` 校验 |
| | 新增索引 | `idx_wgi_group_added` | — | — | — | `(group_id, added_at)` |
| `trade_slip` | 新增 | `user_id` | INT | NOT NULL | — | 回填 admin |
| | 删除索引 | `uq_trade_slip_order`、`uq_trade_slip_hash` | — | — | — | 原全局唯一 |
| | 新增 | `uq_trade_slip_user_order` | — | — | — | `(user_id, account_no, trade_date, order_no) WHERE order_no<>''` |
| | 新增 | `uq_trade_slip_user_hash` | — | — | — | `(user_id, row_hash)` |
| | 新增 | `idx_trade_slip_user_date` | — | — | — | `(user_id, trade_date DESC)` |
| | 新增 | `idx_trade_slip_user_code` | — | — | — | `(user_id, stock_code, trade_date)` |
| | 新增 | `idx_trade_slip_user_batch` | — | — | — | `(user_id, import_batch_id)` |
| `trade_import_batch` | 新增 | `user_id` | INT | NOT NULL | — | 回填 admin |
| | 新增 | `idx_trade_batch_user` | — | — | — | `(user_id, created_at DESC)` |
| `trade_intraday` | 新增 | `user_id` | INT | NOT NULL | — | 回填 admin |
| | 替换索引 | `idx_trade_intraday_date`、`idx_trade_intraday_code` | — | — | — | 加 `user_id` 前缀 |
| `trade_cash_snapshot` | 新增 | `user_id` | INT | NOT NULL | — | 回填 admin |
| | 修改主键 | `trade_date` → `(user_id, trade_date)` | — | — | — | 先 DROP 旧 PK，再 ADD 新 PK |
| `trade_plan` | 新增 | `user_id` | INT | NOT NULL | — | 回填 admin |
| | 修改主键 | `trade_date` → `(user_id, trade_date)` | — | — | — | 同上 |
| `trade_journal` | 新增 | `user_id` | INT | NOT NULL | — | 回填 admin |
| | 修改主键 | `trade_date` → `(user_id, trade_date)` | — | — | — | 同上 |
| `risk_alerts` | 新增 | `user_id` | INT | NULL | NULL | NULL=系统/模型告警；个人告警写 user_id |
| | 新增 | `idx_ra_user_ack` | — | — | — | `(user_id, ack, id)` |
| | 新增 | `idx_ra_user_date_kind` | — | — | — | `(user_id, trade_date, kind)` |
| | 新增 | `idx_ra_user_stock` | — | — | — | `(user_id, stock_code, created_at DESC)` |
| | 新增 | `idx_ra_user_dedup` | — | — | — | `(user_id, trade_date, kind, title)`；去重 SQL 必须带 user_id，模型告警用 `user_id IS NULL` |
| | 新增 | `ack_by` | INT | NULL | NULL | 确认人；模型告警全局 ack 审计 |
| | 新增 | `ack_at` | TIMESTAMP | NULL | NULL | 确认时间 |
| `intraday_ai_event` | 新增 | `user_id` | INT | NOT NULL | — | 回填 admin；索引 `(user_id, created_at DESC)`；AI 冷却/日限/WS 按 owner |
| `intraday_signal_outcome` | 新增 | `user_id` | INT | 先 NULL → NOT NULL | — | 按 `alert_id` 关联告警回填；索引 `(user_id, alert_time DESC)`；`alert_id` 唯一性保留；全局统计按 `(stock_code,kind,alert_time)` 去重 |
| `dag_run_log` | 新增 | `created_by` | INT | NULL | NULL | 记录触发者；`dag.terminate` 校验 owner/admin；索引 `(created_by, created_at DESC)` |
| `watch_groups` | 新增 | `uq_watch_user_default` | — | — | — | `UNIQUE (user_id) WHERE is_default=true`，保证每用户一个默认组 |
| `agent_sessions` | 新增 | `user_id` | INT | NOT NULL | — | 回填 admin |
| | 替换索引 | `idx_agent_sess_updated` → `idx_agent_sess_user_updated` | — | — | — | `(user_id, updated_at DESC)` |
| | 新增 | `idx_agent_sess_user_pinned` | — | — | — | `(user_id, pinned DESC, updated_at DESC)` |
| `agent_messages` | 不变 | 无 | — | — | — | 通过 session 校验 owner |
| `agent_feedback` | 不变 | 无 | — | — | — | 通过 session 校验 owner |
| `strategy_config` | 不变 | 无 | — | — | — | 个人键迁出；新增全局键 `model_risk_rules`（模型纸面风控）；`global_preference` 继续全局 |
| `signal_history` | 不变 | 无 | — | — | — | 仅保留全局买入；迁移时把卖出行复制到 `user_signal_history` 后删除 |

#### 7.3.2 关键 DDL 片段

```sql
-- 1) 个人表加列（先可空；IF NOT EXISTS 保证可重跑）
ALTER TABLE portfolio ADD COLUMN IF NOT EXISTS user_id INT REFERENCES users(id) ON DELETE RESTRICT;
ALTER TABLE portfolio_history ADD COLUMN IF NOT EXISTS user_id INT REFERENCES users(id) ON DELETE RESTRICT;
ALTER TABLE watch_groups ADD COLUMN IF NOT EXISTS user_id INT REFERENCES users(id) ON DELETE RESTRICT;
ALTER TABLE trade_slip ADD COLUMN IF NOT EXISTS user_id INT REFERENCES users(id) ON DELETE RESTRICT;
ALTER TABLE trade_import_batch ADD COLUMN IF NOT EXISTS user_id INT REFERENCES users(id) ON DELETE RESTRICT;
ALTER TABLE trade_intraday ADD COLUMN IF NOT EXISTS user_id INT REFERENCES users(id) ON DELETE RESTRICT;
ALTER TABLE trade_cash_snapshot ADD COLUMN IF NOT EXISTS user_id INT REFERENCES users(id) ON DELETE RESTRICT;
ALTER TABLE trade_plan ADD COLUMN IF NOT EXISTS user_id INT REFERENCES users(id) ON DELETE RESTRICT;
ALTER TABLE trade_journal ADD COLUMN IF NOT EXISTS user_id INT REFERENCES users(id) ON DELETE RESTRICT;
ALTER TABLE agent_sessions ADD COLUMN IF NOT EXISTS user_id INT REFERENCES users(id) ON DELETE RESTRICT;
ALTER TABLE risk_alerts ADD COLUMN IF NOT EXISTS user_id INT REFERENCES users(id) ON DELETE SET NULL;
ALTER TABLE intraday_ai_event ADD COLUMN IF NOT EXISTS user_id INT REFERENCES users(id) ON DELETE RESTRICT;
ALTER TABLE intraday_signal_outcome ADD COLUMN IF NOT EXISTS user_id INT REFERENCES users(id) ON DELETE RESTRICT;
ALTER TABLE dag_run_log ADD COLUMN IF NOT EXISTS created_by INT REFERENCES users(id) ON DELETE SET NULL;

-- 2) 回填种子管理员（:admin_id 由脚本注入）
UPDATE portfolio SET user_id = :admin_id WHERE user_id IS NULL;
-- ... 其余个人表同理（intraday_ai_event/outcome 优先按 alert 归属回填）

-- 3) 收紧 NOT NULL（risk_alerts 除外）
ALTER TABLE portfolio ALTER COLUMN user_id SET NOT NULL;
-- ... 其余个人表同理（intraday_signal_outcome 回填后也 SET NOT NULL）

-- 4) 默认组规范化（必须先于 uq_watch_user_default）
UPDATE watch_groups g SET is_default = false
WHERE g.is_default = true
  AND g.id <> (SELECT min(x.id) FROM watch_groups x
               WHERE x.user_id = g.user_id AND x.is_default = true);

-- 若已有非默认“自选”组，优先提升它，避免后面 (user_id, group_name) 唯一冲突
UPDATE watch_groups g SET is_default = true
WHERE g.group_name = '自选' AND g.is_default = false
  AND NOT EXISTS (
    SELECT 1 FROM watch_groups x
    WHERE x.user_id = g.user_id AND x.is_default = true
  );

-- 完全没有“自选”组的用户，补建一个
INSERT INTO watch_groups (user_id, group_name, is_default, sort_order)
SELECT u.id, '自选', true, 0
FROM users u
WHERE NOT EXISTS (
  SELECT 1 FROM watch_groups g
  WHERE g.user_id = u.id AND g.group_name = '自选'
);

-- 5) 唯一约束切换（IF NOT EXISTS）
DROP INDEX IF EXISTS idx_portfolio_active_stock;
CREATE UNIQUE INDEX IF NOT EXISTS uq_portfolio_user_stock_active
    ON portfolio (user_id, stock_code) WHERE is_active = true;

ALTER TABLE watch_groups DROP CONSTRAINT IF EXISTS watch_groups_group_name_key;
CREATE UNIQUE INDEX IF NOT EXISTS uq_watch_user_group_name ON watch_groups (user_id, group_name);
CREATE UNIQUE INDEX IF NOT EXISTS uq_watch_user_default
    ON watch_groups (user_id) WHERE is_default = true;

DROP INDEX IF EXISTS uq_trade_slip_order;
DROP INDEX IF EXISTS uq_trade_slip_hash;
CREATE UNIQUE INDEX IF NOT EXISTS uq_trade_slip_user_order
    ON trade_slip (user_id, account_no, trade_date, order_no) WHERE order_no <> '';
CREATE UNIQUE INDEX IF NOT EXISTS uq_trade_slip_user_hash ON trade_slip (user_id, row_hash);

-- 6) 现金/计划/日志主键切换（DO 块幂等）
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conrelid = 'trade_cash_snapshot'::regclass AND contype = 'p'
      AND pg_get_constraintdef(oid) LIKE '%(user_id, trade_date)%'
  ) THEN
    ALTER TABLE trade_cash_snapshot DROP CONSTRAINT IF EXISTS trade_cash_snapshot_pkey;
    ALTER TABLE trade_cash_snapshot ADD PRIMARY KEY (user_id, trade_date);
  END IF;
END $$;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conrelid = 'trade_plan'::regclass AND contype = 'p'
      AND pg_get_constraintdef(oid) LIKE '%(user_id, trade_date)%'
  ) THEN
    ALTER TABLE trade_plan DROP CONSTRAINT IF EXISTS trade_plan_pkey;
    ALTER TABLE trade_plan ADD PRIMARY KEY (user_id, trade_date);
  END IF;
END $$;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conrelid = 'trade_journal'::regclass AND contype = 'p'
      AND pg_get_constraintdef(oid) LIKE '%(user_id, trade_date)%'
  ) THEN
    ALTER TABLE trade_journal DROP CONSTRAINT IF EXISTS trade_journal_pkey;
    ALTER TABLE trade_journal ADD PRIMARY KEY (user_id, trade_date);
  END IF;
END $$;

-- 6) 视图：列形状变化必须 DROP + CREATE，不能 CREATE OR REPLACE
--    PostgreSQL 不允许 OR REPLACE 改变列名/列序（旧视图首列是 stock_code）
DROP VIEW IF EXISTS v_focus_pool_all;
DROP VIEW IF EXISTS v_focus_pool;

CREATE VIEW v_focus_pool AS
SELECT p.user_id, p.stock_code
FROM portfolio p
WHERE p.is_active = true AND p.quantity > 0
UNION
SELECT g.user_id, i.stock_code
FROM watch_group_items i JOIN watch_groups g ON g.id = i.group_id;

CREATE VIEW v_focus_pool_all AS
SELECT DISTINCT stock_code FROM v_focus_pool;
```

> **`init_db` 同步改造（强制）**：`app/db/schema.py` 中旧 DDL 会在每次启动时重建全局唯一索引：
> - `idx_portfolio_active_stock ON portfolio(stock_code)`
> - `uq_trade_slip_order` / `uq_trade_slip_hash`
> - `watch_groups.group_name UNIQUE`
> - 旧 `v_focus_pool` 定义
>
> 必须删除/改写这些常量，否则迁移后重启会把旧约束建回来：轻则启动报错被吞、重则用户 B 加同股/同文件时 500 或 `ON CONFLICT DO NOTHING` 静默丢行。迁移脚本与 `schema.py` 必须同版本发布，启动后执行 §7.4 的索引/视图断言。

### 7.3.3 写入路径清单（必须加 owner 的 INSERT/UPDATE）

> 这张表比“文件清单”更重要：迁移把 `user_id` 设为 NOT NULL 后，**所有 INSERT 必须立即写 owner**；`INSERT ... SELECT` 必须从源表带出 owner。开发时按函数逐项勾选。

| 文件:函数 | 操作 | 必须加 owner / 校验 |
|---|---|---|
| `app/trade_slip.py:import_slip` | `INSERT trade_slip`、`INSERT trade_import_batch` | 两条 INSERT 都写 `user_id`；批次与成交同 user |
| `app/trade_slip.py:_portfolio_map` | `SELECT portfolio` | `WHERE user_id=:uid`；否则同股多用户成本互相覆盖 |
| `app/trade_slip.py:load_slip_rows` | `SELECT trade_slip` | `WHERE user_id=:uid`；所有 replay/复盘/闸门共用 |
| `app/trade_slip.py:build_positions` | 读交割单 + portfolio 差异 | 参数 `user_id`；`_portfolio_map`/`load_slip_rows` 都传 |
| `app/trade_slip.py:apply_positions` | `UPDATE/INSERT portfolio`、`INSERT portfolio_history`、`UPDATE trade_import_batch` | 全部带 user；批次更新加 `AND user_id=:uid` |
| `app/trade_slip.py:list_trades/list_batches/delete_batch` | 查询/删除 | `WHERE user_id=:uid`；`delete_batch` 用 `id AND user_id` |
| `app/trade_intraday.py:_slip_end` | `MAX(trade_date) FROM trade_slip` | `WHERE user_id=:uid` |
| `app/trade_intraday.py:_manual_rows` | `trade_intraday` | 加 user |
| `app/trade_intraday.py:_today_actions` | 今日买卖/额度 | 加 user |
| `app/trade_intraday.py:_get_profile` + `_PROFILE_CACHE` | 交易画像 | 参数与缓存 key 都加 user |
| `app/trade_intraday.py:get_or_create_plan` | `trade_plan` `ON CONFLICT (trade_date)` | 冲突目标改 `(user_id, trade_date)`；SELECT/UPDATE 加 user |
| `app/trade_intraday.py:journal_get/journal_save` | `trade_journal` `ON CONFLICT (trade_date)` | 同上 |
| `app/trade_intraday.py:_recent_sell/_cooling_off` | 近期卖出/冷静期 | 加 user |
| `app/trade_intraday.py:record` | `INSERT trade_intraday` | 写 owner |
| `app/trade_intraday.py:delete_action` | 按 id 删除 | `AND user_id=:uid` |
| `app/trade_intraday.py:reconcile` | `MAX(trade_date) FROM trade_slip`、`UPDATE trade_intraday`、`DELETE trade_cash_snapshot` | 全部按 user；导入只对账自己的记录 |
| `app/trade_intraday.py:_snapshot/set_cash` | `trade_cash_snapshot` | 冲突目标/查询按 `(user_id, trade_date)` |
| `app/trade_review.py:get_discipline/save_discipline` | `strategy_config.trade_discipline` | 改读 `user_config(user_id,'trade_discipline')` |
| `app/trade_review.py:build_review` | 全量交割单/现金 | 传 user；`load_slip_rows` 过滤 |
| `app/trade_review.py:today_gate` | 全量交割单/现金 | 传 user |
| `app/execution_quality.py:report/sell_quality/alert_execution/emotion_summary` | 交割单/提醒/情绪 | 全部加 user |
| `app/api/portfolio.py` CRUD/history/risk-heat/watch-window | 持仓/历史/告警计数 | 全部 `data_user_id`；缓存 key 加 user |
| `app/api/watch.py:create_group` | `INSERT watch_groups` | 写 `user_id`；默认组按用户；不能依赖旧全局 `ON CONFLICT (group_name)` |
| `app/api/watch.py:set_stock_groups` | 批量 `DELETE`/`INSERT watch_group_items` | **gid 必须来自当前用户组**；DELETE 加 `AND group_id IN (SELECT id FROM watch_groups WHERE user_id=:uid)`；INSERT 前校验 owner |
| `app/api/portfolio.py:get_portfolio` | 今日信号子查询 `signal_history` | 买入读全局；卖出读 `user_signal_history(user_id)`；不能只查全局表 |
| `app/api/trades.py` 导入/对账入口 | 调 `import_slip/apply_positions/intraday_reconcile` | 显式传 user；不能依赖函数内部默认 |
| `app/api/stock.py:_quote_levels/_quote_cache/_levels_cache` | 个人成本/止损/止盈 | 缓存只存共享行情；个人价位缓存 key 加 `user_id`；或缓存外合并 |
| `app/api/stock.py` 最近 AI 事件 | `intraday_ai_event` | join `risk_alerts.user_id` 或按 `user_id` 过滤 |
| `app/api/news.py:news_health` | `SELECT count(*) FROM v_focus_pool` | 改 `v_focus_pool_all` / `DISTINCT stock_code` |
| `app/intraday_watch.py:_load_pool/_load_live/_persist` | 关注池/实时持仓/告警 | 单行情循环 + 每用户 `user_id`；`_ctx/_state` key 加 user |
| `app/intraday_ai.py` | 规则/冷却/日限/队列 | 通过 `risk_alerts.user_id` 读规则、按 user 冷却/限额、按 user 推送 |
| `app/intraday_outcome.py:evaluate_pending/_upsert` | `intraday_signal_outcome` | 写入 `user_id`（来自 alert）；全局统计按 `(stock_code,kind,alert_time)` 去重 |
| `app/risk.py` | 模型纸面风控 vs 个人实盘风控 | 拆 `get_model_risk_rules`（`strategy_config.model_risk_rules`）与 `get_user_risk_rules`（`user_config`）；告警去重加 user |
| `app/api/risk.py:list_alerts` | `ack=true` 全局确认 | 拆 `POST /api/risk/alerts/ack`；个人 `WHERE user_id=:uid`；模型告警单独语义 |
| `scripts/pipeline.py:model_signal` | 卖出持仓 | 改 `live_positions(db, uid)`；写 `user_signal_history`；先删当日该用户该模型卖出行 |
| `scripts/pipeline.py:model_health` | 绩效 EOD 查询 | 只统计全局买入；`paper_trades` 查询补 `model_version` 过滤（顺手修现存 bug） |
| `app/agent/runtime.py:create_session/branch_session` | `INSERT agent_sessions` | 写 `user_id`；`stream_agent`/工具调度传 `Principal` |
| `app/agent/tools.py` | 全部工具 | 按 §6.2.5 工具级权限校验；`portfolio_summary` 用 `data_user_id` |
| `app/agent/media.py` | 文件签名 | 签名/校验加 `user_id`；无签名 fallback 校验 session owner |
| `app/agent/skills.py` | 技能目录 | `data/skills/<user_id>/`；全局技能只读 |
| `app/agent/sandbox.py` | 工作区 | `data/agent_ws/<user_id>/<session_id>/`；执行清空敏感环境变量 |
| `app/feishu/webhook.py` | 消息/卡片 | 解析 `open_id`→users；未绑定拒绝；卡片写操作校验 `portfolio.position.edit` |
| `app/feishu/webhook.py:_handle_card_action` | `INSERT/UPDATE portfolio`、`INSERT portfolio_history` | 从 `open_id` 解析 `user_id` 写入；不能沿用全局持仓/全局偏好 |
| `app/feishu/handler.py` | 消息处理 | `handle_message(user_id,...)`；历史 key `feishu:<open_id>` |
| `app/feishu/context.py` | 持仓/偏好/规则 | 按 user；`global_preference` 只读 |
| `app/feishu/history.py` | 历史 | 修复 `save_history` 位置参数；按 open_id |
| `app/ai/tools.py` | 持仓查询/确认/配置 | 按 user；`get_strategy_config` 强制脱敏 |
| `app/db/schema.py:init_db` | 旧 DDL | 删除旧全局唯一索引/旧视图；新增幂等迁移块 |
| `app/api/settings.py` | 个人/系统设置 | 拆 `/api/me/settings` 与 `/api/admin/settings`；旧个人键过滤/删除 |
| `app/api/status.py` | WS/补数 | WS ticket + 消息过滤；补数/终止权限；`intraday_ai` join owner |
| `app/api/models.py:delete_model` | 删除模型 | 清理/归档 `user_signal_history` 对应 `model_version` |
| `app/api/market.py` | 市场数据 | **共享表，无 owner 改造**；只加权限码 |
| `crawler/news/collect.py` | 关注池采集 | 改用 `v_focus_pool_all` |
| `app/api/dbexplorer.py` | 全库浏览 | 表/列白名单；个人表禁止；敏感列脱敏 |

### 7.3.4 `ON CONFLICT` / 主键切换影响

| 位置 | 旧写法 | 新写法 |
|---|---|---|
| `trade_cash_snapshot` | `ON CONFLICT (trade_date)` | `ON CONFLICT (user_id, trade_date)` |
| `trade_plan` | `ON CONFLICT (trade_date)` | `ON CONFLICT (user_id, trade_date)` |
| `trade_journal` | `ON CONFLICT (trade_date)` | `ON CONFLICT (user_id, trade_date)` |
| `trade_slip` | `ON CONFLICT (account_no, trade_date, order_no)` / `row_hash` | 唯一索引改 `(user_id, ...)`；冲突目标同步 |
| `watch_groups` 默认组播种 | `ON CONFLICT (group_name)` | 按用户创建；唯一索引 `(user_id, group_name)` |
| `apply_positions` 批次更新 | `WHERE positions_synced=false` | 加 `AND user_id=:uid` |
| `intraday_reconcile` | `MAX(trade_date) FROM trade_slip` 全局 | `WHERE user_id=:uid` |

### 7.4 索引与约束校验清单

| # | 校验 | 期望 |
|---|---|---|
| 1 | `portfolio` 每个用户每个股票最多一条 active | `SELECT user_id, stock_code, count(*) ... HAVING count(*)>1` 返回 0 |
| 2 | `watch_groups` 每个用户组名唯一 | 返回 0 重复 |
| 3 | `trade_slip` 每个用户成交编号/行哈希唯一 | 返回 0 重复 |
| 4 | `trade_cash_snapshot` 每个用户每日一条 | PK 生效 |
| 5 | `trade_plan` / `trade_journal` 每个用户每日一条 | PK 生效 |
| 6 | `risk_alerts` 个人告警都有 user_id，模型告警为 NULL | 分布符合预期 |
| 7 | `agent_sessions` 无 NULL user_id | 0 行 |
| 8 | `v_focus_pool` 无 NULL user_id | 0 行 |
| 9 | 个人表无 NULL user_id | 0 行（`risk_alerts` 除外） |
| 10 | 每个 personal permission 的 scope != 'global' | 0 行违规 |
| 11 | 每个 shared permission 的 scope = 'global' | 0 行违规 |
| 12 | support 角色的个人读权限 `support_only=true` | 0 行违规 |
| 13 | override 的个人权限不得 `scope='global'` | 0 行违规 |

### 7.5 数据归属与迁移

| 数据 | 迁移规则 |
|---|---|
| 现有 `portfolio` / `portfolio_history` | 全部归种子管理员 |
| 现有 `watch_groups` / `watch_group_items` | 全部归种子管理员；确保每个用户有且仅有一个 `is_default=true` 的“自选”组；若历史数据有多个，保留 `id` 最小的一条，其余改为 `is_default=false` |
| 现有 `trade_*` | 全部归种子管理员 |
| 现有个人 `risk_alerts`（`it*`） | 归种子管理员；模型级告警 `user_id=NULL` |
| 现有 `agent_sessions` | 全部归种子管理员 |
| 现有 `intraday_ai_event` / `intraday_signal_outcome` | 通过 `alert_id` 关联的告警 owner 回填；无关联的归种子管理员 |
| 现有 `dag_run_log` | `created_by` 回填 NULL（历史任务无发起者）；新任务写触发者 |
| 现有 `strategy_config.risk_rules` | **先复制 `params/enabled` 到 `strategy_config.model_risk_rules`（模型纸面风控）**，再复制到 `user_config(admin_id,'risk_rules')`（个人实盘）；两条规则独立；旧行迁移后删除/过滤 |
| 现有 `strategy_config.intraday_rules` / `trade_discipline` | 复制到 `user_config(admin_id, key)`；**复制后立即从通用设置 API 过滤/删除旧行**，避免旧接口继续暴露/改写 |
| 现有 `strategy_config` 其他个人键 | 按白名单迁出；迁移后旧行不得再经通用 API 读写 |
| 现有 `signal_history` 卖出信号 | 复制到 `user_signal_history(admin_id)`（显式列映射、COALESCE、DISTINCT ON 去重），然后从 `signal_history` 删除卖出行；迁移前后行数对账 |
| 文件：`data/agent_ws/<session_id>` | 按 `agent_sessions.user_id` 移到 `data/agent_ws/<user_id>/<session_id>`；旧路径只读兼容一个版本 |
| 文件：`data/skills/*` | 旧用户技能归种子管理员到 `data/skills/<admin_id>/`；全局内置技能保持只读 |
| `paper_*` | 不动 |
| `news_*` / 行情 / 特征 / 模型 | 不动 |
| `xueqiu_status.user_id` | **不是应用用户 owner，禁止迁移/改写**；它是雪球平台用户 ID |
| 新用户 | 创建时自动建默认“自选”组，并播种默认 `intraday_rules`/`risk_rules`/`trade_discipline`；不继承任何个人数据 |

### 7.6 迁移执行顺序（停机窗口）

> 推荐停机迁移，避免“旧代码读新 schema / 新代码读旧 schema”的窗口。单机系统可以接受 3~5 分钟维护。

```
0. 备份：pg_dump -Fc + 校验和；备份 data/agent_ws、data/skills
1. 停止 uvicorn / 前端只读维护页
2. 改好 schema.py 的旧 DDL（旧全局唯一索引/旧视图/默认组播种），与迁移脚本同版本
3. 执行 01_schema_permission.sql（新表 + 角色；权限先 NULL parent_id 插入，再 UPDATE 回填）
4. 执行 02_seed_admin.sql（创建种子管理员；输出一次性密码）
5. 执行 03_add_user_columns.sql（加列，可空）
6. 执行 04_backfill_admin.sql（回填；含 intraday_ai_event/outcome）
7. 执行 05_constraints.sql（NOT NULL、**默认组去重/补建**、唯一索引、主键切换；索引加 IF NOT EXISTS / DO 块）
8. 执行 06_views.sql（**DROP + CREATE** v_focus_pool / v_focus_pool_all）
9. 执行 07_migrate_personal_data.sql（配置、卖出信号；默认组已在 05 处理）
10. 执行 08_migrate_files.sh（agent_ws / skills 目录迁移）
11. 执行 09_verify.sql（§7.7）
12. 部署新代码（与迁移同版本）
13. 启动服务；执行启动自检（路由权限、旧索引/视图断言）
14. 冒烟：登录、持仓、自选、交割单导入、现金/计划/日志、告警、WS、Agent、飞书
15. 观察 30 分钟；保留备份
```

### 7.7 迁移验证 SQL

```sql
-- 个人表 NULL owner
SELECT 'portfolio' t, count(*) FROM portfolio WHERE user_id IS NULL
UNION ALL SELECT 'portfolio_history', count(*) FROM portfolio_history WHERE user_id IS NULL
UNION ALL SELECT 'watch_groups', count(*) FROM watch_groups WHERE user_id IS NULL
UNION ALL SELECT 'trade_slip', count(*) FROM trade_slip WHERE user_id IS NULL
UNION ALL SELECT 'trade_import_batch', count(*) FROM trade_import_batch WHERE user_id IS NULL
UNION ALL SELECT 'trade_intraday', count(*) FROM trade_intraday WHERE user_id IS NULL
UNION ALL SELECT 'trade_cash_snapshot', count(*) FROM trade_cash_snapshot WHERE user_id IS NULL
UNION ALL SELECT 'trade_plan', count(*) FROM trade_plan WHERE user_id IS NULL
UNION ALL SELECT 'trade_journal', count(*) FROM trade_journal WHERE user_id IS NULL
UNION ALL SELECT 'agent_sessions', count(*) FROM agent_sessions WHERE user_id IS NULL
UNION ALL SELECT 'intraday_ai_event', count(*) FROM intraday_ai_event WHERE user_id IS NULL
UNION ALL SELECT 'intraday_signal_outcome', count(*) FROM intraday_signal_outcome WHERE user_id IS NULL
UNION ALL SELECT 'user_signal_history', count(*) FROM user_signal_history WHERE user_id IS NULL;

-- 每个用户 active 持仓重复
SELECT user_id, stock_code, count(*) FROM portfolio
WHERE is_active = true GROUP BY user_id, stock_code HAVING count(*) > 1;

-- 每个用户组名重复
SELECT user_id, group_name, count(*) FROM watch_groups
GROUP BY user_id, group_name HAVING count(*) > 1;

-- 个人表 owner 不存在
SELECT count(*) FROM portfolio p LEFT JOIN users u ON u.id=p.user_id WHERE u.id IS NULL;

-- 权限节点父子合法性
SELECT c.code FROM permissions c LEFT JOIN permissions p ON p.id=c.parent_id
WHERE c.parent_id IS NOT NULL AND p.id IS NULL;

-- 个人权限错误使用 global scope
SELECT p.code FROM role_permissions rp JOIN permissions p ON p.id=rp.permission_id
WHERE p.code LIKE 'portfolio.%' AND rp.scope='global';

-- support 角色的个人读权限必须 support_only=true
SELECT r.code, p.code FROM role_permissions rp
JOIN roles r ON r.id=rp.role_id
JOIN permissions p ON p.id=rp.permission_id
WHERE r.code='support' AND p.code LIKE 'portfolio.%' AND rp.support_only=false;

-- override 的个人权限不得 global
SELECT o.user_id, p.code FROM user_permission_overrides o
JOIN permissions p ON p.id=o.permission_id
WHERE p.code LIKE 'portfolio.%' AND o.scope='global';

-- 卖出信号残留
SELECT count(*) FROM signal_history WHERE direction='sell';

-- 卖出信号迁移对账
SELECT (SELECT count(*) FROM user_signal_history WHERE user_id=:admin_id) AS migrated,
       (SELECT count(*) FROM signal_history WHERE direction='sell') AS remaining;

-- 视图 owner / 列形状
SELECT count(*) FROM v_focus_pool WHERE user_id IS NULL;
SELECT column_name FROM information_schema.columns
WHERE table_name='v_focus_pool' ORDER BY ordinal_position;
-- 期望首列 user_id，第二列 stock_code

-- 旧全局唯一索引/约束必须不存在（否则 init_db 又建回来了）
SELECT indexname FROM pg_indexes
WHERE indexname IN ('idx_portfolio_active_stock','uq_trade_slip_order','uq_trade_slip_hash');
SELECT conname FROM pg_constraint
WHERE conrelid='watch_groups'::regclass AND conname='watch_groups_group_name_key';

-- 每个用户必须有且仅有一个默认组
SELECT user_id, count(*) FROM watch_groups WHERE is_default=true
GROUP BY user_id HAVING count(*) <> 1;

-- 个人权限不得使用 global scope
SELECT p.code FROM role_permissions rp JOIN permissions p ON p.id=rp.permission_id
WHERE p.code LIKE 'portfolio.%' AND rp.scope='global';

-- 模型告警/个人告警分布
SELECT user_id IS NULL AS is_model, count(*) FROM risk_alerts GROUP BY 1;

-- 权限父子回填
SELECT count(*) FROM permissions WHERE parent_id IS NULL AND code NOT IN
  ('portfolio.position.view','portfolio.trades.view',
   'risk.rules.view','risk.alerts.view','watch.group.view',
   'research.treemap.view','research.stock.view','research.signal.view',
   'research.cb.view','research.news.view','research.kepl.view','research.lineage.view',
   'model.view','feature.view','function.view','dag.view','agent.use',
   'system.status.view','system.settings.view','system.audit.view',
   'system.user.manage','system.role.manage','admin.support.view',
   'public.login','public.health','public.version','public.feishu','ws.connect');
```

### 7.8 回滚方案

| 场景 | 回滚 |
|---|---|
| 迁移前 | `pg_dump -Fc` 全库备份；记录备份文件与校验和 |
| 迁移失败 | 停止服务，恢复备份，回退代码，重新启动 |
| 迁移成功但代码异常 | 优先修代码；若必须回退，恢复备份（不要手工反向 DDL） |
| 单表数据异常 | 从备份恢复该表；禁止在缺备份时手工删列 |
| 权限配置错误 | 通过种子脚本重新导入系统角色/权限；用户自定义角色从审计/备份恢复 |

**禁止**：迁移后直接 `DROP COLUMN user_id` 回滚；外键/索引/视图依赖复杂，必须整库恢复。

---

## 8. 业务规则

> 规则编号用于测试用例与代码注释引用。每条规则必须可验证；不可验证的表述不进入实现。

### 8.1 用户与认证

| ID | 规则 | 验证 |
|---|---|---|
| AUTH-01 | 除 `POST /api/login`、`GET /health`、`GET /api/version`、`POST /webhook/feishu`、WS 连接、Agent 签名文件外，所有 `/api` 请求必须带有效 JWT | 匿名请求逐一断言 401；public 白名单显式评审 |
| AUTH-02 | 密码只存 bcrypt/argon2 哈希；任何接口/日志/审计不得出现明文或哈希 | 代码扫描 + 审计抽查 |
| AUTH-03 | 种子管理员首次登录必须改密（`must_change_password=true`） | 登录响应 + 强制跳转 |
| AUTH-04 | 禁用/改密/强制下线使 `token_version+1`，旧 token 立即 401 | 旧 token 重放测试 |
| AUTH-05 | 连续失败 N 次锁定 M 分钟；登录限流按 IP + 用户名 | 并发登录测试 |
| AUTH-06 | 生产必须显式设置 `APP_SECRET_KEY`，默认值拒绝启动 | 启动测试 |
| AUTH-07 | JWT 不含权限列表；只含 uid/username/token_version/perm_version | 解码断言 |
| AUTH-08 | WS 使用短期 ticket 或子协议；JWT 不进 URL query | 抓包/日志检查 |
| AUTH-09 | 飞书 webhook 生产必须校验签名/事件 token；未配置时拒绝；已用事件去重 | 伪造/重放请求测试 |
| AUTH-10 | 用户只能修改自己的密码；管理员重置走独立接口并审计 | 越权测试 |
| AUTH-11 | `/health` 只返回 ok/degraded、数据库状态、版本；不返回 `env`、路径、内部地址 | 响应字段断言 |

### 8.2 权限

| ID | 规则 | 验证 |
|---|---|---|
| PERM-01 | 每个 `/api` 路由必须声明权限码或 public 标记 | 启动自检 + CI 测试 |
| PERM-02 | 权限码必须存在于 `permissions` 表；未知码启动失败 | 启动测试 |
| PERM-03 | 个人节点 `scope` 不允许 `global`；共享节点只允许 `global` | 种子校验 SQL |
| PERM-04 | 用户 deny override 优先于任何 role allow | 矩阵测试 |
| PERM-05 | override 过期后立即失效 | 时间旅行/改系统时间测试 |
| PERM-06 | 角色/权限变更 `perm_version+1`，下一次请求生效 | 变更后立即请求 |
| PERM-07 | 前端隐藏不是安全边界；后端独立校验 | 直接调 API 测试 |
| PERM-08 | 权限缓存异常时 fail-closed（拒绝，不放行） | 注入缓存异常 |
| PERM-09 | 系统角色不可删除；自定义角色删除前检查是否仍被使用 | 删除测试 |
| PERM-10 | 不能把用户提升到比操作者更高的权限（防提权） | operator 尝试授予 model.approve |

### 8.3 数据隔离

| ID | 规则 | 验证 |
|---|---|---|
| DATA-01 | 所有个人表查询必须使用 `principal.data_user_id`，禁止直接用手写 `user_id` 参数 | 代码审查 + 查询日志 |
| DATA-02 | 跨用户访问已存在资源返回 404，不返回 403/资源详情 | 越权测试 |
| DATA-03 | 跨用户读只能通过支持会话；支持会话只读 | 支持模式测试 |
| DATA-04 | 支持模式不能读 `user_config`、Agent 会话、密码/身份信息 | 支持模式访问测试 |
| DATA-05 | Agent 原始 SQL 工具仅 admin，且只允许共享表/视图白名单；个人表禁止；其他角色 403 | 工具调用 + 表白名单测试 |
| DATA-06 | Agent 沙箱路径为 `data/agent_ws/<user_id>/<session_id>/`；文件签名绑定 user_id；无签名 fallback 校验 session owner | 路径/签名/越权测试 |
| DATA-07 | WS 连接绑定 user_id；个人告警只推 owner；模型告警只推 admin/operator | 双客户端测试 |
| DATA-08 | 进程内缓存 key 必须包含 user_id + perm_version | 缓存键检查 |
| DATA-09 | 导出/下载只包含当前用户有权限的行；支持模式导出默认禁止 | 导出测试 |
| DATA-10 | 新闻采集用 `v_focus_pool_all`；新闻读取用当前用户 `v_focus_pool` | 采集/读取测试 |
| DATA-11 | 全局买入信号共享；个人卖出信号写 `user_signal_history`；模型健康只统计全局买入；持仓页通过 `signal.personal.view` 合并展示个人卖出 | 数据分布 + 页面测试 |
| DATA-12 | `paper_*` 是模型账本，不按用户过滤；前端必须标注“模型纸面” | UI 文案检查 |

### 8.4 监测与分析

| ID | 规则 | 验证 |
|---|---|---|
| MON-01 | 盘中行情按全体用户关注池并集拉取，不按用户重复请求 | 外部请求计数 |
| MON-02 | 每个用户独立评估规则、独立冷却、独立单日上限 | 双用户同股测试 |
| MON-03 | 个人告警 `risk_alerts.user_id` 必填；模型告警为 NULL；**去重/冷却/单日上限 SQL 必须带 user_id** | 双用户同股同因告警测试 |
| MON-04 | 盘中提醒、AI 事件只推给告警 owner | WS 测试 |
| MON-05 | 雷达/买点候选使用当前用户关注池 + 当前用户实时持仓/纪律 | 双用户测试 |
| MON-06 | 日频用户风控只评用户实盘；模型纸面风控单独运行 | 任务输出断言 |
| MON-07 | 模型信号先全局预测一次，再按用户持仓生成个人卖出信号 | 运行日志/行数 |
| MON-08 | 新闻 LLM 总结全局一次，不按用户重复总结 | LLM 调用计数 |
| MON-09 | 用户关闭盘中提醒后，不评估该用户规则，但全局分钟线采集可继续 | 开关测试 |
| MON-10 | 用户数增长时行情请求数只随并集池增长，不随用户数线性增长 | 压测 |
| MON-11 | 模型告警 ack 是全局语义（任一 A/O 确认即对所有 A/O 隐藏）；个人告警 ack 只影响本人 | 双 operator ack 测试 |
| MON-12 | `intraday_signal_outcome` 按 user_id 归属；全局信号统计按 `(stock_code, kind, alert_time)` 去重，避免多用户同股同 kind 重复计数 | 双用户同股统计断言 |

### 8.5 配置与管理

| ID | 规则 | 验证 |
|---|---|---|
| CFG-01 | 个人配置写 `user_config`；系统/模型配置留 `strategy_config`；模型纸面风控用全局 `model_risk_rules`，个人实盘用 `user_config.risk_rules` | 数据断言 + 模型任务测试 |
| CFG-02 | `global_preference` 保持全局，影响共享信号生成；用户不能改 | API 403 测试 |
| CFG-03 | LLM Key 仅 admin 可读写；读取永远脱敏 | 越权/脱敏测试 |
| CFG-04 | 历史补数受 TuShare 配额保护；低于阈值拒绝启动 | 配额模拟测试 |
| CFG-05 | DAG 发布/执行/终止分离授权；operator 默认可执行，admin 可发布 | 矩阵测试 |
| CFG-06 | 模型审批与训练可分离（operator 训练、admin 审批） | 角色测试 |
| CFG-07 | 支持会话必须填理由、限时、可撤销、全量审计 | 支持模式测试 |
| CFG-08 | 所有敏感配置变更写审计，审计失败则操作失败（fail-closed） | 注入审计异常 |

### 8.6 路由覆盖

| ID | 规则 | 验证 |
|---|---|---|
| ROUTE-01 | 路由声明格式统一：`@router.get(..., permission="portfolio.position.view")` | 代码规范检查 |
| ROUTE-02 | 启动时遍历 `app.routes`，未声明权限且非 public 的路由直接报错 | 启动测试 |
| ROUTE-03 | CI 增加“路由覆盖测试”，新增路由未声明权限则失败 | CI |
| ROUTE-04 | public 路由必须显式列入白名单；白名单变更需评审 | 代码审查 |
| ROUTE-05 | 路由权限码与前端菜单权限码使用同一常量源 | 生成/对比测试 |

### 8.7 审计

| ID | 规则 | 验证 |
|---|---|---|
| AUD-01 | 审计只插入，不更新/删除；管理端只读 | API 测试 |
| AUD-02 | 权限/角色/用户变更、敏感操作、支持访问必须审计 | 事件覆盖测试 |
| AUD-03 | 密码/token/API Key 在 before/after 中脱敏 | 审计抽查 |
| AUD-04 | 审计失败时敏感操作 fail-closed | 注入异常 |
| AUD-05 | 审计保留至少 180 天；建议按月归档 | 运维检查 |
| AUD-06 | 支持访问审计包含操作者、目标用户、理由、资源、IP、时间 | 支持模式测试 |

---

### 8.8 R3/R4 审查新增规则

> 来源：权限/数据两轮反向审查。每条都对应一个已知现状缺陷，开发前必须修复或显式接受风险。

| ID | 规则 | 关联缺陷 | 验证 |
|---|---|---|---|
| WB-01 | 所有个人表 INSERT/UPDATE 必须写 owner；以 §7.3.3 的“写入路径清单”逐函数勾选，不以文件为单位 | `trade_slip/trade_intraday/portfolio/agent_sessions/watch_groups` 写入点无 owner | 双用户端到端：导入/记录/现金/计划/日志/建组/会话 |
| WB-02 | `schema.py` 旧 DDL 必须删除/改写；启动后断言不存在旧全局唯一索引/旧视图 | `init_db` 每次启动重建 `idx_portfolio_active_stock`、`uq_trade_slip_*`、`watch_groups_group_name_key` | 迁移后重启，再插两用户同股/同文件 |
| WB-03 | 视图列形状变化必须 `DROP VIEW` + `CREATE VIEW`；禁止 `CREATE OR REPLACE` 改首列 | PostgreSQL 报 `cannot change name of view column` | 迁移脚本实际执行 |
| WB-04 | `trade_cash_snapshot/trade_plan/trade_journal` 的 `ON CONFLICT` 目标改为 `(user_id, trade_date)`；所有 SELECT/UPDATE 带 user | 迁移后现金/计划/日志 500 或串户 | I12/I13 |
| WB-05 | `set_stock_groups` 的批量 DELETE/INSERT 必须限定当前用户组；不能只 `COUNT(*)` 校验 gid | 用户 A 保存分组会删 B 的成员或插进 B 的组 | I6–I9 |
| WB-06 | `intraday_reconcile(db, user_id)`：`MAX(trade_date) FROM trade_slip WHERE user_id`；UPDATE/DELETE 带 user | A 导入交割单会标记 B 的手工记录/删 B 的现金 | 双用户导入测试 |
| WB-07 | 个股 quote/levels 缓存只缓存共享行情；个人成本/止损/止盈按 user 合并或 key 加 user | 3 秒/60 秒缓存把 A 的成本给 B | 双用户同股 1 秒内连续请求 |
| WB-08 | `risk_alerts` 去重/冷却/单日上限/ack 全部带 user_id；模型告警走 `user_id IS NULL` 通道 | A 的告警抑制 B；A 一次 ack 确认所有人 | 双用户同股同因 + ack 测试 |
| WB-09 | 模型纸面风控用 `strategy_config.model_risk_rules`；个人实盘风控用 `user_config.risk_rules`；两个函数分开 | 迁移后模型风控无规则或读到个人规则 | 模型任务 + 个人规则测试 |
| WB-10 | `user_signal_history` 迁移显式列映射、COALESCE、DISTINCT ON 去重、行数对账 | NULL/重复导致迁移失败或丢字段 | M6 |
| WB-11 | 新用户创建时播种默认组 + `intraday_rules/risk_rules/trade_discipline`；盘中评估用 `users LEFT JOIN user_config`，缺行按默认启用 | 新用户默认监测不生效 | 新用户无配置也收到提醒 |
| WB-12 | 迁移 `data/agent_ws` 与 `data/skills`；旧路径只读兼容一个版本 | 旧会话图片 404、技能互相覆盖 | 文件迁移测试 |
| WB-13 | dbexplorer 只允许共享/系统表白名单；个人表禁止；敏感列永不返回 | 管理员绕过支持会话读个人数据/密码哈希/LLM Key | 表/列白名单测试 |
| WB-14 | Agent 工具按 §6.2.5 校验底层权限；不能靠 `agent.use` 绕过 `dag.edit/dag.execute/portfolio.*` | 普通用户通过 Agent 建/跑 DAG、读全量持仓 | 工具调用矩阵测试 |
| WB-15 | `agent.exec` 默认仅 admin；执行清空 `DATABASE_URL/APP_SECRET_KEY/TUSHARE_TOKEN/LLM Key`；只挂载会话工作区，不挂项目根/.env；**禁网、禁本地 PostgreSQL socket、禁 DB 驱动**；bwrap 不可用 fail-closed；pip/npm 默认禁用/容器内 | 一句“打印环境变量”/读 `.env`/连本地 DB 拿到个人数据 | 环境变量/沙箱/文件挂载/DB 连接测试 |
| WB-16 | Agent 文件签名绑定 `user_id+session_id+rel`；无签名 fallback 校验 session owner | 任意 JWT 可读他人会话图片 | 越权文件测试 |
| WB-17 | Agent/AI 每日配额写 `agent_usage_daily`；`ai-intraday`、`function.ai_chat` 也计入；`MAX_STEPS` 设硬上限 | 一条消息无限循环/刷 LLM 费用 | 配额/步数测试 |
| WB-18 | `intraday_ai_event` 增 `user_id`（或强制 join alert）；冷却/日限/WS 按 owner | 同股双用户 AI 事件互相抑制/泄露 | 双用户同股 AI 测试 |
| WB-19 | `intraday_signal_outcome` 增 `user_id`（或强制 join alert）；统计按 owner | 同股双用户重复评估/统计翻倍 | 结果追踪测试 |
| WB-20 | `dag_run_log` 增 `created_by`；TaskManager 任务带 owner；`dag.terminate` 校验 owner/admin | 任何有 terminate 的人可终止他人任务 | 双用户 DAG 测试 |
| WB-21 | 删除/归档模型时同步清理/归档 `user_signal_history` 对应 `model_version` | 已删模型信号残留 | 模型删除测试 |
| WB-22 | 迁移后旧 `strategy_config` 个人键必须从通用设置 API 过滤/删除；不能保留可读写 | 旧键仍全局可读可写，改了不生效 | 设置 API 测试 |
| WB-23 | `news/health` 的 `focus_n` 用 `v_focus_pool_all` / `DISTINCT stock_code` | 多用户共持同一股票时数量虚高 | 统计测试 |
| WB-24 | `GET /api/version` 改为 public（只返回构建哈希）；前端 version.js 无 token 轮询 | 加权限后所有普通用户版本自检 401 | 前端自检 |
| WB-25 | WS 消息按类型过滤：告警→owner；DAG→`dag.view`；补数/指标→`system.status.view`；AI→owner | 普通用户收到 DAG/主机指标/他人 AI | 双客户端 |
| WB-26 | 飞书生产必须验签/校验事件 token；未配置拒绝；解析 `open_id` 绑定；卡片按底层权限；消息走回复 API | 伪造事件写持仓/改全局偏好 | 飞书签名/绑定/重放测试 |
| WB-27 | `xueqiu_status.user_id` 不是应用 owner，迁移时排除 | 机械迁移把雪球用户 ID 当应用用户 | 迁移脚本审查 |
| WB-28 | `app/api/market.py` 只读共享表，不做 owner 改造；只加权限码 | 误加 user 过滤导致市场数据为空 | 代码审查 |
| WB-29 | 迁移 DDL 幂等：索引 `IF NOT EXISTS`、PK 切换用 `DO` 块、种子 `ON CONFLICT` | 重跑在已建索引/主键处失败 | M10 |
| WB-30 | 支持模式双凭证 + 资源级 `support_readable`；`user_config`/Agent/身份信息不可读；每次请求校验撤销/过期 | 令牌单独可用、撤销后缓存仍放行 | S1–S8 |

### 8.9 端到端业务场景核验

> 每个场景都是开发后的验收脚本；不通过不得进入下一阶段。

| # | 场景 | 步骤 | 期望 |
|---|---|---|---|
| E1 | 开通用户 | admin 创建 A（trader）、B（analyst）；系统自动建默认组/默认规则 | A/B 各自有“自选”组；A 有持仓/交割单权限，B 没有；互不继承数据 |
| E2 | A 建仓/导入交割单 | A 导入交割单 → 同步持仓 → 查看实时持仓 | A 的 portfolio/trade_slip/trade_intraday 更新；B 的数据不变；A 的盘中监测使用 A 的成本 |
| E3 | 自选/雷达隔离 | A、B 都建名为“自选”的组，加入不同股票 | 两个组都成功；A 的雷达/买点只含 A 的池；B 只含 B 的池 |
| E4 | 盘中告警隔离 | A、B 持有同一股票但成本不同；触发止损 | 各自收到自己的止损提醒；冷却/单日上限各自计算；WS 不串 |
| E5 | 日频信号/风控 | 模型产出买入信号；A/B 持仓不同 | 买入信号全局共享；A/B 卖出信号各自写入 `user_signal_history`；个人风控各写各的 `risk_alerts`；模型告警仅 A/O 可见 |
| E6 | 管理员支持模式 | admin 创建支持会话（目标 A，理由，30 分钟）→ 查看 A 持仓 → 尝试编辑 → 撤销 | 只读可看 A 持仓；写 403；每次访问有审计；撤销后立即 401/403 |
| E7 | 权限回收 | admin 移除 A 的 trader 角色 | A 下一次请求个人接口 403；WS 收到权限变更/需重连；前端菜单刷新 |
| E8 | 飞书/Agent 隔离 | A/B 分别绑定 open_id；各自问“我的持仓” | 回复各自数据；会话列表互不可见；Agent 工具按权限返回；`agent.exec` 仅 admin |
| E9 | 新闻采集/读取 | A/B 关注池不同 | 采集按并集；A 的 `only_focus` 只显示 A 的池；B 只显示 B 的池；LLM 总结只做一次 |
| E10 | 数据迁移 | 按 §7.6 停机迁移 → 重启 → 双用户操作 | 旧数据全归 admin；旧全局唯一索引/视图不再出现；A/B 可同时加同股/同名组/同日现金 |

## 9. 监测与分析链路设计

### 9.1 盘中监测：单行情循环 + 多用户评估

```
每 10 秒（交易时段）
  │
  ├─ 1. 读取启用提醒的用户集合（缺配置按默认启用，不能漏新用户）
  │      SELECT u.id
  │      FROM users u
  │      LEFT JOIN user_config c
  │        ON c.user_id=u.id AND c.config_key='intraday_rules'
  │      WHERE u.status='active' AND COALESCE(c.enabled, true)
  │
  ├─ 2. 取全体用户关注池并集
  │      SELECT DISTINCT stock_code FROM v_focus_pool
  │      JOIN stock_master ... stock_type IN ('stock','etf')
  │
  ├─ 3. 批量拉取腾讯快照（每批 <=50 只，分页）
  │      quotes = {code: {price, high, low, volume, amount, ...}}
  │
  ├─ 4. 对每个用户并行/串行评估：
  │      ├─ pool = SELECT stock_code FROM v_focus_pool WHERE user_id=:uid
  │      ├─ live = live_positions(db, uid)         # 交割单 + 今日手工
  │      ├─ rules = get_intraday_rules(db, uid)    # user_config
  │      ├─ evaluate_tick(quotes, pool, live, rules)
  │      ├─ cooldown per (uid, code, kind)
  │      ├─ INSERT risk_alerts(user_id=uid, ...)
  │      └─ enqueue intraday_ai_event(user_id=uid, alert_id=...)
  │
  └─ 5. WS 广播：
         SELECT ... FROM risk_alerts WHERE id > cursor ORDER BY id
         for each alert:
             send only to connections where conn.user_id = alert.user_id
         model alerts (user_id IS NULL):
             send only to admin/operator connections
```

**实现要点**：

| 点 | 设计 |
|---|---|
| 行情拉取 | 只拉并集一次；用户池是并集子集，复用 quotes |
| 规则读取 | `user_config(user_id,'intraday_rules')`；无记录用默认值 |
| 实时持仓 | `live_snapshot(db, user_id)`；不能用 `portfolio` 代替 |
| 冷却 | 查询 `risk_alerts WHERE user_id=:uid AND ...`；内存冷却 key 含 uid |
| 单日上限 | 按 user 统计，不共享全局计数 |
| AI 事件 | `intraday_ai_event` 增 `user_id`（或 join alert）；队列按用户隔离 |
| 性能 | 用户数 <=50 时串行可接受；超过则按用户分片到线程池，行情仍共享 |
| 失败隔离 | 单个用户评估异常只记录日志，不影响其他用户 |

### 9.2 日频模型信号：全局预测一次，个人卖出信号

```
model_signal（每日）
  │
  ├─ 1. 加载 ACTIVE 模型 + 全市场特征宽表
  ├─ 2. 全市场预测一次（5d/10d/20d）
  ├─ 3. 写全局买入信号 signal_history(direction='buy')
  ├─ 4. （可选）写 model_score_daily 全市场分数/分位
  ├─ 5. 对每个用户：
  │      ├─ held = live_positions(db, uid)   # 交割单 + 今日手工，不能用 portfolio 代替
  │      ├─ 对 held 读取当日分数/分位
  │      ├─ 触发卖出条件 → INSERT user_signal_history(user_id, direction='sell')
  │      └─ 幂等：先删当日该用户该模型卖出行，再插入
  └─ 6. 模型健康只统计 signal_history(direction='buy')
```

**为什么不能继续写全局 `signal_history` 卖出**：

- 多用户后，同一股票可能被 A 持有、B 不持有；全局卖出行无法表达 owner。
- `model_health` 的统计、`/api/signal/stats`、树图 overlay 都会被个人卖出行污染。
- 个人卖出信号需要按用户展示、按用户了结；独立表更清晰。

### 9.3 用户实盘日频风控（新增）

当前 `app/risk.py` 评的是 `paper_positions`。多用户后拆成两条：

```
model_risk_eval（全局，模型账本）
  ├─ 规则：strategy_config('model_risk_rules')，仅系统任务读写
  ├─ 对象：paper_positions / paper_trades
  └─ 写 risk_alerts(user_id=NULL, kind='stop_loss'/'drawdown'/...)
      仅 admin/operator 可见（model.alerts.view）

user_risk_eval（每日收盘后，按用户）
  ├─ 规则：user_config(uid,'risk_rules')
  ├─ 持仓：live_snapshot(db, uid)（交割单 + 今日手工），portfolio 只作账本展示
  ├─ 止损：成本 vs 最新有效收盘
  ├─ 回撤：用户账户净值曲线（trade_slip 重放 / 快照）
  ├─ 行业上限：按 industry_l1 聚合
  ├─ 解禁：stock_share_float
  └─ 写 risk_alerts(user_id=uid, kind='user_stop_loss'/'user_drawdown'/...)
```

模型纸面风控继续保留，写 `user_id=NULL`，管理员/operator 查看；不要与用户告警混在同一列表。**`risk_rules` 必须拆成两个键**：`strategy_config.model_risk_rules`（模型）与 `user_config.risk_rules`（个人）。

### 9.4 新闻采集与读取

| 阶段 | 范围 | 说明 |
|---|---|---|
| 采集 | `v_focus_pool_all`（全体用户并集） | 只影响“采哪些票的新闻”，不区分用户 |
| 去重/原文 | 全局 | `(source, source_id)` 幂等 |
| LLM 总结 | 全局一次 | 所有用户共享摘要/重要度 |
| 列表读取 | 当前用户 `v_focus_pool` | `only_focus=true` 只返回自己的关注池 |
| 标记 | 当前用户 | `focus_linked` 按当前用户计算 |
| 健康计数 | `v_focus_pool_all` / `DISTINCT stock_code` | `news/health` 的 `focus_n` 不能按用户×股票重复计数 |

### 9.5 雷达 / 买点候选

`app/api/stock.py:get_intraday_radar` 与 `app/buy_check.py:candidates`：

- 关注池：`v_focus_pool WHERE user_id=:uid`
- 实时持仓/现金：`live_snapshot(db, uid)`
- 纪律规则：`user_config(uid,'trade_discipline')`
- 近期卖出：`trade_slip/trade_intraday WHERE user_id=:uid`
- 输出 `is_holding/qty/cost/weight` 只属于当前用户
- 入口：实时页 `#/rt` 的持仓区（雷达表 + 持仓动作行）与机会区（买点候选 → 买点检查弹窗），见 design/14；
  页面入口由容器节点 `research.realtime.view` 控制，本节两个端点仍归 `research.radar.view`

### 9.6 树图与纸面组合

- 树图数据全局共享，不做用户过滤。
- 前端叠加：
  - “我的持仓”：`GET /api/portfolio`（已按用户隔离）。
  - “模型纸面”：`GET /api/v1/models/paper-portfolio`（全局模型账本）。
  - 图例明确区分，避免用户误以为纸面组合是自己的模拟盘。

### 9.7 告警与 WS

| 消息类型 | 数据来源 | 过滤规则 |
|---|---|---|
| `risk_alert`（个人 `it_*`/`user_*`） | `risk_alerts.user_id` | 只推 owner |
| `risk_alert`（模型/系统） | `risk_alerts.user_id IS NULL` | 只推有 `model.alerts.view` 的 admin/operator |
| `intraday_ai` | `intraday_ai_event.user_id` 或 join alert | 只推 owner |
| `dag_status` / `dag_log` / `task_progress` | 全局任务 | 只推有 `dag.view` 的连接 |
| `backfill_progress` / `sys_metrics` | 全局运维 | 只推有 `system.status.view` 的连接 |
| `connection` / `pong` | 连接本身 | 所有已认证连接 |

WS 连接对象：

```python
class WSClient:
    websocket: WebSocket
    user_id: int
    role_codes: set[str]
    perm_version: int
    support_target_id: int | None  # 支持模式下可选
```

广播时按 `alert.user_id` 过滤；`user_id=NULL` 只发给 `admin/operator`。

### 9.9 交易/持仓链路的用户上下文（易漏点）

| 链路 | 现状风险 | 改法 |
|---|---|---|
| `intraday_reconcile` | 导入 A 的交割单会把 B 的手工记录标记已对账、删 B 的现金快照 | `reconcile(db, user_id)`；`MAX(trade_date)`/UPDATE/DELETE 全部带 user |
| `trade_review.build_review/today_gate` | 复盘、账户汇总、今日闸门合并所有用户交割单 | 传 user；`load_slip_rows` 过滤；现金快照按 user |
| `execution_quality` | 卖出质量/提醒执行率/情绪统计跨用户 | 全函数加 user |
| `portfolio.risk-heat` | 告警计数跨用户；行业上限读全局规则 | SQL 加 `user_id`；读 `user_config.risk_rules`；缓存 key 加 user |
| `stock._quote_levels/_quote_cache/_levels_cache` | 成本/止损/止盈缓存跨用户 | 只缓存共享行情；个人价位缓存 key 加 user 或缓存外合并 |
| `stock` 最近 AI 事件 | 按股票取全局最新，看到他人事件 | join `risk_alerts.user_id` 或按 `intraday_ai_event.user_id` |
| `watch.set_stock_groups` | 批量 DELETE/INSERT 不校验 owner | gid 必须来自当前用户组；DELETE/INSERT 都带 owner |
| `models.delete_model` | 只删 `signal_history`，个人卖出信号残留 | 清理/归档 `user_signal_history` 对应 `model_version` |
| `news/health` | `count(*) FROM v_focus_pool` 按用户×股票重复 | 用 `v_focus_pool_all` / `DISTINCT stock_code` |
| `agent.tools` | 工具只拿 session_id，读全局个人表 | 工具调度传 `Principal`；按 §6.2.5 校验 |
| `dbexplorer` | 可读全库，含密码哈希/LLM Key/个人表 | 表/列白名单；个人表禁止；支持会话专用 API |
| `feishu` | 忽略 open_id；全局偏好可被任意用户改 | 绑定用户；卡片按底层权限；`global_preference` admin-only |
| `intraday_ai` | 规则/冷却/日限/消息全局 | 按 owner 读规则、冷却、限额、推送 |
| `dag_run_log` | 无发起者，terminate 无法校验 owner | 增 `created_by`；TaskManager 任务带 owner |

### 9.8 容量与性能

| 指标 | 假设 | 设计 |
|---|---|---|
| 用户数 | 2~50 | 单进程 + 线程池/串行 |
| 并集关注池 | 几百到几千 | 腾讯批量 <=50/请求；10s 一轮 |
| 每用户规则评估 | 池子大小 × 规则数 | 纯内存计算，DB 只写告警 |
| 每日个人信号 | 用户数 × 持仓数 | 全局预测一次，个人只做阈值比较 |
| 每日用户风控 | 用户数 × 持仓数 | 可合并到一个任务循环 |
| 告警写入 | 每用户每日上限 | 按用户限流，避免刷库 |

---

## 10. API 设计

### 10.1 新增 API

| 方法+路径 | 权限 | 说明 |
|---|---|---|
| `GET /api/me` | 登录 | 返回 user + roles + permissions + scopes + perm_version |
| `POST /api/me/password` | 登录 | 修改自己的密码 |
| `POST /api/me/reauth` | 登录 | 二次认证，签发短期 step-up 票据（用于 `model.approve`） |
| `GET /api/me/settings` | 登录 | 个人设置（`ui_preference` 等） |
| `PUT /api/me/settings` | 登录 | 保存个人设置；不写全局 `strategy_config` |
| `GET /api/me/support-access` | 登录 | 目标用户查看“谁在何时支持访问过我”（可选） |
| `POST /api/risk/alerts/ack` | `risk.alerts.ack` | 确认自己的告警（支持模式 403） |
| `GET /api/signals/personal` | `signal.personal.view` | 个人卖出/个人信号（owner 过滤） |
| `GET /api/admin/users` | `system.user.manage` | 用户列表（分页） |
| `POST /api/admin/users` | `system.user.manage` | 创建用户 |
| `PATCH /api/admin/users/{id}/status` | `system.user.manage` | 禁用/启用 |
| `POST /api/admin/users/{id}/reset-password` | `system.user.manage` | 重置密码 |
| `PUT /api/admin/users/{id}/roles` | `system.user.manage` | 分配角色（支持 `expires_at`；防提权） |
| `GET /api/admin/users/{id}/permissions` | `system.user.manage` | 用户 override 列表 |
| `PUT /api/admin/users/{id}/permissions` | `system.user.manage` | 设置 allow/deny override（含 scope/conditions/expires_at） |
| `DELETE /api/admin/users/{id}/permissions/{permission_id}` | `system.user.manage` | 删除 override |
| `GET /api/admin/roles` | `system.role.manage` | 角色列表 |
| `POST /api/admin/roles` | `system.role.manage` | 创建角色 |
| `PUT /api/admin/roles/{id}` | `system.role.manage` | 修改角色 |
| `DELETE /api/admin/roles/{id}` | `system.role.manage` | 删除自定义角色 |
| `GET /api/admin/roles/{id}/permissions` | `system.role.manage` | 角色权限树 |
| `PUT /api/admin/roles/{id}/permissions` | `system.role.manage` | 保存角色权限（含 scope/conditions） |
| `GET /api/admin/permissions` | `system.role.manage` | 功能节点树 |
| `GET /api/admin/audit` | `system.audit.view` | 审计查询 |
| `POST /api/admin/support/sessions` | `admin.support.view` | 创建支持会话 |
| `DELETE /api/admin/support/sessions/{id}` | `admin.support.view` | 撤销支持会话 |
| `GET /api/admin/support/sessions` | `admin.support.view` | 我的支持会话 |

### 10.2 现有 API 的权限映射（按节点分组）

> 完整路径以 §10.3 的覆盖检查为准。下表是开发实现时的映射基线。

| 权限节点 | 方法 + 路径 |
|---|---|
| `public.login` | `POST /api/login` |
| `public.health` | `GET /health` |
| `public.version` | `GET /api/version` |
| `public.feishu` | `POST /webhook/feishu` |
| `ws.connect` | `WS /api/ws/dag`（连接后按消息类型过滤） |
| `portfolio.position.view` | `GET /api/portfolio`、`GET /api/portfolio/risk-heat`、`GET /api/portfolio/watch-window` |
| `portfolio.position.edit` | `POST /api/portfolio`、`PUT /api/portfolio/{stock_code}`、`DELETE /api/portfolio/{stock_code}` |
| `portfolio.history.view` | `GET /api/portfolio/{stock_code}/history` |
| `portfolio.watch_window.launch` | `POST /api/portfolio/watch/prepare`、`POST /api/portfolio/watch/launch` |
| `portfolio.trades.view` | `GET /api/portfolio/trades`、`/intraday/state`、`/intraday/quote`、`/journal`、`/reduction-plan`、`/execution-quality`、`/account-summary`、`/review`、`/batches` |
| `portfolio.trades.import` | `POST /api/portfolio/trades/import`、`POST /apply-positions`、`DELETE /batches/{batch_id}` |
| `portfolio.trades.intraday` | `POST /intraday/check`、`POST /intraday/record`、`PUT /intraday/cash`、`DELETE /intraday/{action_id}` |
| `portfolio.trades.journal.edit` | `PUT /api/portfolio/trades/journal` |
| `portfolio.discipline.view` | `GET /api/portfolio/trades/discipline/rules`、`GET /api/portfolio/trades/discipline/today` |
| `portfolio.discipline.edit` | `PUT /api/portfolio/trades/discipline/rules` |
| `risk.rules.view` | `GET /api/risk/rules`、`GET /api/risk/intraday-rules` |
| `risk.rules.edit` | `POST /api/risk/rules`、`POST /api/risk/intraday-rules`、`POST /api/risk/evaluate-personal` |
| `risk.alerts.view` | `GET /api/risk/alerts`（`ack=false`） |
| `risk.alerts.ack` | `POST /api/risk/alerts/ack`（旧 `GET ?ack=true` 仅兼容） |
| `watch.group.view` | `GET /api/watch/groups`、`/watch/groups/{id}/stocks`、`/watch/stock-groups` |
| `watch.group.edit` | `POST/DELETE /api/watch/groups`、`PUT /api/watch/stock-groups` |
| `research.realtime.view` | （容器页，无独立端点——分区端点见下面三行；只授本码的角色看得到空壳、拿不到任何数据） |
| `research.treemap.view` | `GET /api/treemap_data`、`/treemap_intraday`、`/treemap_latest_date`、`/market_sentiment`、`/sector_flow_history` |
| `research.market.view` | `GET /api/market/overview`、`/valuation/{ts_code}`、`/moneyflow`、`/breadth`、`/margin`、`/macro`、`/sentiment-history`、`/ratio-percentile`、`/realtime`、`/realtime/zt-history` |
| `research.stock.view` | `GET /api/stocks`、`/stocks/suggest`、`/stock/{code}/detail`、`/history`、`/kline`、`/pe_history`、`/moneyflow`、`/signals`、`/margin`、`/top_list`、`/chip`、`/xueqiu`、`/quote`、`/ai-intraday*`、`/tape`、`/sector-rs`、`/minkline` |
| `research.signal.view` | `GET /api/buy_signals`、`GET /api/signal/stats`、`GET /api/risk/signal-stats`、`GET /api/risk/signal-outcomes`（全局统计按 `(stock_code,kind,alert_time)` 去重） |
| `signal.personal.view` | `GET /api/signals/personal` |
| `model.alerts.view` | `GET /api/risk/alerts?scope=model` |
| `model.alerts.ack` | `POST /api/risk/alerts/ack?scope=model` |
| `model.risk.evaluate` | `POST /api/risk/evaluate` |
| `research.cb.view` | `GET /api/cb/*`、`/stock/{code}/convertible-bonds`、`/stock/{code}/cb-linkage` |
| `research.news.view` | `GET /api/news`、`/news/health`、`/news/{id}`、`/stock/{code}/news` |
| `research.radar.view` | `GET /api/stock/radar`、`/buy/check`、`/buy/candidates` |
| `research.kepl.view` | `GET /api/kepl/functions`、`POST /api/kepl/parse` |
| `research.lineage.view` | `GET /api/lineage`、`/lineage/model/{version}` |
| `model.view` | `GET /api/v1/models`、`/{v}`、`/{v}/signals`、`/paper-portfolio`、`/{v}/paper-trades`、`/{v}/trade-rules`、`/{v}/health`、`/{v}/diagnosis`、`/{v}/delete-check`、`/{v}/feature-check`、`/{v}/quality-dashboard`、`/{v}/permutation`、`/{v}/walk-forward`、`/{v}/strategy-scan/{task_id}` |
| `feature.view` | `GET /api/features`、`/fields`、`/groups`、`/tags`、`/dependency-graph`、`/ic/board`、`/ic/correlation`、`/{id}`、`/{id}/quality`、`/{id}/data`、`/{id}/missing-heatmap`、`/{id}/compute-status`、`/{id}/ic`、`/{id}/ic/task/{task_id}` |
| `function.view` | `GET /api/functions`、`/{id}`、`/{id}/versions` |
| `dag.view` | `GET /api/dag/flows`、`/{id}`、`/{id}/task-status`、`/{id}/versions`、`/dag/logs`、`/dag/node-types*`、`GET /api/dag_status`、`/dag_logs`、`/dag_config` |
| `agent.use` | `/api/agent/sessions*`、`/messages`、`/chat`、`/upload`、`/stop`、`/files/*`、`/info`、`GET /skills`、`GET /skills/{name}`、`POST /feedback` |
| `agent.feedback.view` | `GET /api/agent/feedback` |
| `agent.skill.edit` | `POST/DELETE /api/agent/skills` |
| `agent.skill.import` | `POST /api/agent/skills/import/preview`、`/confirm` |
| `model.train` | `POST /api/v1/models/{v}/train`、`/retrain`、`/stop` |
| `model.approve` | `POST /api/v1/models/{v}/approve`、`/reject`、`/promote` |
| `model.archive` | `POST /api/v1/models/{v}/archive`、`DELETE /api/v1/models/{v}` |
| `model.config.edit` | `POST /api/v1/models`、`PUT /api/v1/models/{v}/config` |
| `model.evaluate` | `POST /api/v1/models/{v}/attribution`、`/permutation`、`/walk-forward`、`/strategy-scan` |
| `model.regenerate` | `POST /api/v1/models/{v}/regenerate-day` |
| `model.scan.apply` | `POST /api/v1/models/{v}/strategy-scan/{task_id}/apply` |
| `feature.edit` | `POST /api/features`、`PUT/DELETE /api/features/{id}`、`PATCH /{id}/status`、`PUT /{id}/ic-status` |
| `feature.compute` | `POST /api/features/validate`、`/{id}/stats`、`/{id}/compute-range`、`/{id}/recompute-stats`、`/check-stats-integrity`、`/{id}/ic` |
| `function.edit` | `POST /api/functions`、`PUT/DELETE /{id}`、`POST /{id}/rollback/{version}` |
| `function.test` | `POST /api/functions/test-run-temp`、`/{id}/test-run` |
| `function.ai_chat` | `POST /api/functions/ai-chat` |
| `dag.edit` | `POST/PUT/DELETE /api/dag/flows`、`POST /api/dag/flows/validate` |
| `dag.publish` | `POST /api/dag/flows/{id}/publish`、`/unpublish` |
| `dag.execute` | `POST /api/dag/flows/{id}/execute`、`POST /api/dag_trigger` |
| `dag.terminate` | `POST /api/dag_terminate` |
| `system.status.view` | `GET /api/data_status`、`/trade_calendar`、`/data_status/sync_status`、`/data-sources/health`、`/tushare_quota`、`/minute-data`、`/xueqiu`、`/system/metrics`、`/stock_fund_list` |
| `system.settings.view` | `GET /api/settings`、`GET /api/settings/preference`、`GET /api/llm_config`、`GET /api/llm_config/metrics` |
| `system.settings.edit` | `POST /api/settings/toggle_strategy`、`POST /api/settings/preference`、`/update_params`、`/deepseek_key`、`POST /api/llm_config`、`/llm_config/test` |
| `system.dbexplorer.view` | `GET /api/dbex/overview`、`/tables`、`/tables/{table}/columns`、`/tables/{table}/rows`（仅共享/系统表白名单） |
| `system.backfill.execute` | `POST /api/data_status/backfill`、`POST /data_status/backfill/{id}/cancel` |
| `system.backfill.view` | `GET /api/data_status/backfill/history`、`/logs` |
| `system.stats.refresh` | `POST /api/refresh_stats` |
| `system.sync_date` | `POST /api/data_status/sync_date` |
| `system.audit.view` | `GET /api/admin/audit` |
| `system.user.manage` | `/api/admin/users*` |
| `system.role.manage` | `/api/admin/roles*`、`GET /api/admin/permissions` |
| `admin.support.view` | `/api/admin/support/sessions*` |

> **同一路径按 `scope` 分支**：`GET /api/risk/alerts?scope=model` 需要 `model.alerts.view`；不带 `scope` 的个人告警需要 `risk.alerts.view`。`POST /api/risk/alerts/ack?scope=model` 需要 `model.alerts.ack`；个人 ack 需要 `risk.alerts.ack`。路由层不能只挂一个权限码，handler 必须按 `scope` 做二次校验。

### 10.3 路由覆盖自检

```python
# 伪代码：启动时执行
PUBLIC_PATHS = {"/api/login", "/health", "/api/version", "/webhook/feishu"}

for route in app.routes:
    # WebSocket 单独处理：连接用 ws.connect，消息类型再按权限过滤
    if isinstance(route, APIWebSocketRoute):
        if getattr(route.endpoint, "__permission__", None) != "ws.connect":
            raise RuntimeError(f"WS 未声明 ws.connect: {route.path}")
        continue

    if route.path not in PUBLIC_PATHS and not route.path.startswith("/api"):
        continue

    meta = getattr(route.endpoint, "__permission__", None)
    if route.path in PUBLIC_PATHS:
        if meta is not None:
            raise RuntimeError(f"public 路由不应声明权限: {route.path}")
        continue
    if meta is None:
        raise RuntimeError(f"路由未声明权限: {route.methods} {route.path}")
    if meta not in permission_codes:
        raise RuntimeError(f"未知权限码: {meta}")
```

**覆盖口径**：

- 以运行时 `app.routes` 为准。当前实测约 **232 条 HTTP/WS 路由**；`design/03-API接口文档.md` 的“123 个端点”是 2026-09-01 的旧基准，已过期，不能作为开发清单。
- Agent 文件下载 `/api/agent/sessions/{sid}/files/{rel:path}` 声明 `agent.use`，但必须额外校验签名/owner；签名不是免鉴权。
- `/api/version` 必须 public，因为前端 `version.js` 无 token 轮询；只返回构建哈希。
- WS 路由声明 `ws.connect`；消息级权限在广播时判断，不能只靠连接权限。

**实现建议**：

- 用自定义 `APIRouter` 子类或装饰器参数记录权限；不要在每个函数里手写字符串。
- 权限码常量由 `app/auth/permissions.py` 单一导出；种子 SQL 与常量做一致性测试。
- CI 增加 `tests/unit/test_route_permissions.py`，不依赖数据库即可跑。

### 10.4 请求上下文

```python
@dataclass
class Principal:
    user_id: int
    username: str
    role_codes: set[str]
    permissions: dict[str, Grant]   # code -> grant
    support_session_id: int | None
    support_target_id: int | None
    perm_version: int

    @property
    def support_mode(self) -> bool:
        return self.support_target_id is not None

    @property
    def data_user_id(self) -> int:
        return self.support_target_id or self.user_id
```

- `data_user_id` 是个人查询唯一允许使用的 owner 参数。
- 业务代码禁止接收客户端传入的 `user_id` 作为筛选条件（管理端列表除外）。
- 支持模式下 `is_write` 一律拒绝。

### 10.5 兼容策略

- 保留现有个人 API 路径（`/api/portfolio`、`/api/watch` 等），前端改动最小；后端按 token 自动隔离。
- 新增 `/api/me`、`/api/admin/*`、支持会话 API。
- 旧的 `GET /api/settings`、`GET /api/settings/preference` 保留为**系统设置**（`system.settings.view`，仅 admin）；个人设置走 `/api/me/settings`，不按角色混返同一路径。旧路径不得再返回个人 `user_config`。
- 旧的 `GET /api/risk/alerts?ack=true` 标记为废弃；保留兼容时只允许确认自己的告警，且支持模式 403；新前端用 `POST /api/risk/alerts/ack`。
- 旧的 `POST /api/dag_trigger` 保留，权限 `dag.execute`；新前端用 `POST /api/dag/flows/{id}/execute`。

---

## 11. 前端设计

### 11.1 权限来源

`GET /api/me` 返回：

```json
{
  "user": {"id": 2, "username": "alice", "display_name": "Alice", "status": "active"},
  "roles": ["trader"],
  "permissions": [
    "portfolio.position.view",
    "portfolio.position.edit",
    "watch.group.view",
    "research.treemap.view",
    "research.stock.view",
    "agent.use"
  ],
  "scopes": {"portfolio.position.view": "self"},
  "perm_version": 3,
  "must_change_password": false
}
```

前端 `auth` store 增加 `permissions: Set<string>`、`roles`、`permVersion`、`mustChangePassword`。

### 11.2 导航与路由

| 分组 | tab | 页面 | 权限码 | 默认角色 | 备注 |
|---|---|---|---|---|---|
| 核心 | `p` | 持仓 | `portfolio.position.view` | A/T | 一级 tab；`t` 交易复盘为二级页 |
| 核心 | `rt` | 实时（盘中作战台） | `research.realtime.view` | A/O/T/N/V | 一级 tab（design/14）；旧 `#/kr`、`#/stocks?tab=radar`、树图 `mode=intraday` 三种链接重定向到此 |
| 核心 | `k` | 大盘 | `research.market.view` | A/O/T/N/V | 一级 tab（当前前端已有） |
| 核心 | `m` | 选股/树图 | `research.treemap.view` | A/O/T/N/V | 一级 tab |
| 核心 | `s` | 信号 | `research.signal.view` | A/O/T/N/V | 一级 tab |
| 核心 | `l` | 个股 | `research.stock.view` | A/O/T/N/V | 一级 tab |
| 核心 | `b` | 可转债 | `research.cb.view` | A/O/T/N/V | 一级 tab |
| 核心 | `n` | 资讯 | `research.news.view` | A/O/T/N/V | 一级 tab |
| 核心 | `i` | 问答 | `agent.use` | A/O/T/N | 一级 tab |
| 核心 | `x` | 状态 | `system.status.view` | A/O | 一级 tab |
| 核心 | `u` | 证券主档 | `research.stock.view` | A/O/T/N/V | 一级 tab（当前前端已有） |
| 管理 | `a` | 模型 | `model.view` | A/O/T/N/V | 管理组 |
| 管理 | `f` | 函数 | `function.view` | A/O/N | 管理组 |
| 管理 | `e` | 特征 | `feature.view` | A/O/N | 管理组 |
| 管理 | `g` | DAG | `dag.view` | A/O | 管理组 |
| 管理 | `w` | 数据 | `system.dbexplorer.view` | A | 管理组；后台组紧接其后 |
| 后台 | `y` | 用户 | `system.user.manage` | A | 纯后台：账号/角色分配/支持会话入口 |
| 后台 | `z` | 角色权限 | `system.role.manage` | A | 纯后台：角色/权限树/条件 |
| 后台 | `h` | 审计 | `system.audit.view` | A | 纯后台：审计查询/详情 |
| 后台 | `o` | 系统设置 | `system.settings.view` | A | 纯后台：全局策略/LLM/配额；个人设置走用户菜单 |

**桌面导航（admin）**：

```text
[持仓][实时][大盘][选股][信号][个股][可转债][资讯][问答][状态][证券主档]
[模型][函数][特征][DAG][数据] | [用户][角色权限][审计][系统设置]
```

- **`|` 是分组分隔符，不是 tab**：位于【数据 w】之后；只有当前用户至少有 1 个后台 tab 时才渲染。
- 管理组和后台组都只对有权用户显示：`operator` 可看管理组（无 `w` 数据时也不显示数据）；`trader/analyst/viewer` 默认看不到两组；`admin` 看全部。
- 移动端“管理”抽屉用分组标题“管理 / 后台管理”代替字面 `|`，不要在窄屏硬塞竖线。
- `o` 系统设置从状态页移出，作为独立后台页；状态页只保留数据/系统状态，避免后台能力藏在状态页里。
- `nav.js` 增加 `y/z/h/o` 的 hash 解析与 `titleMap/pageMeta`；`isAdminTab` 同时覆盖管理组与后台组，`showAdmin` 判断按可见 tab 集合而不是单一布尔。
- 导航渲染：`can('...')` 为 false 的 tab 不渲染；不要只置灰（避免暴露管理面）。
- hash 子路由：`d`（个股详情）需 `research.stock.view`；`v`（特征详情）需 `feature.view`；`c`（转债详情）需 `research.cb.view`；`q`/`r`（流程日志/查看）需 `dag.view`；`t`（交易复盘）需 `portfolio.trades.view`。直接改 hash 也要重定向。

**后台页面路由/标题**：

| tab | hash | 页面标题 | 权限码 | 备注 |
|---|---|---|---|---|
| `y` | `#/admin/users` | 用户管理 - K道 | `system.user.manage` | 支持会话入口 |
| `z` | `#/admin/roles` | 角色权限 - K道 | `system.role.manage` | 角色 + 权限树 |
| `h` | `#/admin/audit` | 审计 - K道 | `system.audit.view` | 只读 |
| `o` | `#/admin/settings` | 系统设置 - K道 | `system.settings.view` | 全局设置；个人设置走用户菜单 |
- `nav.js` 解析到无权限 tab 时重定向到第一个有权限的 tab，不闪烁。
- **容器页的二次控制**（`rt` 实时页，design/14）：一级入口只看 `research.realtime.view`；页内分区各自再看
  `research.market.view` / `research.treemap.view` / `research.radar.view`，缺哪个分区就不渲染哪个，
  导航与分区都遵循「不渲染而不是置灰」。
- `/api/version` 是 public；版本自检不依赖登录权限，避免普通用户被 401 刷屏。
- 管理端路由额外检查角色/权限，避免直接改 hash 进入。

### 11.3 按钮级权限

```vue
<n-button v-perm="'portfolio.position.edit'">新增持仓</n-button>
<n-button v-perm="'model.train'">训练</n-button>
```

- `v-perm` 只做隐藏/禁用；后端仍校验。
- 敏感按钮（删除、审批、发布、补数、终止）建议二次确认。
- 无权限时不要显示灰色按钮堆叠；直接不渲染或放入“更多”菜单并标注无权限。

### 11.4 管理端原型（简易线框）

#### 全局导航（桌面 admin）

```
┌──────────────────────────────────────────────────────────────────────────────┐
│ 持仓  大盘  选股  信号  个股  可转债  资讯  问答  状态  证券主档                 │
│                                                                              │
│ 模型  函数  特征  DAG  数据  │  用户  角色权限  审计  系统设置                  │
└──────────────────────────────────────────────────────────────────────────────┘
       └──────── 管理组 ────────┘  │  └──────── 后台管理组 ────────┘
                                   └─ 仅 admin 可见；无后台 tab 时不渲染
```

- `|` 是普通分隔符，不可点击、不参与路由；桌面端用一条 1px 竖线或字面 `|`。
- `用户/角色权限/审计/系统设置` 只对 admin 渲染；`operator` 最多看到管理组，trader/analyst/viewer 不显示这两组。
- 移动端管理抽屉用“管理”“后台管理”两个分组标题，不用 `|`。

#### 用户管理

```
┌──────────────────────────────────────────────────────────────┐
│ 系统管理 / 用户                              [+ 新建用户]      │
├──────────────────────────────────────────────────────────────┤
│ 用户名   显示名   角色        状态     最后登录      操作       │
│ alice    Alice    trader      正常     11-21 09:30  改角色/禁用  │
│ bob      Bob      analyst     正常     11-20 18:12  改角色/禁用  │
│ ops      Ops      operator    正常     11-21 08:00  改角色/禁用  │
├──────────────────────────────────────────────────────────────┤
│ 分页 / 搜索 / 状态筛选                                         │
│ 行操作：改角色 / 重置密码 / 禁用 / 支持会话（只读）             │
└──────────────────────────────────────────────────────────────┘
```

#### 角色编辑器

```
┌──────────────────────────────────────────────────────────────┐
│ 角色：trader（交易用户）        [保存] [取消]                   │
├──────────────────────────────────────────────────────────────┤
│ 基本信息：名称 / 描述 / 数据范围(self)                          │
├──────────────────────────────────────────────────────────────┤
│ 功能节点树（勾选）                                             │
│ ▼ 个人交易域                                                   │
│   ☑ 持仓查看          scope: self                            │
│   ☑ 持仓编辑          scope: self                            │
│   ☑ 交割单查看        scope: self                            │
│   ☑ 交割单导入        conditions: {"allowed_hours":...}       │
│   ☑ 风控规则          scope: self                            │
│ ▼ 共享研究域                                                   │
│   ☑ 树图/市场         scope: global                          │
│   ☑ 个股研究          scope: global                          │
│   ☑ 信号              scope: global                          │
│ ▼ 管理域                                                       │
│   ☐ 模型训练 …                                                 │
├──────────────────────────────────────────────────────────────┤
│ 条件（仅对有 ABAC 钩子的节点显示）                              │
│ 交割单导入：允许时段 [08:00] - [23:00]                          │
│ Agent：每日消息 [200] 条 / token [200000]                      │
└──────────────────────────────────────────────────────────────┘
```

#### 支持会话

```
┌──────────────────────────────────────────────────────────────┐
│ 支持模式                                                     │
│ 目标用户：[alice        ▼]                                   │
│ 理由：[用户反馈持仓页数据异常，协助排查            ]           │
│ 时长：[30 分钟 ▼]                           [创建只读会话]    │
├──────────────────────────────────────────────────────────────┤
│ 当前会话：alice / 剩余 27 分钟 / 只读 / [撤销]                 │
│ 所有访问将记录审计日志。                                       │
└──────────────────────────────────────────────────────────────┘
```

#### 审计

```
┌──────────────────────────────────────────────────────────────┐
│ 后台管理 / 审计                                              │
│ 筛选：操作者 [      ]  动作 [      ]  资源 [      ]           │
│       时间 [开始] - [结束]  目标用户 [      ]   [查询]         │
├──────────────────────────────────────────────────────────────┤
│ 时间       操作者  动作              资源        目标   结果   │
│ 11-21 10:12 admin  model.approve     model:v18   —      成功   │
│ 11-21 10:20 admin  admin.support.access portfolio alice 成功   │
│ 11-21 10:21 alice  portfolio.update  portfolio —      成功   │
├──────────────────────────────────────────────────────────────┤
│ 详情抽屉：before/after（脱敏）/ request_id / IP / user_agent   │
└──────────────────────────────────────────────────────────────┘
```

#### 系统设置（后台）

```
┌──────────────────────────────────────────────────────────────┐
│ 后台管理 / 系统设置                                           │
│ [全局策略] [LLM 配置] [配额/补数] [通知]                       │
├──────────────────────────────────────────────────────────────┤
│ LLM Key：****abcd        [更新 Key]                           │
│ Base URL：https://api.deepseek.com  [测试连通性]              │
│ 模型：deepseek-chat                                           │
│ 注意：仅 admin 可见；Key 永不回显明文；修改写审计              │
└──────────────────────────────────────────────────────────────┘
```

> 个人偏好（主题外的交易偏好/通知开关）走用户菜单的“个人设置”，不放在系统设置页。

#### 用户菜单

```
┌──────────────┐
│ alice        │
│ trader       │
├──────────────┤
│ 个人设置      │
│ 修改密码      │
│ 退出登录      │
└──────────────┘
```

#### 持仓页“我的”语义

```
持仓（我的）   自选（我的）   [新增分组]
────────────────────────────────────────
代码   名称   数量   成本   现价   盈亏
...

图例：◆ 我的持仓    ◇ 模型纸面（全局）    ★ 买入信号（全局）
```

### 11.5 本地存储按用户隔离

| 键 | 现状 | 改为 |
|---|---|---|
| `detail_chart_order:<username>` | 已按用户名 | `detail_chart_order:<user_id>` |
| `price_alarms_v1` | 全局 | `price_alarms_v1:<user_id>` |
| `notif_enabled` | 全局 | `notif_enabled:<user_id>` |
| `floating_watch_positions_v1` | 全局 | `floating_watch_positions_v1:<user_id>` |
| `theme-mode` | 全局 | 保留全局（设备偏好） |
| WPF `%APPDATA%\KdaoWatch\token.txt` | 单文件，后写覆盖 | 文件名带 `user_id`/username，或启动时按当前用户重新 `prepare`；避免浮窗显示上一个用户的持仓 |

**登录后强制改密**：`/api/login` 返回 `must_change_password=true` 时，前端只渲染改密页，改密成功后才加载主应用；不能只弹一个可关闭的提示。

### 11.6 WS 生命周期

```
登录成功
  ├─ 存储 token/user/permissions
  ├─ disconnectWebSocket()（防止旧用户连接）
  └─ connectWebSocket()

权限变更 / 切换用户
  ├─ 重新 GET /api/me
  ├─ 更新菜单/按钮
  └─ 重连 WS

退出登录
  ├─ auth.logout()
  ├─ disconnectWebSocket()
  └─ 清理当前用户本地缓存（可选）
```

### 11.7 403/404 处理

- 403：提示“无权限”，不跳登录页；记录一次前端日志。
- 404：按“资源不存在”处理；不要提示“这是别人的数据”。
- 401：清 token，回登录页。
- 权限变更导致 403：提示“权限已变更，请刷新”，并重新拉 `/api/me`。

---

## 12. 原型图索引与关键流程

### 12.1 图索引

| 图 | 位置 | 用途 |
|---|---|---|
| 系统分区图 | §4.1 | 共享域/个人域/系统派生 |
| 请求鉴权链路 | §4.2 | 认证→权限→数据范围→审计 |
| 权限评估流程 | §4.3 | allow/deny/ABAC/scope |
| 支持模式流程 | §6.6 | 管理员跨用户只读 |
| 盘中监测时序 | §9.1 | 单行情循环 + 多用户评估 |
| 日频信号流程 | §9.2 | 全局预测 + 个人卖出 |
| 全局导航/后台分组 | §11.4 | 数据后用 \| 分隔的纯后台组 |
| 用户管理线框 | §11.4 | 管理端 |
| 角色编辑器线框 | §11.4 | 功能节点/条件 |
| 支持会话线框 | §11.4 | 理由/时长/撤销 |
| 审计页线框 | §11.4 | 审计查询/详情 |
| 系统设置页线框 | §11.4 | 全局策略/LLM/配额 |
| 持仓页语义 | §11.4 | “我的” vs 模型纸面 |

### 12.2 数据隔离示意

```
用户 A 请求 GET /api/portfolio
  │
  ├─ Principal: user_id=1, support_target_id=NULL
  ├─ permission: portfolio.position.view ✓
  ├─ scope: self
  └─ SQL: SELECT ... FROM portfolio WHERE user_id=1 AND is_active

管理员请求 GET /api/portfolio（无支持会话）
  │
  ├─ Principal: user_id=99, support_target_id=NULL
  ├─ permission: portfolio.position.view ✓（admin 有 self）
  └─ SQL: SELECT ... FROM portfolio WHERE user_id=99 AND is_active
       → 只看到管理员自己的持仓，不是所有人的

支持人员请求 GET /api/portfolio（带支持会话）
  │
  ├─ Principal: user_id=100, support_target_id=1, support_mode=true
  ├─ permission: portfolio.position.view ✓
  ├─ support_mode: true（主 JWT + 支持令牌）
  └─ SQL: SELECT ... FROM portfolio WHERE user_id=1 AND is_active
       + audit(admin.support.access, target=1)
       + 任何写操作 403
```

---

## 13. 实施计划

### 13.1 阶段划分

| 阶段 | 内容 | 交付物 | 验收 | 预估 |
|---|---|---|---|---|
| P0 | 设计冻结 | 本文档评审通过；开放问题关闭 | 评审签字/记录 | 1–2 天 |
| P1 | 认证与用户 | `users` 表、登录、JWT、`/api/me`、用户管理 API、全局鉴权 | 登录/禁用/改密/旧 token 失效测试 | 3–4 天 |
| P2 | 权限引擎 | roles/permissions/grants/overrides、`require_perm`、`personal_scope`、缓存、审计 | 权限矩阵/deny/过期/缓存失效测试 | 4–6 天 |
| P3 | 个人数据 schema | 新表、个人表加 owner、唯一约束/主键切换、视图、回填 | 迁移验证 SQL 全绿 | 3–5 天 |
| P4 | 个人 API 隔离 | portfolio/watch/trades/risk/stock/news/agent 按用户 | 跨用户隔离测试全绿 | 6–10 天 |
| P5 | 背景链路 | 盘中多用户、日频用户风控、个人信号、新闻并集、WS 路由 | 双用户并行测试 | 5–8 天 |
| P6 | 前端 | auth store、导航/路由守卫、`v-perm`、管理端、后台管理组（用户/角色/审计/设置）、本地存储、WS 生命周期 | 角色切换/权限变更/403/后台分组测试 | 4–6 天 |
| P7 | 飞书/Agent | open_id 绑定、会话 owner、沙箱、技能命名空间、原始 SQL 限制 | 双用户飞书/Agent 隔离测试 | 4–6 天 |
| P8 | 测试与上线 | 全量回归、迁移演练、性能、上线检查 | 验收清单全绿 | 4–6 天 |

**总工期**：

- MVP（P0–P6，Web + 个人数据 + 监测 + 权限）：**4–6 周**（单人，含测试）。
- 完整（含 P7 飞书/Agent）：**6–9 周**。
- 不含交割单/交易复盘的最小数据隔离：约 3 周，但不满足“持仓监测隔离”目标，不建议。

### 13.2 开发顺序原则

1. **先认证/权限，再数据隔离**：没有 Principal 和 scope helper，后面每个 API 都要返工。
2. **先个人表 schema，再改 API**：先让数据库能表达 owner，再改查询。
3. **先读接口，后写接口**：读隔离验证成本低；写接口需要审计/事务。
4. **先 Web，后飞书/Agent**：Agent 的原始 SQL/沙箱是最高风险，放最后并单独评审。
5. **每阶段可回滚**：数据库迁移前备份；代码按阶段合并；不要一次性大爆炸。

### 13.3 代码改造文件清单

| 模块 | 文件 | 改动 |
|---|---|---|
| 认证 | `app/auth/auth.py` | `CurrentUser`/`Principal`、users 表校验、`require_perm`、`require_admin`、支持会话解析 |
| 认证 | `app/auth/permissions.py`（新） | 权限码常量、个人/共享节点分类、ABAC 钩子注册 |
| 入口 | `app/main.py` | 全局鉴权依赖、public 白名单、路由权限元数据、`/api/me`、登录返回权限 |
| 配置 | `app/config.py` | 移除单账号密码依赖；生产密钥校验；支持会话 TTL 等配置 |
| Schema | `app/db/schema.py` | 新表 DDL、个人表迁移、视图、种子权限/角色 |
| 持仓 | `app/api/portfolio.py` | 所有 SQL 加 `data_user_id`；缓存按 user；写操作审计 |
| 自选 | `app/api/watch.py` | 分组按 user；默认组创建；成员通过 group owner 校验 |
| 交易 | `app/api/trades.py` | 所有端点传 user_id |
| 交割单 | `app/trade_slip.py` | `build_positions/apply_positions` 加 user_id；去重键按 user |
| 实时持仓 | `app/trade_intraday.py` | `live_snapshot/live_positions/_snapshot` 加 user_id；现金按用户 |
| 复盘 | `app/trade_review.py` | `get_discipline/save_discipline` 读 `user_config` |
| 盘中 | `app/intraday_watch.py` | 单行情循环 + 每用户 `_ctx/_state`；告警写 user_id；冷却按 user |
| 风控 | `app/risk.py` | 拆 `evaluate_model_risk` 与 `evaluate_user_risk` |
| 风控 API | `app/api/risk.py` | 规则读 `user_config`；告警按 user；ack 按 user |
| 个股 | `app/api/stock.py` | quote 成本线、radar 按 user |
| 资讯 | `app/api/news.py` | `only_focus/focus_linked` 按 user |
| 买点 | `app/buy_check.py` | 关注池/实时持仓/纪律按 user |
| 信号 | `app/api/signals.py` | 全局买入；个人信号新接口（**必需**）：`GET /api/signals/personal` |
| 持仓信号 | `app/api/portfolio.py:get_portfolio` | 今日信号合并 `user_signal_history`；买入读全局 `signal_history` |
| 模型 | `app/api/models.py` | 纸面组合标注全局；管理操作权限 |
| 设置 | `app/api/settings.py` | 个人/系统设置拆分；LLM Key admin-only |
| 状态 | `app/api/status.py` | WS 连接绑定 user；告警/任务按 owner；补数 admin-only；路由权限 |
| Agent API | `app/api/agent.py` | 会话 owner 校验 |
| Agent 运行时 | `app/agent/runtime.py` | 所有 session 函数加 user_id；沙箱路径按用户 |
| Agent 工具 | `app/agent/tools.py` | `tool_query_sql` 仅 admin；`portfolio_summary` 按 user；股票分组按 user |
| Agent 沙箱 | `app/agent/sandbox.py` | 工作区 `data/agent_ws/<user_id>/<session_id>/`；禁网、禁 DB socket、清空敏感环境；bwrap 不可用 fail-closed |
| Agent 技能 | `app/agent/skills.py` | 个人技能目录 `data/skills/<user_id>/`；全局技能只读 |
| 飞书 | `app/feishu/webhook.py` | 解析 `open_id/chat_id`；绑定/未绑定分支；卡片回调带 user |
| 飞书 | `app/feishu/handler.py` | `handle_message(user_id, ...)`；历史按 user |
| 飞书 | `app/feishu/context.py` | 持仓/偏好/规则按 user |
| 飞书 | `app/feishu/history.py` | key 改为 `feishu:<open_id>`；修复位置参数 bug |
| AI 工具 | `app/ai/tools.py` | 持仓查询/确认写入按 user |
| 新闻采集 | `crawler/news/collect.py` | 采集用 `v_focus_pool_all` |
| 新闻总结 | `scripts/news_summary.py` | 关注池优先级全局/按用户展示分离 |
| 模型信号 | `scripts/pipeline.py` | 全局预测一次；个人卖出写 `user_signal_history`；模型健康只统计买入 |
| 调度 | `scripts/cron_scheduler.py` | 全局任务保持；个人任务按用户遍历 |
| 前端 auth | `web-v2/src/stores/auth.js` | user/roles/permissions/permVersion/mustChangePassword |
| 前端请求 | `web-v2/src/main.js` | 401/403 处理；`/api/me` 刷新 |
| 前端导航 | `web-v2/src/App.vue` | 按权限渲染核心/管理/后台三组；后台组在 `w` 后用 \| 分隔；无后台 tab 不渲染分隔符；用户菜单 |
| 前端路由 | `web-v2/src/stores/nav.js` | 新增 `y/z/h/o`；无权限 tab 重定向；`d/v/c/q/r/t` 子路由守卫 |
| 前端 WS | `web-v2/src/utils/ws.js` | 登录后连接、退出断开；支持 ticket |
| 前端版本 | `web-v2/src/utils/version.js` | `/api/version` 改 public；无 token 轮询 |
| 前端页面 | `PortfolioView/WatchTable/DetailView/TreemapView/SignalsView/StatusView/ModelView/FunctionView/FeatureView/DagFlowEdit/DbExplorerView/AgentView/LoginView` | 权限按钮、个人/全局文案、403 处理、本地存储按 user |
| 后台页面 | `AdminUsersView/AdminRolesView/AdminAuditView/AdminSettingsView`（新） | 用户/角色权限/审计/系统设置；仅 admin；支持会话入口在用户页 |
| 交易复盘 | `app/trade_review.py` | `build_review/today_gate` 全链路 user_id |
| 执行质量 | `app/execution_quality.py` | `report/sell_quality/alert_execution/emotion_summary` 全链路 user_id |
| 盘中 AI | `app/intraday_ai.py` | 按 owner 读规则/冷却/日限；队列与 WS 按 user |
| 盘中结果 | `app/intraday_outcome.py` | `evaluate_pending/_upsert` 写 `user_id`；全局统计按 `(stock_code,kind,alert_time)` 去重 |
| Agent 文件 | `app/agent/media.py` | 文件签名绑定 user_id |
| 权限 API | `app/api/features.py`、`functions.py`、`dag_flows.py`、`dag_types.py`、`kepl.py`、`lineage.py` | 只加权限码；共享数据不改 owner |
| 权限 API | `app/api/treemap.py`、`market.py`、`cb.py`、`stocks.py`、`buy.py` | 只加权限码；共享数据/个人雷达分别处理 |
| 权限 API | `app/api/dbexplorer.py` | 表/列白名单；个人表禁止 |
| 文档 | `design/03-API接口文档.md` | 以运行时路由重新生成端点清单（当前 232 条，不是 123） |

### 13.4 开发前必须修复的现状代码（P0/P1）

> 这些不是“设计选择”，而是现有代码缺陷；不修就做多用户会直接串户/泄露/迁移失败。逐项关闭后才能进入 P1。

| # | 现状缺陷 | 证据 | 修复 | 关联 |
|---|---|---|---|---|
| C-01 | 无全局默认拒绝/权限层 | `app/main.py:181-206`；`app/auth/auth.py:36-43` | 全局鉴权 + `require_perm` + 路由自检 | PERM-01/ROUTE-02 |
| C-02 | JWT 默认密钥/空密码/明文比较 | `app/config.py:9,50`；`app/auth/auth.py:31-33` | bcrypt/argon2；prod 拒绝默认密钥；token_version | AUTH-02/04/06 |
| C-03 | LLM Key 明文泄露 | `app/api/settings.py:31-66,197-204`；`app/ai/tools.py:200-225` | 字段级脱敏；admin-only；`/api/settings` 拆分 | CFG-03 |
| C-04 | 匿名可补数/调 LLM | `app/api/status.py:1000,1033`；`app/api/functions.py:318-331` | 权限 + 配额 + 审计 | AUTH-01/PERM-01 |
| C-05 | 任意登录用户可改全局配置 | `app/api/settings.py:137-169,227-280` | `system.settings.edit` admin-only；base_url 白名单 | CFG-03 |
| C-06 | dbexplorer 可读全库 | `app/api/dbexplorer.py:29-130` | 表/列白名单；个人表禁止 | WB-13 |
| C-07 | Agent 会话无 owner | `app/api/agent.py:52-139`；`app/agent/runtime.py:338-411` | `agent_sessions.user_id`；runtime 校验 | DATA-06 |
| C-08 | Agent 代码执行/环境变量泄露 | `app/agent/sandbox.py:71-108,196-216`；`app/agent/tools.py:276-295` | `agent.exec` admin-only；清空 env；禁网/禁 DB socket；fail-closed | WB-15 |
| C-09 | Agent 工具绕过 RBAC | `app/agent/tools.py:321-379,490-537,666-714` | 工具级权限映射 + Principal | WB-14 |
| C-10 | 告警 ack 全局 | `app/api/risk.py:183-185` | `POST /risk/alerts/ack` + user 过滤 | WB-08 |
| C-11 | WS 全局广播/token 进 URL | `app/api/status.py:413-441,527-569` | WS ticket + 消息过滤 | WB-25 |
| C-12 | 个人 GET 匿名可读 | `app/api/portfolio.py:287-330,464`；`app/api/watch.py:16-32,78,193` | 认证 + owner 过滤 | DATA-01 |
| C-13 | 全局写操作无角色校验 | `app/api/models.py`、`features.py`、`functions.py`、`dag_flows.py` | 按矩阵加 `require_perm` | PERM-01 |
| C-14 | 飞书未强制签名/无 open_id | `app/feishu/webhook.py:17-47,97-167`；`handler.py:11`；`history.py:27-62` | 生产强制签名；绑定 open_id；按 user | WB-26 |
| C-15 | `init_db` 重建旧全局唯一索引 | `app/db/schema.py:97-100,170-175,1685-1697` | 改写 DDL；启动断言 | WB-02 |
| C-16 | `v_focus_pool` 不能用 OR REPLACE 改列 | `app/db/schema.py:2030-2033` | DROP + CREATE | WB-03 |
| C-17 | `set_stock_groups` 跨用户删除/插入 | `app/api/watch.py:168-186` | gid owner 校验；DELETE/INSERT 带 user | WB-05 |
| C-18 | quote/levels 缓存泄露成本 | `app/api/stock.py:822-826,931,963-978` | 只缓存共享行情；个人价位按 user | WB-07 |
| C-19 | `intraday_reconcile` 全局改写 | `app/api/trades.py:95-108`；`app/trade_intraday.py:1259-1274` | 按 user 对账 | WB-06 |
| C-20 | `trade_cash_snapshot/trade_plan/trade_journal` 的 `ON CONFLICT (trade_date)` | `app/trade_intraday.py:76-81,493-496,918-924` | 改 `(user_id, trade_date)` | WB-04 |
| C-21 | `risk_rules` 模型/个人混用 | `app/risk.py:31-42,69-78`；`app/api/portfolio.py:209`；`app/api/stock.py:914-917` | 拆 `model_risk_rules` 与 `user_config.risk_rules` | WB-09 |
| C-22 | 卖出信号写全局 `signal_history` 且读 `portfolio` | `scripts/pipeline.py:2017-2019` | 改 `live_positions`；写 `user_signal_history` | D7/WB-10 |
| C-23 | 模型删除不清理个人信号 | `app/api/models.py:906` | 清理/归档 `user_signal_history` | WB-21 |
| C-24 | `intraday_ai_event` 无 owner/全局限额 | `app/intraday_ai.py:85-109`；`app/api/status.py:548-569` | 加 owner；按 user 冷却/限额/推送 | WB-18 |
| C-25 | 版本自检无 token | `web-v2/src/App.vue:347`；`web-v2/src/utils/version.js:17` | `/api/version` public | WB-24 |

### 13.5 上线策略

| 项 | 方案 |
|---|---|
| 维护窗口 | 停机迁移 3–5 分钟；提前通知 |
| 备份 | `pg_dump -Fc` + 校验和；保留至少 7 天 |
| 灰度 | 先只启用 admin；验证后再创建 trader/analyst 测试账号 |
| 回滚 | 恢复备份 + 回退代码；不手工反向 DDL |
| 观察 | 登录失败率、403 率、WS 连接数、告警数、慢查询、审计量 |
| 应急开关 | `MULTI_USER_ENABLED=false` 时只允许 admin 登录（仍需 schema）；或回滚 |

---

## 14. 测试与验证

### 14.1 认证测试

| # | 用例 | 期望 |
|---|---|---|
| A1 | 无 token 访问 `/api/portfolio` | 401 |
| A2 | 过期 token | 401 |
| A3 | 禁用用户旧 token | 401 |
| A4 | 改密后旧 token | 401 |
| A5 | 错误密码 5 次 | 第 6 次 429/锁定 |
| A6 | 登录返回权限列表 | 与角色矩阵一致 |
| A7 | 生产默认密钥启动 | 拒绝启动 |
| A8 | 飞书无签名 | 403 |
| A9 | WS 无 token（prod） | 拒绝连接 |
| A10 | `/api/me` 不返回密码哈希 | 字段不存在 |

### 14.2 跨用户隔离测试（核心）

| # | 用户 A 操作 | 期望 |
|---|---|---|
| I1 | `GET /api/portfolio` | 只返回 A 的持仓 |
| I2 | `POST /api/portfolio` B 已持有的股票 | 创建 A 的持仓，不影响 B |
| I3 | `PUT /api/portfolio/{code}`（B 的持仓） | 404，B 数据不变 |
| I4 | `DELETE /api/portfolio/{code}`（B 的持仓） | 404，B 数据不变 |
| I5 | `GET /api/portfolio/{code}/history`（B 的股票） | 只返回 A 的历史；无 A 历史则空 |
| I6 | `GET /api/watch/groups` | 只返回 A 的分组 |
| I7 | `GET /api/watch/groups/{B_group_id}/stocks` | 404 |
| I8 | `POST /api/watch/groups` 与 B 同名 | 成功（按用户唯一） |
| I9 | `PUT /api/watch/stock-groups` 操作 B 的组 | 404/忽略 |
| I10 | `GET /api/portfolio/trades` | 只返回 A 的交割单 |
| I11 | `POST /api/portfolio/trades/import` | 批次归 A；不影响 B |
| I12 | `GET /api/portfolio/trades/intraday/state` | 只返回 A 的实时持仓/现金 |
| I13 | `PUT /api/portfolio/trades/intraday/cash` | 只改 A 的现金 |
| I14 | `GET /api/risk/alerts` | 只返回 A 的告警 |
| I15 | `GET /api/risk/alerts?ack=true` | 只确认 A 的告警 |
| I16 | `POST /api/risk/rules` | 只改 A 的规则 |
| I17 | `GET /api/agent/sessions` | 只返回 A 的会话 |
| I18 | `GET /api/agent/sessions/{B_sid}/messages` | 404 |
| I19 | `DELETE /api/agent/sessions/{B_sid}` | 404 |
| I20 | A 的 WS 连接 | 只收到 A 的告警，不收 B 的 |
| I21 | `GET /api/signals/personal?stock_code={B_持仓}&days=1095` | 只返回 A 在该股的个人信号；无则空（详情页 K 线不出现 B 的卖出标记） |

### 14.3 权限矩阵测试（抽样，完整由脚本覆盖）

| # | 角色 | 操作 | 期望 |
|---|---|---|---|
| P1 | trader | `POST /api/v1/models/{v}/train` | 403 |
| P2 | trader | `POST /api/dag/flows/{id}/execute` | 403 |
| P3 | trader | `POST /api/data_status/backfill` | 403 |
| P4 | trader | `GET /api/dbex/rows` | 403 |
| P5 | analyst | `GET /api/portfolio` | 403 |
| P6 | analyst | `GET /api/watch/groups` | 200 |
| P7 | operator | `GET /api/portfolio` | 403 |
| P8 | operator | `POST /api/refresh_stats` | 200 |
| P9 | viewer | `POST /api/agent/sessions` | 403 |
| P10 | admin | `GET /api/admin/users` | 200 |
| P11 | admin | `GET /api/portfolio`（无支持会话） | 只返回 admin 自己的 |
| P12 | admin | `POST /api/settings/deepseek_key` | 200 |
| P13 | operator | `POST /api/settings/deepseek_key` | 403 |
| P14 | support | 无支持会话 `GET /api/portfolio`（目标用户） | 只返回自己的/403 |
| P15 | support | 有支持会话 `GET /api/portfolio` | 返回目标用户；写 403 |
| P16 | trader / admin | `GET /api/signals/personal?stock_code=…&days=1095` | 200；详情页 K 线叠加该股个人卖出 ▼，策略信号卡显示当日「卖出」 |
| P17 | operator / analyst / viewer | `GET /api/signals/personal` | 403；详情页不请求该端点，K 线只有全局信号 |

### 14.4 支持模式测试

| # | 用例 | 期望 |
|---|---|---|
| S1 | 创建支持会话无理由 | 422/403 |
| S2 | TTL > 60 分钟 | 422 |
| S3 | 对自己创建支持会话 | 409 |
| S4 | 支持会话读目标持仓 | 200 + 审计 |
| S5 | 支持会话写目标持仓 | 403 |
| S6 | 支持会话访问 `user_config` | 403 |
| S7 | 支持会话过期后访问 | 401/403 |
| S8 | 撤销后立即访问 | 401/403 |

### 14.5 ABAC 测试

| # | 用例 | 期望 |
|---|---|---|
| B1 | 非交易时段导入交割单 | 403 |
| B2 | Agent 超过每日消息配额 | 429 |
| B3 | 非管理员 DB 查询 > max_rows | 403/截断 |
| B4 | operator 授予 `model.approve` | 403 |
| B5 | 禁用最后一个 admin | 409 |
| B6 | 补数配额不足 | 403 |
| B7 | 审批未二次认证 | 403 |
| B8 | 钩子异常 | 503/403，不放行 |

### 14.6 迁移测试

| # | 用例 | 期望 |
|---|---|---|
| M1 | 迁移前备份可恢复 | 恢复后行数/校验和一致 |
| M2 | 个人表 NULL owner | 0 行 |
| M3 | 唯一约束 | 同用户重复被拒；跨用户允许 |
| M4 | 主键切换 | 同用户同日唯一；跨用户同日允许 |
| M5 | 视图 | `v_focus_pool` 带 user_id；`v_focus_pool_all` 去重 |
| M6 | 卖出信号迁移 | `signal_history` 无 sell；`user_signal_history` 有 admin 数据 |
| M7 | 个人配置迁移 | `user_config` 有 admin 的 risk/intraday/discipline |
| M8 | 新旧登录 | 种子 admin 可登录，必须改密 |
| M9 | 回滚 | 恢复备份后旧代码可启动 |
| M10 | 幂等 | 迁移脚本重复执行不报错/不重复 |

### 14.7 监测与性能测试

| # | 用例 | 期望 |
|---|---|---|
| O1 | 两个用户持有同一股票，成本不同 | 各自触发各自止损 |
| O2 | A 关闭提醒，B 开启 | 只评估 B |
| O3 | 两个用户关注池不同 | 雷达/买点只含各自池 |
| O4 | 50 用户、并集 1000 只 | 行情请求按分页，不随用户数线性增长 |
| O5 | 个人日频风控 | 只写各自 user_id 告警 |
| O6 | 新闻采集 | 只按并集采集，不按用户重复 |
| O7 | 全局模型预测 | 只跑一次 |
| O8 | 告警 WS | 只推 owner |

### 14.8 回归测试

- 现有单用户功能全量回归：持仓 CRUD、自选、交割单导入、盘中提醒、雷达、信号、模型页、DAG、新闻、Agent、飞书。
- 现有测试套件 `pytest` 全绿。
- 前端构建 `npm --prefix web-v2 run build` 通过。
- 关键页面手工冒烟：登录、持仓、选股、信号、个股、可转债、资讯、问答、状态、模型、函数、特征、DAG、数据。

---

### 14.9 R3/R4 新增回归用例

| # | 用例 | 期望 |
|---|---|---|
| X1 | 迁移后重启服务，两用户加同一股票 | 都成功；旧 `idx_portfolio_active_stock` 不存在 |
| X2 | 两用户各导入同一份交割单 | 都成功；`row_hash` 去重不跨用户；旧 `uq_trade_slip_hash` 不存在 |
| X3 | 两用户各有“自选”组 | 都成功；旧 `watch_groups_group_name_key` 不存在 |
| X4 | A 保存分组时传入 B 的 group_id | 404/忽略；B 的成员不被删除 |
| X5 | A 导入交割单后 B 的现金/手工记录 | 不受影响；`intraday_reconcile` 按 user |
| X6 | 两用户同一天写现金/计划/日志 | 都成功；`ON CONFLICT (user_id,trade_date)` |
| X7 | 两用户同股，1 秒内先后请求 quote | 各自成本/止损/止盈不串 |
| X8 | 两用户同股同因告警 | 都写入；冷却/单日上限不互相抑制 |
| X9 | A 确认告警 | 只确认 A 的；B 的仍在 |
| X10 | 两用户同股触发 AI 事件 | 各自解读；日限按用户；WS 不串 |
| X11 | 普通用户通过 Agent 要求建/跑 DAG | 403；不产生 `dag_run_log` |
| X12 | 普通用户通过 Agent 执行 Python/Node | 403；只有 `agent.exec` 可执行 |
| X13 | 管理员执行 Python 打印环境变量 | 不含 `DATABASE_URL/APP_SECRET_KEY/TUSHARE_TOKEN/LLM Key` |
| X14 | 用他人 `sid+rel` 访问 Agent 文件 | 404；签名绑定 user_id |
| X15 | 普通用户访问 dbexplorer 个人表 | 403；敏感列不返回 |
| X16 | 管理员无支持会话访问他人持仓 | 只返回自己的 |
| X17 | 支持令牌单独使用（无主 JWT） | 401 |
| X18 | 支持会话访问 `user_config`/Agent 会话 | 403 |
| X19 | 支持会话写操作 | 403 |
| X20 | 撤销支持会话后立即请求 | 401/403（不能等缓存 TTL） |
| X21 | 迁移后 `GET /api/version` 无 token | 200 |
| X22 | 普通用户 WS 连接 | 只收到共享/自己的消息；不收 DAG/主机指标/他人告警 |
| X23 | 飞书伪造事件/未绑定 open_id | 403/拒绝；不写持仓 |
| X24 | 新用户未配置 `intraday_rules` | 默认收到盘中提醒 |
| X25 | 删除模型 | `user_signal_history` 对应信号清理/归档 |
| X26 | 多用户共持同一股票，`news/health focus_n` | 按去重股票数，不按用户×股票 |
| X27 | 迁移 `data/agent_ws`/`data/skills` | 旧会话文件可读；技能归 admin/个人 |
| X28 | 迁移脚本重复执行 | 不报错、不重复 |
| X29 | 管理层 operator 改系统设置/LLM Key | 403 |
| X30 | operator 看模型告警 | 200；看个人告警 403/空 |
| X31 | operator A 确认模型告警 | A、B 两个 operator 都不再看到（全局 ack） |
| X32 | 两用户同股同 kind 触发 outcome | 全局统计按 `(stock_code,kind,alert_time)` 去重，不按用户翻倍 |
| X33 | `agent.sql.raw` 用子查询/JOIN 读 `portfolio` | 403；AST 识别并拒绝 |
| X34 | `agent.exec` 内尝试连本地 PostgreSQL/外网 | 连接失败；沙箱无网络/无 socket |
| X35 | admin 登录看导航 | 管理组后有 \|，后台组为用户/角色权限/审计/系统设置 |
| X36 | operator/trader 看导航 | operator 无数据/后台组；trader 无管理/后台组；无后台 tab 时无 \| |
| X37 | 直接改 hash 进后台页 | 无权限重定向到第一个有权限 tab；不闪现后台内容 |

## 15. 风险登记

| # | 风险 | 概率 | 影响 | 等级 | 缓解 | 触发信号 |
|---|---|---|---|---|---|---|
| R1 | 漏改个人链路导致串户 | 高 | 高 | P0 | 以“数据产生方”清单逐项核对；跨用户测试 | 双用户看到同一持仓/告警 |
| R2 | 权限默认放行/路由漏配 | 高 | 高 | P0 | 全局默认拒绝 + 启动自检 + CI | 新路由匿名可访问 |
| R3 | Agent 原始 SQL 泄露 | 中 | 高 | P0 | 非管理员禁用；管理员审计 | 普通用户能查 portfolio |
| R4 | WS 告警泄露 | 高 | 高 | P0 | 连接绑定 user；按 owner 推送 | 双浏览器收到同一告警 |
| R5 | 迁移失败/数据丢失 | 中 | 高 | P0 | 备份、测试库演练、停机迁移 | 迁移 SQL 报错/行数不符 |
| R6 | 权限缓存不失效 | 中 | 中 | P1 | perm_version + 短 TTL | 收回权限后仍可访问 |
| R7 | 盘中循环 N 倍请求 | 中 | 中 | P1 | 并集拉取 + 共享 quotes | 腾讯请求数随用户数增长 |
| R8 | 全局偏好被个人覆盖 | 中 | 中 | P1 | global_preference 保持全局 | 用户改偏好影响别人信号 |
| R9 | 管理员默认看个人数据 | 中 | 中 | P1 | 支持会话 + 审计 | admin 无会话看到他人持仓 |
| R10 | 角色爆炸/难维护 | 中 | 低 | P2 | 模板 + override | 角色数量接近用户数 |
| R11 | 飞书身份映射错误 | 中 | 中 | P1 | open_id 绑定 + 未绑定提示 | 飞书回复错用户数据 |
| R12 | Agent 技能污染全局 | 中 | 中 | P1 | 个人技能目录 | 用户 A 的技能出现在 B |
| R13 | 审计日志膨胀 | 低 | 低 | P2 | 归档/分区 | 表体积快速增长 |
| R14 | 单 worker 假设被打破 | 中 | 中 | P1 | 明确部署约束；扩容前上 Redis | 多 worker 后 WS/任务异常 |
| R15 | 字段级泄露（成本/API Key） | 中 | 中 | P1 | 序列化层脱敏 + 权限 | 支持模式看到不该看的字段 |

---

## 16. 审查记录

### 16.1 R1：现状边界审查（2026-11-21）

| # | 发现 | 处理 |
|---|---|---|
| R1-01 | `app/risk.py` 日频风控评的是 `paper_positions`，不是用户实盘 | 新增 §9.3 用户实盘风控；模型告警 `user_id=NULL` |
| R1-02 | `v_focus_pool` 全局，驱动盘中/雷达/买点/新闻 | 拆 `v_focus_pool(user_id)` + `v_focus_pool_all` |
| R1-03 | 盘中监测实际用 `trade_slip` 实时持仓，不是 `portfolio` | 个人域必须同时覆盖 `trade_*`；§2.2 C3 |
| R1-04 | 卖出信号读全局 `portfolio` | 拆 `user_signal_history`；§9.2 |
| R1-05 | `global_preference` 影响全局信号生成 | 保持全局，用户不可改；§8.5 CFG-02 |
| R1-06 | `agent/tools.py` 可查任意表 | `agent.sql.raw` 仅 admin；§6.2.2 |
| R1-07 | `agent_sessions` 无 owner | 加 `user_id`；§7.3 |
| R1-08 | 飞书忽略 `open_id/chat_id` | 增加绑定流程；§13.3 |
| R1-09 | `settings.py` LLM Key 可被普通用户改 | `system.settings.edit` 仅 admin；§6.4 |
| R1-10 | 补数接口无鉴权 | `system.backfill.execute` 权限 + 默认拒绝 |
| R1-11 | `paper_portfolio` 容易被当成用户模拟盘 | 前端标注“模型纸面”；§9.6 |
| R1-12 | `news_poll_state` 全局 | 采集用并集，读取按用户；§9.4 |
| R1-13 | `watch_group_items` 无 user_id | 通过 group owner 校验；不冗余加列 |
| R1-14 | `trade_cash_snapshot/trade_plan/trade_journal` 主键是 trade_date | 改复合主键；§7.3 |
| R1-15 | Agent 技能写入全局目录 | 个人技能命名空间；§13.3 |

### 16.2 R2：权限模型审查（2026-11-21）

| # | 发现 | 处理 |
|---|---|---|
| R2-01 | admin 静态 `all` 会导致默认全知 | 改为支持会话；§6.6 |
| R2-02 | `admin.support.view` 不应默认授予 admin | 默认 `○`；§6.4 |
| R2-03 | operator 不应有个人数据权限 | 矩阵中 operator 个人域全 `—`；§6.4 |
| R2-04 | viewer 不应使用 Agent（LLM 成本/外泄） | 矩阵中 viewer `agent.use=—` |
| R2-05 | `model.approve` 与训练应可分离 | operator 标 `○`；§6.4 |
| R2-06 | `system.settings.edit` 不应给 operator | 仅 admin；§6.4 |
| R2-07 | `system.dbexplorer.view` 风险高 | 仅 admin；§6.4 |
| R2-08 | 权限变更不能强制所有人重登 | 增加 `perm_version`，与 `token_version` 分离；§6.10 |
| R2-09 | deny/allow 冲突未定义 | deny 优先；§6.7.3 |
| R2-10 | 临时支持角色需要过期 | `user_roles.expires_at`；§7.2.5 |
| R2-11 | 防提权未定义 | `ABAC-ROLE-GRANT-NO-ESCALATION`；§6.7.1 |
| R2-12 | 最后一个 admin 保护未定义 | `ABAC-LAST-ADMIN-PROTECT`；§6.7.1 |

### 16.3 R3：数据/迁移审查（2026-11-21 完成，已回写）

| # | 发现 | 严重度 | 处理 | 状态 |
|---|---|---|---|---|
| R3-01 | `CREATE OR REPLACE VIEW v_focus_pool` 改首列名必失败 | P0 | 改 `DROP VIEW` + `CREATE VIEW`；§7.3.2 | 已回写 |
| R3-02 | `init_db` 每次启动重建旧全局唯一索引 | P0 | 改写 `schema.py` DDL；启动断言；WB-02 | 已回写 |
| R3-03 | 视图未过滤 `quantity>0`；默认组不唯一 | P1 | 视图加 `quantity>0`；`UNIQUE(user_id) WHERE is_default` | 已回写 |
| R3-04 | `set_stock_groups` 跨用户删除/插入 | P0 | gid owner 校验；DELETE/INSERT 带 user；WB-05 | 已回写 |
| R3-05 | quote/levels 缓存泄露个人成本 | P0 | 只缓存共享行情；个人价位按 user；WB-07 | 已回写 |
| R3-06 | Agent 文件签名不绑 user | P0 | 签名加 user_id；fallback 校验 owner；WB-16 | 已回写 |
| R3-07 | `intraday_reconcile` 全局改写 | P0 | `reconcile(db, user_id)`；WB-06 | 已回写 |
| R3-08 | `ON CONFLICT (trade_date)` 迁移后失效 | P0 | 改 `(user_id, trade_date)`；WB-04 | 已回写 |
| R3-09 | 大量 INSERT 未写 owner | P0 | §7.3.3 写入路径清单；WB-01 | 已回写 |
| R3-10 | `risk_rules` 模型/个人混用 | P0 | 拆 `model_risk_rules` / `user_config.risk_rules`；WB-09 | 已回写 |
| R3-11 | `user_signal_history` 迁移 NULL/重复/丢字段 | P0 | COALESCE + DISTINCT ON + 显式列映射；WB-10 | 已回写 |
| R3-12 | 权限种子把权限码写进 `parent_id INT` | P0 | 先 NULL 插入再 UPDATE 回填；§18.2 | 已回写 |
| R3-13 | 迁移 DDL 不可重复执行 | P1 | 索引 `IF NOT EXISTS`/PK `DO` 块；WB-29 | 已回写 |
| R3-14 | 全新库仍按旧单用户 DDL 建库 | P0 | 改写 `schema.py` 建表常量；WB-02 | 已回写 |
| R3-15 | 旧个人配置键仍可被通用设置 API 读写 | P1 | 迁移后过滤/删除旧键；WB-22 | 已回写 |
| R3-16 | `data/agent_ws`/`data/skills` 无迁移方案 | P1 | 文件迁移步骤 + 旧路径只读兼容；WB-12 | 已回写 |
| R3-17 | Agent 工具只拿 session_id，读全局个人表 | P0 | 工具调度传 Principal；WB-14 | 已回写 |
| R3-18 | 飞书忽略 open_id/历史默认 key | P0 | 绑定 open_id；历史 key；WB-26 | 已回写 |
| R3-19 | 删除模型不清理个人卖出信号 | P1 | 清理/归档 `user_signal_history`；WB-21 | 已回写 |
| R3-20 | 告警 ack 全局、去重无 user | P0 | `POST /risk/alerts/ack`；去重/冷却带 user；WB-08 | 已回写 |
| R3-21 | `intraday_ai_event`/outcome 无 owner | P1 | 加 `user_id`；WB-18/19 | 已回写 |
| R3-22 | `xueqiu_status.user_id` 不是应用 owner | P2 | 迁移排除；WB-27 | 已回写 |
| R3-23 | `app/api/market.py` 是共享行情 | P2 | 只加权限码，不做 owner；WB-28 | 已回写 |
| R3-24 | `news/health focus_n` 按用户×股票重复 | P2 | 用 `v_focus_pool_all`；WB-23 | 已回写 |

### 16.4 R4：路由/权限覆盖审查（2026-11-21 完成，待代码验证）

> 运行时实测约 **232 条 HTTP/WS 路由**；`design/03-API接口文档.md` 的 123 条是旧基准。开发前必须用 §10.3 自检脚本跑一遍，逐条绑定权限码或 public 标记。

| # | 发现 | 严重度 | 处理 | 状态 |
|---|---|---|---|---|
| R4-01 | 无全局默认拒绝；大量路由只挂登录或裸奔 | P0 | 全局 Principal 依赖 + 启动自检；C-01 | 设计已回写，代码待修 |
| R4-02 | 73 条无认证、159 条仅登录、65 条匿名 GET | P0 | 按 §6.2/§10.2 绑定权限；P2-01 | 设计已回写，代码待修 |
| R4-03 | 匿名可补数/调 LLM/验证函数 | P0 | `system.backfill.execute`/`function.ai_chat`；C-04 | 设计已回写，代码待修 |
| R4-04 | 全局写操作无角色校验 | P0 | 模型/特征/函数/DAG 管理权限；C-13 | 设计已回写，代码待修 |
| R4-05 | WS 路由不在权限模型内 | P1 | `ws.connect` + 消息级过滤；WB-25 | 设计已回写，代码待修 |
| R4-06 | `/api/version` 被误归 A/O | P1 | 改 `public.version`；WB-24 | 设计已回写，代码待修 |
| R4-07 | `GET /api/settings/preference`、`/llm_config/metrics`、`POST /features/validate` 漏映射 | P1 | 补入 §10.2 | 已回写 |
| R4-08 | `GET /api/dbex/*` 路径写成 `/api/dbx/*` | P2 | 统一 `/api/dbex` | 已回写 |
| R4-09 | `GET /discipline/today` 路径不完整 | P2 | 补 `/api/portfolio/trades` 前缀 | 已回写 |
| R4-10 | 个人卖出信号只有表没有读 API | P1 | 新增 `signal.personal.view` + `GET /api/signals/personal` | 已回写 |
| R4-11 | 支持模式 scope 与 DDL/矩阵矛盾 | P0 | 删除持久化 `support` scope；运行时 support_mode | 已回写 |
| R4-12 | dbexplorer/raw SQL 与“admin 默认不看个人数据”矛盾 | P0 | 表/列白名单；个人表禁止；支持会话专用 API | 已回写 |
| R4-13 | Agent `agent.use` 隐含代码执行/DAG/持仓 | P0 | 拆 `agent.exec`；工具级权限映射 §6.2.5 | 已回写 |
| R4-14 | Agent 每日配额无计量表 | P1 | `agent_usage_daily`；WB-17 | 已回写 |
| R4-15 | 审批二次认证无端点 | P1 | `POST /api/me/reauth` + step-up 票据 | 已回写 |
| R4-16 | override/临时角色无 API | P1 | `/api/admin/users/{id}/permissions`、`roles expires_at` | 已回写 |
| R4-17 | `dag.terminate` 无任务 owner | P1 | `dag_run_log.created_by`；WB-20 | 已回写 |
| R4-18 | operator 权限与 CFG-05 冲突 | P1 | `dag.publish` operator 默认 `○`；settings/dbexplorer 去掉 | 已回写 |
| R4-19 | 前端导航表缺 k/u，子路由无守卫 | P2 | §11.2 修正 | 已回写 |
| R4-20 | `design/03` 端点清单过期 | P2 | 以运行时路由重新生成 | 已回写 |

### 16.5 R5：内部一致性复核（2026-11-21）

| # | 不一致 | 修正 |
|---|---|---|
| R5-01 | `support` 作为持久化 scope vs `roles.data_scope` 只允许 self/team | 删除持久化 support；运行时 `support_mode` |
| R5-02 | `role_permissions.scope` 默认 `global` vs 个人节点禁 global | 默认改 `self`；写入时即时校验 |
| R5-03 | “多 grant 取最宽 scope” vs “self 不因叠加变 all” | 个人权限只能收敛到 self；support 只能由会话开启 |
| R5-04 | admin.support.view 节点表 A/S vs 矩阵 admin ○ | 统一 admin `○`、support `✔` |
| R5-05 | dbexplorer “可读全库” vs admin 默认不看个人数据 | 白名单 + 个人表禁止 + 支持会话专用 API |
| R5-06 | `risk.alerts.ack` 用 GET vs 支持模式只读 | 新增 `POST /api/risk/alerts/ack` |
| R5-07 | `/api/version` 归 A/O vs 前端无 token 轮询 | 改 `public.version` |
| R5-08 | `permissions.parent_id INT` vs 种子写权限码 | 先 NULL 插入再 UPDATE |
| R5-09 | `CREATE OR REPLACE VIEW` vs 改首列 | DROP + CREATE |
| R5-10 | `scope=all` 请求链路 vs 不引入静态 all | 统一 self/support |
| R5-11 | Agent 会话支持读取“可选” vs “不适用” | 默认不可读；`support_readable=false` |
| R5-12 | operator 有 agent.use vs 无个人数据 | 工具级权限映射；无个人工具权限 |
| R5-13 | `research.radar.view` 节点表 T/N vs 矩阵 A/T/N | 统一 A/T/N |
| R5-14 | `feature.view` 重复行 | 合并 |
| R5-15 | `system.settings.view` 给 operator vs LLM Key 仅 admin | operator 去掉 |
| R5-16 | `dag.publish` operator ✔ vs CFG-05 admin 发布 | operator 默认 `○` |
| R5-17 | Q1–Q12 待确认 vs 正文已决定 | §17 改为“默认决策”，用户可否决 |
| R5-18 | support 的 `S` 无法落库为普通 grant | `role_permissions.support_only`；仅支持会话生效；§7.2.4/§6.8 |
| R5-19 | 节点表与矩阵默认角色不一致 | 矩阵为唯一事实源；节点表省略 admin 个人 self；§6.2 约定 |
| R5-20 | `watch.group.view` 父节点两套写法；根集合漏 risk.* | 节点表 NULL + 附录 B 不再挂父；§7.7 根集合修正 |
| R5-21 | override 个人权限可写 `global` | 写入校验 + effective_grant 收敛 self；§7.2.6/§7.7 |
| R5-22 | `public.version`/`ws.connect` 与 `permissions.type` CHECK 不兼容 | CHECK 增加 `public/system`；种子补齐；§7.2.3/§18.2 |
| R5-23 | `agent.exec` 可绕过 A7 直连 DB/网络 | 沙箱禁网/禁 DB socket/禁驱动；§6.2.5/WB-15 |
| R5-24 | `agent.sql.raw` 白名单可被子查询/JOIN 绕过 | SQL AST 解析所有表引用；§18.6 |
| R5-25 | 模型告警 ack 语义未定义 | `model.alerts.ack`，A/O 全局确认；§6.2.3/§8.4 |
| R5-26 | `intraday_signal_outcome` owner 与全局统计冲突 | 回填后 NOT NULL；全局统计按 `(stock_code,kind,alert_time)` 去重；§7.3.1/§8.4 |
| R5-27 | `03_add_user_columns` 漏 3 列 | 补 `intraday_ai_event/intraday_signal_outcome/dag_run_log`；§7.3.2 |
| R5-28 | 默认组唯一索引早于去重 | 05 先规范化再建索引；§7.3.2/§7.6 |

### 16.6 S1–S3：三轮自洽式审查（2026-11-21）

> 目标：不是再查代码，而是检查“导航—权限—API—原型”四层是否自洽。每轮独立执行，发现的修正立即回写。

#### S1 导航/权限节点

| # | 检查项 | 方法 | 结果 |
|---|---|---|---|
| S1-1 | 一级 tab key 唯一 | 提取 §11.2 表，统计 19 个 key | 0 重复 |
| S1-2 | 后台组位于【数据】之后 | 检查 `w` 索引 14、后台 `y/z/h/o` 索引 15–18 | 通过 |
| S1-3 | 每个 tab 都有权限码 | 19 个 tab → §6.2 节点集合 | 0 缺失 |
| S1-4 | 后台 tab 仅 admin | 提取矩阵 `system.user/role/audit/settings` 列 | 仅 A |
| S1-5 | 子路由有守卫 | `d/v/c/q/r/t` 映射到父权限 | 已定义 |
| S1-6 | `public.*`/`ws.connect` 类型合法 | 对照 `permissions.type` CHECK | `public/system` 已加入 |

**发现并修正**：

1. `o` 原先只存在于 `titleMap`，没有独立页面；已定义“系统设置”后台页，并从状态页移出。
2. `y/z/h` 为新增 key，与 `u/t/d/v/c/q/r` 无冲突；已写入 `nav.js` 与页面标题表。
3. `watch.group.view` 的父节点/根集合存在历史矛盾；已统一为根节点，附录 B 不再挂父。
4. `public.version`/`ws.connect` 与 `permissions.type` CHECK 不兼容；已扩展 CHECK 并补充种子。

**结论**：S1 通过。

#### S2 API/数据范围

| # | 检查项 | 方法 | 结果 |
|---|---|---|---|
| S2-1 | 后台页面对应 API | 用户→`/api/admin/users*`；角色→`/api/admin/roles*`；审计→`/api/admin/audit`；设置→`/api/settings*`、`/llm_config*` | 全部存在 |
| S2-2 | 后台权限仅 admin | 矩阵列检查 | `system.user/role/audit/settings` 仅 A |
| S2-3 | 支持会话只读 | `admin.support.view` + `support_only` + `SUP-READONLY` | 已定义 |
| S2-4 | 用户页不返回密码哈希 | API 响应约定 | 已声明 |
| S2-5 | 设置页不回显 LLM Key | 字段级脱敏 | `api_key/deepseek_key` 永不返回明文 |
| S2-6 | 审计页不读个人表 | 审计查询只允许 `audit_log` | 已声明 |
| S2-7 | 后台页面不混入个人数据 | 权限/资源矩阵 | 无个人域权限 |

**发现并修正**：

1. `system.settings.view` 原先给 operator；已收紧为 admin-only，operator 看不到 LLM Key。
2. `admin.support.view` 默认只给 support 角色（`support_only`），admin 为可选；避免 admin 无会话全知。
3. 用户页“支持会话”入口复用 `admin.support.view`，不新增页面级权限。
4. 审计页只读、不可删；before/after 脱敏；不提供个人表查询。

**结论**：S2 通过。

#### S3 原型/交互

| # | 检查项 | 方法 | 结果 |
|---|---|---|---|
| S3-1 | \| 位于【数据】之后 | 检查导航线框与 §11.2 | 通过 |
| S3-2 | \| 不可点击、不参与路由 | 原型说明 | 已声明 |
| S3-3 | 无后台 tab 时不渲染 \| | `showAdmin` 按可见 tab 集合 | 已声明 |
| S3-4 | 后台组仅 admin 可见 | 角色矩阵 + 导航说明 | 通过 |
| S3-5 | 移动端不用字面 \| | 抽屉分组标题“管理/后台管理” | 已声明 |
| S3-6 | 页面标题/hash 完整 | `y/z/h/o` 标题表 | 4 项齐全 |
| S3-7 | 403/空状态 | 无权限重定向；空列表/无审计提示 | 已定义 |
| S3-8 | 系统设置与个人设置分离 | `o` 后台页 vs 用户菜单“个人设置” | 已声明 |

**发现并修正**：

1. 分隔符不能放进 tab 数组（否则会被当成路由/按钮）；应作为独立 `<span class="nav-sep">|</span>` 或 CSS 竖线。
2. `showAdmin` 不能用单一布尔；改为 `visibleAdminTabs.length > 0`，否则无后台权限的用户会看到空分隔符。
3. `o` 系统设置从状态页移出；状态页只保留数据/系统状态，避免后台能力藏匿。
4. 后台页面统一 403 → 重定向到第一个有权限 tab；空状态显示“暂无数据/无权限”，不泄露资源存在性。

**结论**：S3 通过。

#### 三轮审查总表

| 轮次 | 范围 | 检查点数 | 发现/修正 | 结论 |
|---|---|---:|---:|---|
| S1 | 导航/权限节点 | 6 | 4 | 通过 |
| S2 | API/数据范围 | 7 | 4 | 通过 |
| S3 | 原型/交互 | 8 | 4 | 通过 |

> 三轮审查只覆盖设计自洽性；代码层 C-01~C-25 仍未实施，不能据此上线。

---

## 17. 默认决策（用户可否决）

> 用户已表示“可以按分析来”。以下按推荐值冻结；如否决，在 P0 评审记录中修改并回写正文。

| # | 问题 | 默认决策 | 影响 |
|---|---|---|---|
| Q1 | 账号创建方式 | **管理员创建**；不开放自助注册 | 认证/用户管理 |
| Q2 | 是否需要 `viewer` 角色 | **保留**，用于演示/只读 | 角色矩阵 |
| Q3 | `model.approve` 是否与训练分离 | **默认 admin 单独审批**；operator 的 `model.approve` 为可选 | 角色矩阵/ABAC |
| Q4 | 支持会话默认时长 | **默认 30 分钟，最长 60 分钟**；可提前撤销 | 支持模式 |
| Q5 | 普通用户是否可见模型纸面组合 | **可见但明确标注“模型账本”**；不可写 | 前端/API |
| Q6 | Agent 技能是否允许个人保存 | **允许，写 `data/skills/<user_id>/`**；全局技能仅 admin | Agent |
| Q7 | 飞书绑定方式 | **一次性码自助绑定**；未绑定拒绝 | 飞书 |
| Q8 | 是否需要多券商账户切换 | **本期一人一组合**；`account_no` 仅合并展示 | 数据模型 |
| Q9 | 审计保留期限 | **180 天**，之后归档 | 运维 |
| Q10 | 是否引入 PostgreSQL RLS | **本期应用层 + 跨用户测试**；RLS 作为二期加固 | 安全 |
| Q11 | `model_score_daily` 是否一期建设 | **二期**；一期在模型任务内按用户持仓生成卖出信号 | 模型链路 |
| Q12 | 普通用户能否使用 KEPL/特征页 | **trader 只读 KEPL**；feature 页 analyst 及以上 | 角色矩阵 |

---

## 18. 附录

### 18.1 附录 A：角色种子 SQL（示例）

```sql
INSERT INTO roles (code, name, description, is_system, data_scope) VALUES
('admin',    '系统管理员', '全局数据与系统管理；个人数据仅 self', true, 'self'),
('operator', '数据/模型运维', '跑批/补数/模型/特征/DAG；不含个人数据', true, 'self'),
('trader',   '交易用户', '个人持仓/自选/交割单/监测 + 共享研究', true, 'self'),
('analyst',  '研究用户', '共享研究 + 个人自选；不含持仓/交割单', true, 'self'),
('viewer',   '只读访客', '共享行情/信号/资讯只读', true, 'self'),
('support',  '支持人员', '限时只读用户个人数据，需支持会话', true, 'self')
ON CONFLICT (code) DO NOTHING;
```

### 18.2 附录 B：权限种子 SQL（示例，完整清单见 §6.2）

```sql
-- 第一步：全部以 NULL parent_id 插入（parent_id 是 INT，不能直接写权限码字符串）
INSERT INTO permissions (code, name, type, parent_id, route, sort_order, is_system) VALUES
('portfolio.position.view', '持仓查看', 'menu', NULL, 'p', 10, true),
('portfolio.position.edit', '持仓编辑', 'action', NULL, NULL, 11, true),
('watch.group.view', '自选分组查看', 'menu', NULL, 'p', 12, true),
('research.treemap.view', '树图/市场情绪', 'menu', NULL, 'm', 20, true),
('research.stock.view', '个股研究', 'menu', NULL, 'l', 30, true),
('research.signal.view', '买入信号', 'menu', NULL, 's', 40, true),
('signal.personal.view', '我的卖出信号', 'action', NULL, NULL, 41, true),
('research.news.view', '资讯', 'menu', NULL, 'n', 50, true),
('agent.use', '站内问答', 'menu', NULL, 'i', 60, true),
('agent.exec', '代码执行', 'sensitive', NULL, NULL, 61, true),
('model.view', '模型只读', 'menu', NULL, 'a', 70, true),
('model.alerts.view', '模型告警', 'action', NULL, NULL, 71, true),
('feature.view', '特征只读', 'menu', NULL, 'e', 80, true),
('function.view', '函数只读', 'menu', NULL, 'f', 90, true),
('dag.view', 'DAG 只读', 'menu', NULL, 'g', 100, true),
('system.status.view', '系统状态', 'menu', NULL, 'x', 110, true),
('system.dbexplorer.view', '数据库浏览', 'menu', NULL, 'w', 120, true),
('admin.support.view', '支持模式', 'sensitive', NULL, NULL, 130, true),
('model.alerts.ack', '模型告警确认', 'action', NULL, NULL, 72, true),
('public.login', '登录', 'public', NULL, NULL, 900, true),
('public.health', '健康检查', 'public', NULL, NULL, 901, true),
('public.version', '版本自检', 'public', NULL, NULL, 902, true),
('public.feishu', '飞书回调', 'public', NULL, NULL, 903, true),
('ws.connect', 'WebSocket 连接', 'system', NULL, NULL, 904, true)
ON CONFLICT (code) DO NOTHING;

-- 第二步：按 code 回填 parent_id（子节点 -> 父节点）
UPDATE permissions c SET parent_id = p.id
FROM permissions p
WHERE (c.code, p.code) IN (
  ('portfolio.position.edit', 'portfolio.position.view'),
  ('portfolio.history.view', 'portfolio.position.view'),
  ('watch.group.edit', 'watch.group.view'),
  ('signal.personal.view', 'portfolio.position.view'),
  ('research.market.view', 'research.treemap.view'),
  ('research.radar.view', 'research.stock.view'),
  ('agent.skill.edit', 'agent.use'),
  ('agent.exec', 'agent.use'),
  ('agent.sql.raw', 'agent.use'),
  ('model.alerts.view', 'model.view'),
  ('model.alerts.ack', 'model.alerts.view'),
  ('feature.edit', 'feature.view'),
  ('feature.compute', 'feature.view'),
  ('function.edit', 'function.view'),
  ('function.test', 'function.view'),
  ('dag.edit', 'dag.view'),
  ('dag.publish', 'dag.edit'),
  ('dag.execute', 'dag.view'),
  ('dag.terminate', 'dag.execute')
);
```

> 完整节点清单见 §6.2；种子生成器应从同一份权限码常量导出，避免 SQL 与代码漂移。其余节点同样先以 `parent_id=NULL` 插入，再按 code 回填。

### 18.3 附录 C：跨用户隔离测试脚本骨架

```python
# tests/integration/test_multiuser_isolation.py
def test_portfolio_isolation(client, user_a_token, user_b_token):
    # A 建仓
    r = client.post("/api/portfolio", json={"stock_code": "600000", "quantity": 100, "cost_price": 10},
                    headers={"Authorization": f"Bearer {user_a_token}"})
    assert r.status_code == 200
    # B 看不到
    r = client.get("/api/portfolio", headers={"Authorization": f"Bearer {user_b_token}"})
    assert all(p["stock_code"] != "600000" for p in r.json()["positions"])
    # B 改 A 的持仓 → 404
    r = client.put("/api/portfolio/600000", json={"quantity": 1},
                   headers={"Authorization": f"Bearer {user_b_token}"})
    assert r.status_code == 404
```

### 18.4 附录 D：字段命名与权限码约定

| 约定 | 示例 |
|---|---|
| 权限码 | `domain.resource.action`，全小写，点分隔 |
| 功能节点 parent | 页面节点为父，操作节点为子；共享数据根节点无父 |
| 个人表 owner | 统一 `user_id`，不使用 `owner_id/created_by` |
| 数据范围 | `self` / `team` / `global`；`all` 不支持静态角色 |
| 审计 action | `domain.action`，如 `portfolio.update`、`model.train`、`admin.support.access` |
| 时间 | 统一 `TIMESTAMP`，业务日期 `DATE`；北京时间由应用层处理 |

### 18.5 附录 E：版本与变更记录

| 版本 | 日期 | 变更 |
|---|---|---|
| v0.9 | 2026-11-21 | 初稿：现状边界、RBAC 节点、数据范围、ABAC 钩子、数据模型、迁移/测试/风险 |
| v1.0-rc1 | 2026-11-21 | 完成 R1–R5 反向审查；修正 support scope/DB 白名单/Agent 工具/迁移 DDL/写入路径等 60+ 项；关闭 Q1–Q12 为默认决策 |
| v1.0 | 待评审 | 代码级 C-01~C-25 修复完成、路由覆盖测试全绿、测试库迁移/回滚演练通过后冻结 |

---

### 18.6 附录 F：DB 浏览 / Agent SQL 表白名单

> `system.dbexplorer.view` 与 `agent.sql.raw` 只允许下表“共享/系统”表；个人表、权限表、审计表、支持会话表一律禁止。敏感列即使表在白名单内也要脱敏。

**允许（只读）**：

| 类别 | 表/视图 |
|---|---|
| 行情 | `daily_quote`、`index_daily_quote`、`stock_master`、`trade_calendar`、`stock_treemap_cache`、`stock_treemap_intraday`、`sector_flow_daily` |
| 基本面 | `stock_fundamentals`、`stock_fundamentals_history`、`stock_share_float`、`index_valuation`、`macro_indicator` |
| 资金/筹码 | `stock_moneyflow`、`stock_margin_detail`、`stock_top_list`、`stock_holder_number` |
| 特征/函数 | `feature_values`、`features`、`functions`、`function_versions`、`factor_ic_stats` |
| 模型 | `model_versions`、`training_trials`、`model_health`、`backtest_records`、`backtest_trades`、`backtest_daily_records`、`paper_positions`、`paper_trades`、`signal_history`（全局买入） |
| 资讯 | `news_item`、`news_summary`、`news_stock`、`news_crawl_state`、`news_poll_state` |
| 可转债 | `cb_basic`、`cb_daily`、`cb_share`、`cb_metrics` |
| 统计/系统 | `daily_completeness`、`entity_stats`、`data_stats_cache`、`data_lineage`、`dag_flows`、`dag_run_log`、`backfill_tasks`、`tushare_quota` |
| 雪球 | `xueqiu_status`、`xueqiu_sentiment`（`user_id` 是平台用户，不是本站 owner） |

**禁止**：

| 类别 | 表 |
|---|---|
| 身份/权限 | `users`、`roles`、`permissions`、`role_permissions`、`user_roles`、`user_permission_overrides`、`support_sessions` |
| 个人交易 | `portfolio`、`portfolio_history`、`watch_groups`、`watch_group_items`、`trade_slip`、`trade_import_batch`、`trade_intraday`、`trade_cash_snapshot`、`trade_plan`、`trade_journal` |
| 个人分析 | `risk_alerts`、`user_config`、`user_signal_history`、`intraday_ai_event`、`intraday_signal_outcome` |
| Agent 隐私 | `agent_sessions`、`agent_messages`、`agent_feedback`、`agent_usage_daily` |
| 审计/配置 | `audit_log`、`strategy_config`（含 LLM Key） |

**列黑名单**：`password_hash`、`api_key`、`deepseek_key`、`token_hash`、`params`（`strategy_config`）、`before`/`after`（审计）。

**SQL 约束**：

- 只允许单条 `SELECT`/`WITH`；禁止 `COPY`、`SET`、`DO`、`CALL`、`CREATE`、`ALTER`、`DROP`。
- **必须用 SQL AST 解析所有表引用**（含 CTE、子查询、JOIN、UNION），逐个校验白名单；禁止 `pg_catalog`/`information_schema`/系统表。正则只做初筛，不能作为唯一防线。
- 个人表即使被间接引用（子查询/JOIN）也必须拒绝；建议优先使用只读视图 + PostgreSQL RLS 作为第二道防线。
- 语句超时 25s、行数上限 5000；结果按体积截断。

---

## 19. 设计完成定义（Definition of Done）

开发前，本文档必须满足：

- [ ] §6.2 所有功能节点有唯一权限码、父节点、API 映射。
- [ ] §6.2.5 Agent 工具逐个映射到底层权限；`agent.exec` 仅 admin。
- [ ] §6.4 角色矩阵评审通过；每个权限至少有一个角色拥有；operator 无个人数据/系统设置/DB 浏览。
- [ ] §6.5 数据范围矩阵覆盖所有个人表；支持模式双凭证/资源级排除已确认。
- [ ] §6.7 ABAC 钩子全部有触发点、条件、默认值、失败动作、审计；多 grant conditions 取 AND。
- [ ] §7 新表/字段/约束/索引/迁移/回滚齐全；`CREATE OR REPLACE VIEW` 已改 DROP/CREATE。
- [ ] §7.3.3 写入路径清单逐函数签字；所有 INSERT/UPDATE 有 owner。
- [ ] §7.3.4 `ON CONFLICT`/主键切换影响全部修正。
- [ ] §8 业务规则可测试；WB-01~WB-30 有验证方式。
- [ ] §9 盘中/日频/新闻/雷达/WS/交易复盘/执行质量链均有用户上下文。
- [ ] §10 所有现有约 232 条路由有权限码或 public 标记（R4）；WS/签名文件例外已评审。
- [ ] §11 前端导航（含 k/u）、子路由、按钮、WS、本地存储按权限与用户隔离。
- [ ] §11.2 后台管理组（用户/角色权限/审计/系统设置）位于【数据】之后，用 `|` 分隔，仅 admin 可见；无后台 tab 时不渲染分隔符；移动端用分组标题。
- [ ] 后台页面 `y/z/h/o` 的 hash、标题、权限守卫、403/空状态齐全；审计只读、设置页脱敏。
- [ ] §13.4 C-01~C-25 现状代码缺陷全部关闭。
- [ ] §14 测试矩阵覆盖认证、跨用户、权限、支持模式、ABAC、迁移、监测、回归；X1–X30 全绿。
- [ ] §17 Q1–Q12 默认决策已确认或否决并回写。
- [ ] `design/03-API接口文档.md` 按运行时 232 条路由更新。
- [ ] 在测试库完成一次完整迁移 + 回滚演练；`init_db` 重启后旧索引/旧视图不再出现。

---

> **本文档不是开发许可。** 只有上述 DoD 全部勾选、评审记录关闭后，才进入 P1 开发。

---

## 20. 后续功能准入规则（强制）

> 本节是长期维护规则，适用于本设计实施后的所有新功能；与代码同仓库维护。

1. **先判定节点，再写代码**：新增/修改页面、菜单、子 tab、按钮、API、Agent 工具、敏感动作时，先回答：
   - 是否是用户可独立授权的能力？
   - 属于共享域还是个人域？`scope` 是 `self` 还是 `global`？
   - 是否仅支持会话可用（`support_only`）？
   - 无权限时应隐藏入口还是返回 403？
2. **需要节点的必须同提交更新**：
   - `app/auth/definitions.py`：权限码、名称、类型、父节点、route、API 映射、默认角色授权；
   - `app/auth/route_permissions.py`：所有新 HTTP 路由的权限映射（或显式 public/auth_only）；
   - 角色矩阵/迁移种子：默认授予哪些角色，旧角色如何迁移；
   - 前端：导航/子 tab/按钮按权限显示（`v-perm` 或 `auth.can`）；
   - 本文档 §6.2 节点表、§6.4 角色矩阵、§10 API 映射、§11 前端节点；
   - 多角色测试：至少覆盖"有权可用 + 无权隐藏/403"两条路径。
3. **无需节点的情况必须留痕**：纯内部定时任务、DAG 节点、纯共享只读聚合若没有独立授权入口，可不建节点，但要在代码注释/设计文档注明归属父节点；一旦提供页面或 API 操作入口，入口本身必须有节点。
4. **禁止只加后端路由不登记权限**：`init_db` 启动自检会拒绝未映射路由；不能通过关闭自检绕过。
5. **权限码只增不改语义**：已有权限码的含义、scope 语义变更必须走迁移评审；废弃节点先保留并标记 deprecated，确认无角色引用后再删。
6. **默认拒绝**：新节点默认不授予除 admin 外的角色；需要开放时显式写入角色矩阵，不能依赖"前端隐藏"当权限。
