"""雪球情绪轮询器入口（design/06，P1 试点）。

用法：
    venv/bin/python -m scripts.xueqiu_poller --init-pool   # 一次性建 P1 试点池（100 只冷热混合）
    venv/bin/python -m scripts.xueqiu_poller               # 常驻采集（Ctrl-C / kill 停）
    XUEQIU_TOKENS='xq_a_token=...;u=...' venv/bin/python -m scripts.xueqiu_poller   # 登录 token 模式（形态 C）

试点池：最新交易日成交额 top 70（tier=1 热）+ 成交额排名 2000-2029（tier=3 冷）。
"""
import sys

from loguru import logger


def init_pool(db):
    """建/补 P1 试点池（幂等：已存在的 code 不动）。"""
    from sqlalchemy import text
    rows = db.execute(text("""
        WITH latest AS (SELECT MAX(trade_date) AS d FROM daily_quote),
        ranked AS (
            SELECT stock_code, amount, ROW_NUMBER() OVER (ORDER BY amount DESC) AS rn
            FROM daily_quote WHERE trade_date = (SELECT d FROM latest)
        )
        SELECT stock_code, CASE WHEN rn <= 70 THEN 1 ELSE 3 END AS tier FROM ranked
        WHERE rn <= 70 OR (rn BETWEEN 2000 AND 2029)
    """)).fetchall()
    if not rows:
        logger.error('daily_quote 无数据，无法建池——先跑行情采集')
        sys.exit(1)
    for code, tier in rows:
        db.execute(text(
            "INSERT INTO xueqiu_crawl_state (code, tier) VALUES (:c, :t) "
            "ON CONFLICT (code) DO NOTHING"),
            {'c': code, 't': tier})
    db.commit()
    logger.info(f'雪球 P1 试点池就绪：{len(rows)} 只（tier1 {sum(1 for _, t in rows if t == 1)} / '
                f'tier3 {sum(1 for _, t in rows if t == 3)}）')


def main():
    from app.db.connection import get_sync_db
    from crawler.xueqiu.poller import Poller

    db = get_sync_db()
    if '--init-pool' in sys.argv:
        init_pool(db)
        return

    n = db.execute(__import__('sqlalchemy').text(
        'SELECT COUNT(*) FROM xueqiu_crawl_state')).scalar()
    if not n:
        logger.info('xueqiu_crawl_state 为空，自动建试点池')
        init_pool(db)

    poller = Poller(db)
    poller.load_states()
    try:
        poller.run()
    except KeyboardInterrupt:
        logger.info('收到中断，收尾 flush')
        poller._flush(force=True)
        poller._persist()


if __name__ == '__main__':
    main()
