# 模型训练模块 — 测试用例文档

> 版本：v1.0 | 2026-06-22

---

## 一、模型生命周期状态机

```
DRAFT → TRAINING → PENDING → (审批) → ACTIVE → ARCHIVED
  ↑        ↓          ↓                    ↓
  └── 失败回退       REJECTED            FAILED
```

---

## 二、API 测试用例

### TC-API-01: 创建模型版本

| 项 | 内容 |
|----|------|
| **前置** | DB 中无模型版本 |
| **操作** | `POST /api/v1/models` `{"model_name":"测试模型",...}` |
| **期望** | 返回 `{"ok":true,"version":"v1.0","status":"DRAFT"}` |
| **验证** | `model_versions` 表新增 1 行，status='DRAFT'，config 含四层配置 |

**异常场景：**

| TC-API-01a | 名称为空 | 返回 400 "模型名称不能为空" |
| TC-API-01b | 名称为纯空格 | 返回 400 "模型名称不能为空" |
| TC-API-01c | 版本号递增 | 已有 v1.0 再创建 → 返回 v2.0 |
| TC-API-01d | features 传空数组 | 正常创建，config.features=[] |
| TC-API-01e | 日期非法(start > end) | 正常创建（无校验），后续训练时失败 |

### TC-API-02: 获取模型列表

| 项 | 内容 |
|----|------|
| **前置** | DB 中有 v1.0(DRAFT), v2.0(ACTIVE) |
| **操作** | `GET /api/v1/models` |
| **期望** | 返回 2 个版本，按 created_at 倒序 |
| **验证** | 字段完整：version, model_name, status, sharpe, win_rate, ... |

**异常场景：**

| TC-API-02a | DB 为空 | 返回 `{"versions":[],"count":0}` |
| TC-API-02b | deleted_at 列不存在（旧 DB 迁移前） | 回退查询正常返回 |
| TC-API-02c | 有已软删除的版本 | deleted_at IS NOT NULL 的行不返回 |

### TC-API-03: 删除模型

| 项 | 内容 |
|----|------|
| **前置** | v1.0(DRAFT)，无关联数据 |
| **操作** | `DELETE /api/v1/models/v1.0?mode=soft` |
| **期望** | 返回 `{"ok":true,"mode":"soft"}`，deleted_at 非空 |

**异常场景：**

| TC-API-03a | 版本不存在 | 返回 404 |
| TC-API-03b | 已软删除再删 | 返回 400 "该模型已被删除" |
| TC-API-03c | ACTIVE 状态删除 | 返回 400 "ACTIVE 状态模型不能删除" |
| TC-API-03d | 有信号数据的模型 hard 删除 | 返回 400 "该模型有关联数据" |
| TC-API-03e | 无关联数据 hard 删除 | 返回 200，关联表全部清理 |
| TC-API-03f | mode 参数非法 | 返回 400 "mode 参数只能是 soft 或 hard" |
| TC-API-03g | deleted_at 列不存在时 soft 删除 | 报错（应修复迁移） |

### TC-API-04: 审批模型

| 项 | 内容 |
|----|------|
| **前置** | v1.0(PENDING), v2.0(ACTIVE) |
| **操作** | `POST /api/v1/models/v1.0/approve` |
| **期望** | v1.0→ACTIVE, v2.0→ARCHIVED |

**异常场景：**

| TC-API-04a | 状态不是 PENDING | 返回 400 |
| TC-API-04b | 版本不存在 | 返回 404 |
| TC-API-04c | DB 连接失败 | 返回 500 |

---

## 三、训练 DAG 节点测试用例

### TC-TRAIN-01: 正常训练流程

| 项 | 内容 |
|----|------|
| **前置** | v1.0(DRAFT)，6 张指标表有足够数据 |
| **操作** | 触发 `model_train` DAG 节点 |
| **期望流程** | ① status→TRAINING ② 步骤1→5 依次完成 ③ status→PENDING，best_params 写入，模型文件落盘 |
| **验证** | `model_versions.status='PENDING'`，`data/models/v1.0/xgb_*.pkl` 存在 3 个文件 |

### TC-TRAIN-02: 训练步骤可视化

| 项 | 内容 |
|----|------|
| **前置** | 训练已启动 |
| **操作** | 观察 WS `dag_log` 消息中 `model_train` 节点的 `detail` 字段 |
| **期望** | "步骤1:加载指标" → "步骤2:特征工程" → "步骤3:标签计算" → "步骤4:模型训练" → "步骤5:存储结果" → success |
| **验证** | 前端 ModelTraining.vue 步骤圆圈依次变绿 ✓ |

### TC-TRAIN-03: 训练失败回退

| 项 | 内容 |
|----|------|
| **前置** | v1.0(DRAFT) |
| **操作** | 训练过程中抛异常（如指标数据不足） |
| **期望** | `dag_run_log.status='failed'`，`model_versions.status` 回退为 'DRAFT' |
| **验证** | 状态页 DAG 流程图显示红色，模型页显示 DRAFT |

### TC-TRAIN-04: 指标数据不足

| 项 | 内容 |
|----|------|
| **前置** | 指标表为空或行数 < 5000 |
| **操作** | 触发训练 |
| **期望** | 立即返回 0，`detail='指标数据不足(N行)'` |
| **验证** | 状态仍为 DRAFT |

### TC-TRAIN-05: 特征计算异常

