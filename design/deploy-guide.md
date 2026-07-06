# 部署手册

> 版本：v2.5+ | 服务器：华为云 `myhuawei` | 域名：`https://s.pmlab.top`

---

## 一、部署前检查

### 1.1 本地代码检查 ✅

```bash
# 1. Python 编译检查
python3 -c "
import py_compile
import glob, os
for f in glob.glob('app/**/*.py', recursive=True) + glob.glob('crawler/**/*.py', recursive=True) + glob.glob('scripts/*.py'):
    py_compile.compile(f)
print('All Python OK')
"

# 2. 前端构建检查
npm --prefix web-v2 run build
# 确认 dist/ 生成成功，无报错
```

### 1.2 Git 状态检查 ✅

```bash
# 1. 确认所有改动已提交
git status
# 应显示：nothing to commit, working tree clean

# 2. 确认当前 tag
git tag --sort=-creatordate | head -3
# 记录当前版本号，如 v2.5

# 3. 确认 tag 指向最新提交
git rev-parse HEAD && git rev-parse $(git tag --sort=-creatordate | head -1)
# 两个 hash 应一致
```

---

## 二、部署操作

### 2.1 打版本 Tag

```bash
# 格式：v{major}.{minor}
# 当前最新是 v2.5，下一个是 v2.6
git tag v2.6
# 如果 tag 已存在但想移动到新提交：
# git tag -f v2.6 && echo "tag moved"
```

### 2.2 同步代码到服务器

> ⚠️ **关键：必须排除 .env 文件，防止覆盖服务器配置！**

```bash
rsync -avz \
  --exclude '.git/' \
  --exclude '.env' \
  --exclude '.env.prod' \
  --exclude '.env.local' \
  --exclude 'data/' \
  --exclude 'logs/' \
  --exclude 'node_modules/' \
  --exclude 'web-v2/dist/' \
  --exclude '__pycache__/' \
  --exclude '*.pyc' \
  --exclude '.DS_Store' \
  --exclude '*.patch' \
  --exclude '*.orig' \
  . myhuawei:/usr/local/htdoc/stone/
```

### 2.3 部署代码（优先 docker cp，仅有依赖变更时 build）

```bash
# 方式一：仅代码变更（推荐，秒级完成）
ssh myhuawei "\
  docker cp /usr/local/htdoc/stone/app stock-app:/app/ && \
  docker cp /usr/local/htdoc/stone/crawler stock-app:/app/ && \
  docker cp /usr/local/htdoc/stone/strategy stock-app:/app/ && \
  docker cp /usr/local/htdoc/stone/scripts stock-app:/app/ && \
  docker cp /usr/local/htdoc/stone/web-v2/dist stock-app:/app/web-v2/ && \
  docker restart stock-app"

# 方式二：依赖变更时（如 pip install 新包）
# ssh myhuawei "cd /usr/local/htdoc/stone && docker compose build app && docker compose up -d app --force-recreate"
```

### 2.3b 验证代码已生效（必须）

```bash
# 检查关键函数是否在容器中
ssh myhuawei "docker exec stock-app grep -c 'def _backtest.*close_prices' /app/scripts/pipeline.py"
# 应返回 >0，返回 0 表示代码未更新！
```

### 2.4 等待启动完成

```bash
# init_db 需要 2-4 分钟（同步交易日历 2020-2030 × 3 个交易所）
# 每 30 秒检查一次，最多等 10 分钟
for i in $(seq 1 20); do
  STATUS=$(curl -s https://s.pmlab.top/health 2>/dev/null)
  if echo "$STATUS" | grep -q '"status":"ok"'; then
    echo "✅ 上线成功: $STATUS"
    break
  fi
  echo "⏳ 等待中 ($((i*30))s)..."
  sleep 30
done
```

### 2.5 清理旧镜像和构建缓存

```bash
# 构建后立即清理（释放磁盘空间 + 防止缓存旧代码）
ssh myhuawei "docker image prune -f && docker builder prune -f"
```

> ⚠️ `docker compose build` 的 `COPY` 层可能被缓存，导致新代码未进入镜像。`builder prune -f` 清除构建缓存，确保下次构建必定重新 COPY 文件。

---

## 三、部署后验证

### 3.1 健康检查 ✅

```bash
curl -s https://s.pmlab.top/health | python3 -m json.tool
# 期望: {"status":"ok","database":"connected","version":"1.0.0","env":"prod"}
```

### 3.2 登录验证 ✅

```bash
# 测试登录
curl -s -X POST https://s.pmlab.top/api/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"123654Aa,."}'
# 期望: {"ok":true,"token":"...","username":"admin"}
```

### 3.3 新增功能检查 ✅

按本次变更检查新功能是否生效：

| 检查项 | 命令 | 期望 |
|--------|------|------|
| 数据源健康 | `curl -s https://s.pmlab.top/api/data-sources/health` | 返回 sources 列表 + active_source |
| 模型删除 | 前端操作：模型页 hover → 删除按钮 | 正常流程 |
| 适配器选源 | `docker logs stock-app \| grep '\[DataSource\]'` | 应有 health check 日志 |
| K线图 UI | 前端打开任意个股详情页 | 网格线浅灰、成交量不透明、布林线实线2px |
| 指数/ETF 列表 | 前端切换到指数/ETF tab | 有列表、有价格 |
| 指数详情页 | 前端点击有数据的指数 | 显示 K线图 + 行情概览 |
| stock_master 双类型 | 前端搜索 000001 | 应同时显示个股和指数 |

