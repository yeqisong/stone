"""可转债历史/增量同步 CLI。

用法：
    python -m scripts.cb_sync --start 2019-01-01 --end 2026-11-20
    python -m scripts.cb_sync --skip-basic --skip-share --max-days 60

- 默认刷新 cb_basic（1 次调用）→ 回补区间缺失日行情 → 同步当日转股/余额公告。
- --share-history 逐债回补全部转股公告历史（1181 次调用）。
- 已入库日期自动跳过（断点续跑）；配额余量低于 reserve 时优雅停止。
"""
import argparse

from loguru import logger

from crawler.cb import sync_basic, sync_range, sync_share, sync_share_history


def main():
    ap = argparse.ArgumentParser(description='可转债数据同步')
    ap.add_argument('--start', default='2019-01-01', help='回补起始日（默认 2019-01-01，Tushare cb_daily 最早可用）')
    ap.add_argument('--end', default=None, help='回补截止日（默认今天）')
    ap.add_argument('--skip-basic', action='store_true', help='跳过 cb_basic 刷新')
    ap.add_argument('--skip-share', action='store_true', help='跳过收尾的当日 cb_share 公告')
    ap.add_argument('--share-history', action='store_true', help='逐债回补全部转股比例/剩余规模历史（约 1181 次调用）')
    ap.add_argument('--max-days', type=int, default=None, help='本次最多补几个交易日')
    ap.add_argument('--reserve', type=int, default=200, help='tushare 配额保留阈值')
    args = ap.parse_args()

    if not args.skip_basic:
        n = sync_basic()
        logger.info(f'cb_basic 完成 {n} 条')

    def _progress(done, total, td, rows):
        if done and done % 25 == 0:
            logger.info(f'回补进度 {done}/{total}（最新 {td}，入库 {rows} 行）')

    r = sync_range(args.start, args.end, max_days=args.max_days,
                   reserve=args.reserve, progress_cb=_progress)
    logger.info(
        f'cb_daily 完成：新补 {r["done"]} 天 / 已存在 {r["skipped"]} 天 / 入库 {r["rows"]} 行 / '
        f'中断={r["halted"]}（{r["halt_reason"] or "-"}）')

    if args.share_history:
        def _share_progress(done, total, code, rows):
            if done and done % 25 == 0:
                logger.info(f'转股历史回补 {done}/{total}（{code}，入库 {rows} 行）')
        sr = sync_share_history(reserve=args.reserve, progress_cb=_share_progress)
        logger.info(
            f'cb_share 历史完成：{sr["done"]}/{sr["total"]} 只 / 入库 {sr["rows"]} 行 / '
            f'中断={sr["halted"]}（{sr["halt_reason"] or "-"}）')

    if not args.skip_share:
        n = sync_share(trade_date=args.end or None)
        logger.info(f'cb_share 当日公告 {n} 条')


if __name__ == '__main__':
    main()