| 项 | 内容 |
|----|------|
| **前置** | 指标表有数据但某列为 NULL |
| **操作** | 触发训练 |
| **期望** | `dropna` 过滤后可能跳过异常列，或抛异常回退 DRAFT |
| **验证** | 不应静默产生空模型 |

### TC-TRAIN-06: 模型存储路径冲突

| 项 | 内容 |
|----|------|
| **前置** | `data/models/v1.0/` 已存在旧模型文件 |
| **操作** | 重新训练同一版本 |
| **期望** | 覆盖旧文件，正常写入 |
| **验证** | 文件 mtime 更新 |

### TC-TRAIN-07: sklearn 版本不兼容

| 项 | 内容 |
|----|------|
| **前置** | 训练环境的 sklearn 与预测环境版本不一致 |
| **操作** | 用旧版本 sklearn 训练的 pkl 在新版本加载 |
| **期望** | `pickle.load` 可能报错，dag_task_model_signal 中 except 捕获后回退规则模式 |
| **验证** | 信号生成不中断，日志有 WARNING |

### TC-TRAIN-08: 磁盘空间不足

| 项 | 内容 |
|----|------|
| **前置** | 磁盘满 |
| **操作** | 触发训练 |
| **期望** | `open(path,'wb')` 抛 IOError → 异常被捕获 → 回退 DRAFT |
| **验证** | dag_run_log 记录失败原因 |

---

## 四、前端测试用例

### TC-UI-01: 模型列表加载

| 项 | 内容 |
|----|------|
| **前置** | API 正常返回 |
| **操作** | 打开模型页 |
| **期望** | 左面板显示版本列表，默认选中第一个 |
| **验证** | `store.isMock=false` |

**异常场景：**

| TC-UI-01a | API 不可用 | 显示空列表 + "暂无模型版本" |
| TC-UI-01b | API 返回空数组 | 显示空列表，不显示 mock 数据 |
| TC-UI-01c | 网络超时 | catch 后显示空列表 |

### TC-UI-02: 创建模型弹窗

| 项 | 内容 |
|----|------|
| **前置** | 模型页已加载 |
| **操作** | 点「+ 创建」→ 填名称 → 点「创建」 |
| **期望** | 弹窗关闭，列表刷新，新版本出现 |

**异常场景：**

| TC-UI-02a | 名称为空点创建 | 应前端拦截（目前无，需加） |
| TC-UI-02b | API 返回 400 | alert 显示错误信息 |
| TC-UI-02c | 网络错误 | alert "创建失败" |

### TC-UI-03: 训练步骤显示

| 项 | 内容 |
|----|------|
| **前置** | 训练已启动 |
| **操作** | 进入训练 Tab |
| **期望** | 5 个步骤圆圈，进行中蓝色⟳，已完成绿色✓ |

**异常场景：**

| TC-UI-03a | WS 断连 | 步骤停止更新，刷新按钮可手动重查 |
| TC-UI-03b | WS 消息中无 model_train 节点 | 步骤不更新，保持上次状态 |
| TC-UI-03c | detail 格式异常（无"步骤"字样） | 步骤不更新 |
| TC-UI-03d | 训练失败 | WS 推 dag_log status=failed → 当前步骤不变，需手动刷新看状态 |

### TC-UI-04: H5 折叠

| 项 | 内容 |
|----|------|
| **前置** | 屏幕 < 768px |
| **操作** | 打开模型页 |
| **期望** | 侧边栏折叠，显示「版本列表 ▸」按钮，点按钮展开 |

| TC-UI-04a | PC 端(≥768px) | 侧边栏始终展开，无折叠按钮 |

### TC-UI-05: 删除确认弹窗

| 项 | 内容 |
|----|------|
| **操作** | hover DRAFT 模型 → 点 ✕ |
| **期望** | 弹窗显示"可永久删除"或"有 N 条关联数据" |

| TC-UI-05a | 关闭弹窗 | 弹窗关闭，不删除 |
| TC-UI-05b | 确认删除 | 调 API → 成功后列表刷新 |

---

## 五、状态一致性测试

### TC-STATE-01: 双表状态同步

| 项 | 内容 |
|----|------|
| **场景** | 训练成功 |
| **期望** | `dag_run_log.status='success'` ∧ `model_versions.status='PENDING'` |

| TC-STATE-01a | 训练失败 | `dag_run_log.status='failed'` ∧ `model_versions.status='DRAFT'` |
| TC-STATE-01b | 容器被杀 | `_recover_orphaned_tasks` 将 `dag_run_log` running→terminated，但 `model_versions` 仍为 TRAINING（**已知缺陷**） |

### TC-STATE-02: 审批状态联动

| 项 | 内容 |
|----|------|
| **场景** | 审批通过 |
| **期望** | 旧 ACTIVE→ARCHIVED，新版本→ACTIVE |

| TC-STATE-02a | 审批拒绝 | 新版本→REJECTED |

---

## 六、已知缺陷与修复记录

| 编号 | 场景 | 状态 | 修复 |
|------|------|:---:|------|
| GAP-01 | 容器重启后 model_versions 状态残留 | ✅ v2.33 | `_recover_orphaned_tasks` 增加 `UPDATE model_versions SET status='DRAFT' WHERE status='TRAINING'` |
| GAP-02 | 训练页失败不自动刷新 | ✅ v2.33 | WS 收到 `model_train` success/failed 时自动 `store.loadVersions()` |
| GAP-03 | H5 折叠状态不记忆 | ✅ v2.33 | `sidebarOpen` 移入 `modelStore`，跨页面保持 |
