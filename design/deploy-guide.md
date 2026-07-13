# V3.0 服务器部署清单

> 版本: v3.0  
> 日期: 2026-07-15  
> 生产地址: https://s.pmlab.top

---

## 1. 代码打包

```bash
cd /Users/yeqisong/Desktop/项目/stone
git tag v3.0
git push origin v3.0
```

## 2. 服务器端操作

### 2a. 备份当前环境

```bash
ssh deploy@s.pmlab.top
cd /app/stone
# 备份 .env
cp .env .env.backup-$(date +%Y%m%d-%H%M%S)
# 备份数据库
docker compose exec -T db pg_dump -U stock stock_monitor > backup-$(date +%Y%m%d-%H%M%S).sql
```

### 2b. 拉取代码

```bash
git fetch --tags
git checkout v3.0
```

### 2c. 合并 .env 配置

```bash
# 对比新增项（DEEPSEEK_API_KEY、LOGIN_PASSWORD 等已存在则跳过）
diff .env.backup-* .env
# 关键确认项：
#   DATABASE_URL → 生产连接串未被覆盖
#   LOGIN_PASSWORD → 生产密码未被覆盖
#   DEEPSEEK_API_KEY → 已有则跳过
#   FEISHU_APP_ID / FEISHU_APP_SECRET → 已有则跳过
```

### 2d. 安装依赖 + 构建前端

```bash
source venv/bin/activate
pip install -r requirements.txt
pip install akshare baostock croniter cronstrue cron-validator

cd web-v2 && npm install && npm run build && cd ..
```

### 2e. 数据库迁移（自动）

重启后端时 `init_db` 会自动执行：

| 迁移 | 说明 |
|------|------|
| `ALTER TABLE features ADD COLUMN actual_row_count` | 数据预览优化（v2.8） |
| `ALTER TABLE features ADD COLUMN ...` | 其他列（幂等） |
| 清理 `dag_config` 种子数据 | 默认拓扑不再插入 |
| 清理 `stock_indicators_*` 迁移 | 旧表 DDL 已从 schema.py 移除 |

### 2f. 系统数据初始化

```bash
python3 -c "
from app.db.connection import get_sync_db
from sqlalchemy import text
db = get_sync_db()
# 特征数据在 init_db 中由已有的 INSERT 语句保证
# dag_config 节点类型数据在 init_db 中由已有的 SQL 保证
db.commit()
db.close()
"
```

## 3. 重启服务

```bash
docker compose down
docker compose up -d
# 确认所有容器 healthy
docker compose ps
# 查看启动日志
docker compose logs -f app
```

## 4. 生产验证清单

| 序号 | 验证项 | 预期 |
|------|--------|------|
| 1 | `curl https://s.pmlab.top/health` | `{"status":"ok"}` |
| 2 | 前端页面加载 | HTTP 200 |
| 3 | 登录 | 正常 |
| 4 | DAG 流程列表 | 已有流程正常显示 |
| 5 | 新建流程 → 拖拽节点 → 保存 → 编辑 | 反显正确 |
| 6 | 流程查看模式 | 只读画布 + 节点着色 |
| 7 | 流程日志页 | 展示历史任务 |
| 8 | 点击「⚡ 执行」 | 弹日期选择 → 确认 → 后台执行 |
| 9 | 模型列表 | 实体 tab 切换正常 |
| 10 | 新建模型 → 预检 | 缓存正常 |
| 11 | 状态页 | 偏好选择 + 补数按钮 |
| 12 | 特征管理 | 列表 + 计算正常 |
| 13 | 函数管理 | 列表 + 验证正常 |

## 5. 回滚方案

```bash
# 回退代码
git checkout <上一版本tag>
# 恢复.env
cp .env.backup-* .env
# 重启
docker compose down && docker compose up -d
```

> **注意**：v2.8→v2.9 移除的 `stock_indicators_*` 表和 `daily_update`/`model_train` 节点不会影响已有功能，回滚只涉及代码。
