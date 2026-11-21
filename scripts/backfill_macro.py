#!/usr/bin/env python
"""宏观指标历史回补（cn_cpi/cn_ppi/cn_m/cn_gdp/shibor → macro_indicator）。

DAG 节点 macro_daily 每次只回补滚动窗口（月度 24 个月 / GDP 8 季度 / SHIBOR
10 交易日）。本脚本把宏观指标铺到接口最早（1990 年起，SHIBOR 2006-10 起），
让大盘页宏观卡有完整历史。字段口径与 dag_task_macro_daily 逐一对齐：
value 统一存同比（shibor_on 存隔夜利率），extra 保留原始行关键字段。幂等 upsert。

用法：
    venv/bin/python scripts/backfill_macro.py            # 月度/季度全历史（约 5 次调用）
    venv/bin/python scripts/backfill_macro.py --shibor   # SHIBOR 按日回补（长跑，建议后台）

SHIBOR 接口只有单日参数（date），区间报价接口 shibor_quote 是银行报价语义
不能替代定盘利率，只能逐日循环——工作日约 5200 次调用 × 1.3s ≈ 2 小时，
自动跳过已有日期与周末，配额护栏收工后续跑。
"""
import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import json as _json

from loguru import logger
from sqlalchemy import text as _t

UPSERT = _t("""
INSERT INTO macro_indicator (indicator_code, period_date, value, yoy, extra)
VALUES (:c, :d, :v, :y, :e)
ON CONFLICT (indicator_code, period_date) DO UPDATE SET
    value = EXCLUDED.value, yoy = EXCLUDED.yoy, extra = EXCLUDED.extra
""")


def fv(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if f != f else f


def san(obj):
    """递归清洗 NaN：json.dumps 会把 float('nan') 输出成裸 NaN 字面量，
    PostgreSQL jsonb 拒收（早期宏观数据 extra 里常见 NaN）。"""
    if isinstance(obj, dict):
        return {k: san(v) for k, v in obj.items()}
    if isinstance(obj, float):
        return None if obj != obj else obj
    return obj


def upsert(db, code, period, value, yoy, extra):
    db.execute(UPSERT, {'c': code, 'd': period, 'v': fv(value), 'y': fv(yoy),
                        'e': _json.dumps(san(extra or {}), ensure_ascii=False, default=str)})


def month_date(m):
    m = str(m)[:6]
    return f'{m[:4]}-{m[4:6]}-01'


def main():
    ap = argparse.ArgumentParser(description='宏观指标历史回补')
    ap.add_argument('--start-m', default='199001', help='月度/季度回补起点（默认 199001）')
    ap.add_argument('--shibor', action='store_true', help='按日回补 SHIBOR 深历史（长跑）')
    ap.add_argument('--shibor-start', default='20061009', help='SHIBOR 回补起点（默认 20061009）')
    args = ap.parse_args()

    import tushare as ts
    from sqlalchemy import text as _t
    from app.config import settings
    from app.db.connection import get_sync_db
    from crawler.adapters.tushare_quota import QuotaExhausted, TushareQuota

    pro = ts.pro_api(settings.TUSHARE_TOKEN)
    quota = TushareQuota.get()
    db = get_sync_db()
    rows = 0
    calls = 0
    stop = ''

    def _q():
        nonlocal stop
        if quota.remaining() < 20:
            stop = '配额余量不足 20，收工；脚本幂等，明日续跑'
            return False
        quota.consume()
        return True

    try:
        if not args.shibor:
            # ── 月度/季度：一次调用拉全历史，字段口径对齐 dag_task_macro_daily ──
            jobs = [
                ('cpi', lambda: pro.cn_cpi(start_m=args.start_m, end_m=None), lambda r: upsert(
                    db, 'cpi', month_date(r['month']), r.get('nt_yoy'), r.get('nt_yoy'),
                    {k: r.get(k) for k in ('nt_val', 'nt_yoy', 'nt_mom', 'nt_accu')})),
                ('ppi', lambda: pro.cn_ppi(start_m=args.start_m, end_m=None), lambda r: upsert(
                    db, 'ppi', month_date(r['month']), r.get('ppi_yoy'), r.get('ppi_yoy'),
                    {k: r.get(k) for k in ('ppi_yoy', 'ppi_mp_yoy', 'ppi_mp_qm_yoy')})),
                ('m', lambda: pro.cn_m(start_m=args.start_m, end_m=None), lambda r: [
                    upsert(db, c, month_date(r['month']), r.get(f'{c}_yoy'), r.get(f'{c}_yoy'),
                           {c: r.get(c), f'{c}_yoy': r.get(f'{c}_yoy')})
                    for c in ('m0', 'm1', 'm2')]),
                ('gdp', lambda: pro.cn_gdp(start_q=f'{args.start_m[:4]}Q1', end_q=None), lambda r: upsert(
                    db, 'gdp', f"{str(r['quarter'])[:4]}-{int(str(r['quarter'])[-1]) * 3:02d}-01",
                    r.get('gdp_yoy'), r.get('gdp_yoy'),
                    {k: r.get(k) for k in ('gdp', 'gdp_yoy', 'pi', 'si', 'ti')})),
            ]
            for name, fetch, handle in jobs:
                if stop:
                    break
                if not _q():
                    break
                calls += 1
                try:
                    df = fetch()
                    n = 0
                    for _, r in (df.iterrows() if df is not None and len(df) else []):
                        handle(r)
                        n += 1
                    db.commit()
                    rows += n
                    logger.info(f'{name}: {n} 行（累计 {rows}，配额 {calls} 次）')
                except Exception as e:
                    db.rollback()
                    logger.warning(f'{name}: 失败 {str(e)[:80]}')
        else:
            # ── SHIBOR：按日循环（跳过已有日期与周末；节假日 0 行自然跳过）──
            existing = {str(r[0])[:10] for r in db.execute(_t(
                "SELECT period_date FROM macro_indicator WHERE indicator_code='shibor_on'")).fetchall()}
            logger.info(f'shibor_on 已有 {len(existing)} 天，从 {args.shibor_start} 补到今')
            cur = date.fromisoformat(args.shibor_start)
            today = date.today()
            while cur <= today:
                if stop:
                    break
                if cur.weekday() < 5 and cur.isoformat() not in existing:
                    if not _q():
                        break
                    calls += 1
                    try:
                        df = pro.shibor(date=cur.isoformat().replace('-', ''))
                        if df is not None and len(df):
                            r = df.iloc[0]
                            upsert(db, 'shibor_on', cur.isoformat(), r.get('on'), None,
                                   {k: r.get(k) for k in ('on', '1w', '2w', '1m', '3m', '6m', '9m', '1y')})
                            db.commit()
                            rows += 1
                            if rows % 200 == 0:
                                logger.info(f'shibor: {rows} 行（{cur}，配额 {calls} 次）')
                    except Exception as e:
                        db.rollback()
                        logger.warning(f'shibor {cur}: {str(e)[:60]}')
                cur += timedelta(days=1)
    finally:
        try:
            quota.persist(db)
        except Exception as e:
            logger.warning(f'配额持久化失败（不影响数据）: {e}')
        db.close()

    if stop:
        logger.warning(stop)
    logger.info(f'回补完成：{rows} 行 / {calls} 次 API 调用')
    return 1 if stop else 0


if __name__ == '__main__':
    sys.exit(main())
