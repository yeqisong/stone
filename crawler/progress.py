"""下载进度管理 — 支持断点续传。"""
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Set, Optional
from loguru import logger

PROGRESS_FILE = Path("data/download_progress.json")


def _default_state() -> dict:
    return {
        "daily_kline": {
            "completed": [],
            "last_updated": None,
            "total_stocks": 0,
            "downloaded_stocks": 0,
            "start_date": None,
            "end_date": None,
        },
        "fundamentals": {
            "completed": [],
            "last_updated": None,
            "total_stocks": 0,
            "downloaded_stocks": 0,
        },
    }


def load_progress() -> dict:
    """加载进度文件，不存在则返回空状态。"""
    if not PROGRESS_FILE.exists():
        return _default_state()
    try:
        with open(PROGRESS_FILE, "r") as f:
            data = json.load(f)
        # 兼容旧格式 (纯列表)
        if isinstance(data, list):
            logger.info("检测到旧格式进度文件，自动迁移")
            old = _default_state()
            old["daily_kline"]["completed"] = data
            old["daily_kline"]["downloaded_stocks"] = len(data)
            return old
        # 确保所有 key 存在
        default = _default_state()
        for section in default:
            if section not in data:
                data[section] = default[section]
            else:
                for k, v in default[section].items():
                    if k not in data[section]:
                        data[section][k] = v
        return data
    except (json.JSONDecodeError, IOError) as e:
        logger.warning(f"进度文件损坏，重新创建: {e}")
        return _default_state()


def save_progress(data: dict):
    """保存进度到文件（原子写入）。"""
    PROGRESS_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = str(PROGRESS_FILE) + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, str(PROGRESS_FILE))


def get_completed_stocks(section: str = "daily_kline") -> Set[str]:
    """获取已完成下载的股票代码集合。"""
    data = load_progress()
    return set(data.get(section, {}).get("completed", []))


def mark_stock_completed(code: str, section: str = "daily_kline"):
    """标记一只股票下载完成并保存。"""
    data = load_progress()
    sec = data.setdefault(section, _default_state()[section])
    if "completed" not in sec:
        sec["completed"] = []
    if code not in sec["completed"]:
        sec["completed"].append(code)
    sec["downloaded_stocks"] = len(sec["completed"])
    sec["last_updated"] = datetime.now().isoformat()
    save_progress(data)


def mark_batch_completed(codes: list, section: str = "daily_kline"):
    """批量标记股票下载完成。"""
    data = load_progress()
    sec = data.setdefault(section, _default_state()[section])
    if "completed" not in sec:
        sec["completed"] = []
    existing = set(sec["completed"])
    new_codes = [c for c in codes if c not in existing]
    sec["completed"].extend(new_codes)
    sec["downloaded_stocks"] = len(sec["completed"])
    sec["last_updated"] = datetime.now().isoformat()
    save_progress(data)


def get_db_completed_stocks(db, table: str = "daily_quote") -> Set[str]:
    """从数据库查询已有数据的股票代码集合（更可靠的断点续传依据）。"""
    from sqlalchemy import text
    try:
        result = db.execute(text(f"SELECT DISTINCT stock_code FROM {table}"))
        return {row[0] for row in result.fetchall()}
    except Exception as e:
        logger.warning(f"查询已有数据失败: {e}")
        return set()


def init_progress(total_stocks: int, start_date: str = None, end_date: str = None,
                  section: str = "daily_kline"):
    """初始化进度文件。"""
    data = load_progress()
    sec = data.setdefault(section, _default_state()[section])
    sec["total_stocks"] = total_stocks
    if start_date:
        sec["start_date"] = start_date
    if end_date:
        sec["end_date"] = end_date
    sec["last_updated"] = datetime.now().isoformat()
    save_progress(data)


def get_progress_summary() -> str:
    """获取进度摘要字符串。"""
    data = load_progress()
    lines = []
    for section in ["daily_kline", "fundamentals"]:
        sec = data.get(section, {})
        completed = sec.get("downloaded_stocks", 0)
        total = sec.get("total_stocks", 0)
        pct = f"{completed/total*100:.1f}%" if total > 0 else "N/A"
        lines.append(f"  {section}: {completed}/{total} ({pct})")
    return "\n".join(lines)
