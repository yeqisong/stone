"""可转债同步服务（tushare cb_basic/cb_daily/cb_share → 本地三表）。

- 基础信息/日行情/转股余额独立成表，不进 daily_quote/stock_master，
  避免 ETF 分类、treemap、模型扫描域被 6 位转债代码污染。
- 每次调用经 TushareQuota.consume() 计数限流；QuotaExhausted 上抛给任务层。
- sync_range 支持断点续跑：已入库日期直接跳过，按交易日升序补齐。
"""
from datetime import date

from loguru import logger
from sqlalchemy import text

from crawler.writers import (
    batch_upsert_cb_basic,
    batch_upsert_cb_daily,
    batch_upsert_cb_share,
)


def _source():
    from crawler.adapters import get_data_source_manager
    return get_data_source_manager().get_source()


def _conn():
    from app.db.connection import get_sync_db
    return get_sync_db()


def _trade_days(db, start_date, end_date):
    """本地交易日历（baostock 唯一源），升序。"""
    rows = db.execute(text(
        "SELECT DISTINCT cal_date FROM trade_calendar "
        "WHERE cal_date BETWEEN :s AND :e AND is_trade_day = true "
        "ORDER BY cal_date"
    ), {"s": start_date, "e": end_date}).fetchall()
    return [str(r[0])[:10] for r in rows]


def sync_basic(source=None, db=None):
    """拉取并落库可转债基础信息（一次调用，全量覆盖）。"""
    source = source or _source()
    own_db = db is None
    db = db or _conn()
    try:
        rows = source.fetch_cb_basic()
        saved = batch_upsert_cb_basic(db, rows)
        logger.info(f"[cb] basic 入库 {saved} 条")
        return saved
    finally:
        if own_db:
            db.close()


def sync_daily(source=None, db=None, trade_date=None):
    """拉取并落库指定交易日全市场可转债日行情。"""
    td = str(trade_date or date.today())[:10]
    source = source or _source()
    own_db = db is None
    db = db or _conn()
    try:
        rows = source.fetch_cb_daily(td)
        saved = batch_upsert_cb_daily(db, rows)
        logger.info(f"[cb] daily {td} 入库 {saved} 条")
        return saved
    finally:
        if own_db:
            db.close()


def sync_share(source=None, db=None, trade_date=None):
    """拉取该公告日发布的转股/余额（同一债不同公告日各存一行）。"""
    td = str(trade_date or date.today())[:10]
    source = source or _source()
    own_db = db is None
    db = db or _conn()
    try:
        rows = source.fetch_cb_share(td)
        saved = batch_upsert_cb_share(db, rows)
        logger.info(f"[cb] share {td} 入库 {saved} 条")
        return saved
    finally:
        if own_db:
            db.close()


def sync_share_history(source=None, db=None, reserve=200, progress_cb=None):
    """按转债逐只回补全部转股公告历史（cb_share 历史表）。

    1181 只 × 1 次调用；每只返回全部公告（单次上限 2000 行）。
    """
    source = source or _source()
    own_db = db is None
    db = db or _conn()
    from crawler.adapters.tushare_quota import TushareQuota, QuotaExhausted
    quota = TushareQuota.get()
    done = rows = 0
    halted = False
    halt_reason = ''
    try:
        codes = [r[0] for r in db.execute(text(
            "SELECT ts_code FROM cb_basic WHERE ts_code IS NOT NULL ORDER BY ts_code"
        )).fetchall()]
        for i, ts_code in enumerate(codes):
            if quota.remaining() <= reserve:
                halt_reason = f'配额熔断（余 {quota.remaining()} ≤ 保留 {reserve}）'
                halted = True
                break
            try:
                data = source.fetch_cb_share_history(ts_code)
                rows += batch_upsert_cb_share(db, data)
                done += 1
            except QuotaExhausted:
                halt_reason = '配额耗尽'
                halted = True
                break
            except Exception as e:
                logger.warning(f"[cb] share history {ts_code} 失败: {e}")
            if progress_cb and done and done % 25 == 0:
                try:
                    progress_cb(done, len(codes), ts_code, rows)
                except Exception:
                    pass
        return {'done': done, 'rows': rows, 'total': len(codes),
                'halted': halted, 'halt_reason': halt_reason}
    finally:
        if own_db:
            db.close()


def sync_range(start_date, end_date=None, source=None, db=None,
               reserve=200, max_days=None, progress_cb=None):
    """按交易日区间回补可转债日行情（断点续跑，配额熔断）。

    转股比例/剩余规模历史由 sync_share_history 逐债回补（公告口径），
    不再与逐日行情混在同一循环里。
    返回 {'done': 完成日期数, 'rows': 入库行数, 'skipped': 已存在日期数,
          'halted': bool, 'halt_reason': str}
    """
    end_date = str(end_date or date.today())[:10]
    start_date = str(start_date)[:10]
    source = source or _source()
    own_db = db is None
    db = db or _conn()
    from crawler.adapters.tushare_quota import TushareQuota, QuotaExhausted
    quota = TushareQuota.get()
    done = rows = skipped = 0
    halted = False
    halt_reason = ''
    try:
        days = _trade_days(db, start_date, end_date)
        for i, td in enumerate(days):
            if max_days and done >= max_days:
                halt_reason = f'单次上限 {max_days} 天'
                halted = True
                break
            daily_exists = bool(db.execute(text(
                "SELECT 1 FROM cb_daily WHERE trade_date=:d AND close > 0 LIMIT 1"), {"d": td}).fetchone())
            if daily_exists:
                skipped += 1
                continue
            if quota.remaining() <= reserve:
                halt_reason = f'配额熔断（余 {quota.remaining()} ≤ 保留 {reserve}）'
                halted = True
                break
            try:
                rows += sync_daily(source=source, db=db, trade_date=td)
                done += 1
            except QuotaExhausted:
                halt_reason = '配额耗尽'
                halted = True
                break
            except Exception as e:
                logger.warning(f"[cb] daily {td} 失败: {e}")
            if progress_cb:
                try:
                    progress_cb(done, len(days) - skipped, td, rows)
                except Exception:
                    pass
        return {'done': done, 'rows': rows, 'skipped': skipped,
                'halted': halted, 'halt_reason': halt_reason,
                'total_days': len(days)}
    finally:
        if own_db:
            db.close()
