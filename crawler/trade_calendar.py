"""交易日历管理。"""
from datetime import date, timedelta
from sqlalchemy import text


def load_calendar_from_csv(db_session, csv_path: str):
    """从 CSV 文件导入交易日历。CSV 格式: cal_date,is_trade_day,exchange。"""
    import csv
    with open(csv_path, 'r') as f:
        reader = csv.DictReader(f)
        for row in reader:
            is_trade_bool = row['is_trade_day'].strip() in ('1', 'true', 'True')
            db_session.execute(text(
                "INSERT INTO trade_calendar (cal_date, is_trade_day, exchange) VALUES (:d, :t, :e) ON CONFLICT (cal_date, exchange) DO NOTHING"
            ), {"d": row['cal_date'], "t": is_trade_bool, "e": row['exchange']})
    db_session.commit()


def is_trade_day(check_date: date = None, db_session=None) -> bool:
    """判断指定日期是否为交易日。默认查今日。"""
    if check_date is None:
        check_date = date.today()

    if db_session is None:
        # 回退：简单的周末判断（不准确，仅用于无DB的开发环境）
        return check_date.weekday() < 5

    result = db_session.execute(text(
        "SELECT is_trade_day FROM trade_calendar WHERE cal_date = :d LIMIT 1"
    ), {"d": check_date})
    row = result.fetchone()
    return bool(row[0]) if row else (check_date.weekday() < 5)


def get_last_trade_date(db_session) -> date:
    """获取最近一个交易日（从日历表）。"""
    today = date.today()
    result = db_session.execute(text(
        "SELECT MAX(cal_date) FROM trade_calendar WHERE cal_date <= :d AND is_trade_day = true"
    ), {"d": today})
    row = result.fetchone()
    return row[0] if row and row[0] else today


def generate_default_calendar(start_year: int = 2020, end_year: int = 2027):
    """生成默认交易日历（简单周末排除，不含节假日修正）。"""
    records = []
    current = date(start_year, 1, 1)
    end = date(end_year, 12, 31)
    while current <= end:
        is_trade = 1 if current.weekday() < 5 else 0
        for ex in ("SSE", "SZSE", "BSE"):
            records.append((current, is_trade, ex))
        current += timedelta(days=1)
    return records


def sync_from_baostock(db_session, start_year: int = None, end_year: int = None):
    """从 baostock 获取真实交易日历（含中国法定节假日）。

    自动覆盖 (今年-5) ~ (今年+3) 年，确保未来几年的日历就绪。
    UPSERT 语义：若 baostock 更新了节假日安排，DB 自动跟随更新。
    幂等，可重复执行。
    """
    import baostock as bs
    from loguru import logger
    today = date.today()
    if start_year is None:
        start_year = today.year - 5
    if end_year is None:
        end_year = today.year + 3

    lg = bs.login()
    if lg.error_code != '0':
        logger.warning(f"baostock 登录失败: {lg.error_msg}，使用周末日历")
        _insert_default(db_session, start_year, end_year)
        return

    try:
        start_str = f"{start_year}-01-01"
        end_str = f"{end_year}-12-31"
        rs = bs.query_trade_dates(start_date=start_str, end_date=end_str)
        if rs.error_code != '0':
            logger.warning(f"获取交易日历失败: {rs.error_msg}")
            _insert_default(db_session, start_year, end_year)
            return

        count = 0
        for ex in ("SSE", "SZSE", "BSE"):
            inserted = 0
            # 逐年份查询（baostock 可能只返回有数据的年份）
            for y in range(start_year, end_year + 1):
                ys = f"{y}-01-01"; ye = f"{y}-12-31"
                rs = bs.query_trade_dates(start_date=ys, end_date=ye)
                rows_in_year = 0
                while rs.next():
                    d = rs.get_row_data()
                    cal_date = d[0]
                    is_trade = True if d[1] == '1' else False
                    db_session.execute(text(
                        "INSERT INTO trade_calendar (cal_date, is_trade_day, exchange) VALUES (:d, :t, :e) "
                        "ON CONFLICT (cal_date, exchange) DO UPDATE SET is_trade_day=EXCLUDED.is_trade_day"
                    ), {"d": cal_date, "t": is_trade, "e": ex})
                    rows_in_year += 1
                    inserted += 1

                # baostock 暂无该年份数据 → 用周末日历垫底（等明年 baostock 更新后再覆盖）
                if rows_in_year == 0:
                    logger.info(f"    {y} 年 baostock 暂无数据，插入周末日历占位")
                    current = date(y, 1, 1)
                    end = date(y, 12, 31)
                    while current <= end:
                        is_weekend = current.weekday() >= 5
                        db_session.execute(text(
                            "INSERT INTO trade_calendar (cal_date, is_trade_day, exchange) VALUES (:d, :t, :e) "
                            "ON CONFLICT (cal_date, exchange) DO NOTHING"
                        ), {"d": current, "t": not is_weekend, "e": ex})
                        current += timedelta(days=1)
                    inserted += 365 if y % 4 != 0 or (y % 100 == 0 and y % 400 != 0) else 366

            count += inserted
            logger.info(f"  日历 {ex}: {inserted} 天 (含占位)")

        db_session.commit()
        logger.info(f"交易日历同步完成: 共 {count} 条记录 ({start_str} ~ {end_str})")
    finally:
        bs.logout()


def ensure_calendar_updated(db_session) -> bool:
    """检查未来年份的日历是否缺失，若缺失则自动同步。

    在每日 cron 中调用（如每天 crawl 前），确保历年的数据不被遗漏。
    返回 True 表示有更新，False 表示无需操作。
    """
    from loguru import logger

    today = date.today()
    future_year = today.year + 2  # 确保未来 2 年有数据

    # 查询 DB 中最大年份
    result = db_session.execute(text(
        "SELECT MAX(EXTRACT(YEAR FROM cal_date)) FROM trade_calendar"
    ))
    max_year_in_db = result.scalar()
    if max_year_in_db is None:
        max_year_in_db = 0

    if int(max_year_in_db) >= future_year:
        logger.debug(f"交易日历已覆盖至 {int(max_year_in_db)} 年，无需更新")
        return False

    logger.info(f"交易日历需要扩展：DB 仅到 {int(max_year_in_db)} 年，目标至 {future_year} 年")
    sync_from_baostock(db_session)
    return True


def _insert_default(db_session, start_year: int, end_year: int):
    """回退：插入仅含周末的简化日历。"""
    from loguru import logger
    records = generate_default_calendar(start_year, end_year)
    for cal_date, is_trade, ex in records:
        is_trade_bool = bool(is_trade)
        db_session.execute(text(
            "INSERT INTO trade_calendar (cal_date, is_trade_day, exchange) VALUES (:d, :t, :e) ON CONFLICT (cal_date, exchange) DO NOTHING"
        ), {"d": cal_date, "t": is_trade_bool, "e": ex})
    db_session.commit()
    logger.info(f"导入默认日历: {len(records)} 条 (仅周末)")


def get_year_calendar(db_session, year: int = None) -> list:
    """获取指定年份的完整交易日历。"""
    if year is None:
        from datetime import date as dt_date
        year = dt_date.today().year
    result = db_session.execute(text(
        "SELECT cal_date, is_trade_day, exchange FROM trade_calendar "
        "WHERE cal_date >= :s AND cal_date <= :e ORDER BY cal_date, exchange"
    ), {"s": f"{year}-01-01", "e": f"{year}-12-31"})
    return [{"date": str(r[0]), "is_trade_day": bool(r[1]), "exchange": r[2]}
            for r in result.fetchall()]