### 3.4 日志检查 ✅

```bash
ssh myhuawei "docker logs stock-app --tail 20 2>&1"
# 确认无 ERROR / FATAL
```

---

## 四、回滚操作

如果部署后出现严重问题：

```bash
# 1. 切到上一个 tag
git checkout v2.4

# 2. 同步代码
rsync -avz --exclude '.git/' --exclude '.env' ... . myhuawei:/usr/local/htdoc/stone/

# 3. 重建
ssh myhuawei "cd /usr/local/htdoc/stone && docker compose build --no-cache app && docker compose up -d app"
```

---

## 五、服务器环境信息

### 5.1 目录结构

```
/usr/local/htdoc/stone/
├── docker-compose.yml          # 主配置
├── docker-compose.override.yml # 覆盖配置（端口映射 + DB密码）
├── .env                        # 生产环境变量（⚠️ 不要覆盖）
├── .env.prod                   # 生产环境变量模板（⚠️ 不要覆盖）
├── Dockerfile                  # 应用镜像
├── data/pgdata/                # PostgreSQL 数据目录（⚠️ 不要删除）
├── ... (同步的业务代码)
```

### 5.2 容器信息

| 容器 | 服务名 | 端口 | 说明 |
|------|--------|------|------|
| `stock-app` | app | 8000(内部) | FastAPI 应用 |
| `stock-db` | db | 5432(内部) | PostgreSQL 15 |
| `stock-redis` | redis | 6379(内部) | Redis |

### 5.3 凭据

| 项目 | 值 |
|------|-----|
| 站点地址 | https://s.pmlab.top |
| 管理员 | admin / 123654Aa,. |
| DB 用户 | stock / admin123654Aa,. |
| DB 名称 | stock_monitor |
| SSH | `ssh myhuawei` |

---

## 六、常见问题排查

### Q1: 部署后 502 Bad Gateway

```bash
# 原因1: init_db 还在初始化（等 3-5 分钟）
# 原因2: .env 密码不对
ssh myhuawei "docker exec stock-app env | grep DATABASE_URL"
ssh myhuawei "docker exec stock-db env | grep POSTGRES_PASSWORD"
# 两者密码应一致

# 原因3: 代码有语法错误
ssh myhuawei "docker logs stock-app --tail 20"
```

### Q2: 数据库密码不匹配

```bash
# 先确认 DB 容器的密码
ssh myhuawei "docker exec stock-db env | grep POSTGRES_PASSWORD"
# 然后更新 .env 中的 DATABASE_URL 为相同密码
# 注意 URL 中特殊字符需要转义（如逗号 → %2C）
```

### Q3: 本地 .env 覆盖了服务器配置

这是**严重事故**。rsync 时注意 `--exclude '.env'`。

如果已发生：
1. 立即从服务器备份恢复（如果有）
2. 检查 `docker-compose.override.yml` 中的 DB 密码
3. 用正确的密码重建

### Q4: 数据库数据丢失

```bash
# 如果 pgdata 被误删，从最新备份恢复
scp data/restore.sql.gz myhuawei:/tmp/
ssh myhuawei "gunzip -c /tmp/restore.sql.gz | docker exec -i stock-db psql -U stock stock_monitor"
```

---

## 七、快捷部署命令

复制粘贴即可完成标准部署：

```bash
# === 一键部署（在项目根目录执行）===

# 1. 编译检查
python3 -c "import py_compile; [py_compile.compile(f) for f in __import__('glob').glob('app/**/*.py',recursive=True)+__import__('glob').glob('crawler/**/*.py',recursive=True)+__import__('glob').glob('scripts/*.py')]" 2>/dev/null
npm --prefix web-v2 run build 2>/dev/null | tail -1

# 2. 确认 git 状态
git status -s
echo "Current tag:" && git tag --sort=-creatordate | head -1

# 3. 打 tag（按需修改版本号）
git tag v2.6

# 4. 同步代码
rsync -avz --exclude '.git/' --exclude '.env' --exclude '.env.prod' --exclude '.env.local' --exclude 'data/' --exclude 'logs/' --exclude 'node_modules/' --exclude 'web-v2/dist/' --exclude '__pycache__/' --exclude '*.pyc' --exclude '.DS_Store' --exclude '*.patch' --exclude '*.orig' . myhuawei:/usr/local/htdoc/stone/

# 5. 部署代码（docker cp，秒级生效）
ssh myhuawei "docker cp /usr/local/htdoc/stone/app stock-app:/app/ && docker cp /usr/local/htdoc/stone/crawler stock-app:/app/ && docker cp /usr/local/htdoc/stone/scripts stock-app:/app/ && docker cp /usr/local/htdoc/stone/web-v2/dist stock-app:/app/web-v2/ && docker restart stock-app"

# 5b. 验证代码生效
ssh myhuawei "docker exec stock-app grep -c '关键字' /app/scripts/pipeline.py"

# 6. 等待上线（最多 10 分钟）
for i in $(seq 1 20); do
  STATUS=$(curl -s https://s.pmlab.top/health 2>/dev/null)
  if echo "$STATUS" | grep -q '"status":"ok"'; then echo "✅ 上线成功"; break; fi
  echo "⏳ 等待中 ($((i*30))s)..." && sleep 30
done
```
