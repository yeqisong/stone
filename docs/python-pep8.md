# Python PEP 8 及项目规范

## 命名规范
- 模块/包名: `snake_case`（如 `baostock_crawler.py`）
- 类名: `PascalCase`（如 `BaostockCrawler`）
- 函数/方法: `snake_case`（如 `download_all_stocks()`）
- 变量: `snake_case`（如 `stock_code`）
- 常量: `UPPER_CASE`（如 `API_URL`）
- 私有函数/属性: 前缀 `_`（如 `_fetch_kline()`）

## 类型提示
```python
from typing import List, Optional, Dict

def download_fundamentals(
    self, 
    codes: List[str] = None,
    skip_existing: bool = False
) -> int:
    """下载基本面数据。"""
    ...
```

## 文档字符串
```python
def func(param1: str, param2: int = 0) -> bool:
    """简短描述。
    
    Args:
        param1: 参数说明
        param2: 参数说明，默认0
    
    Returns:
        返回值说明
    
    Raises:
        ValueError: 异常情况说明
    """
```

## 导入顺序
1. 标准库（`os`, `sys`, `json`, `time`, `datetime`）
2. 第三方库（`fastapi`, `sqlalchemy`, `pandas`, `baostock`）
3. 本地模块（`from app.db...`, `from crawler...`）

## 项目 Python 要点
- 使用 SQLAlchemy ORM/原生 SQL 访问 PG
- 使用 `loguru.logger` 替代 `print`
- 使用 `text()` 包裹 SQL 字符串
- 数据库操作在 `try/finally` 中确保 `db.close()`
- FastAPI 路由注册在 `app/main.py` 中
