"""新闻资讯 API（design/07）——列表 / 详情 / 源健康。

三层数据在 v_news_feed 视图里已合好（原文 LEFT JOIN 总结），故这里只做筛选与分页。
未总结的行 summary/importance/sentiment 为 NULL：前端据此显示「待总结」，
不要用 0 代替（0 是「中性」的合法值，混用会让「未总结」被当成「中性」）。
"""
from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import text

from app.db.connection import get_sync_db

router = APIRouter(tags=["news"])

CATEGORIES = ('flash', 'stock_news', 'report', 'notice')


@router.get("/news")
def list_news(
    category: str = Query('', description='flash/stock_news/report/notice，空=全部'),
    source: str = Query('', description='cls/em724/wscn/em_stock/em_report/cninfo'),
    code: str = Query('', description='个股代码（仅看关联到该股的资讯）'),
    min_importance: int = Query(0, ge=0, le=3, description='最低重要度，0=不过滤（未总结行不受此过滤）'),
    only_focus: bool = Query(False, description='只看关注池（持仓∪自选）相关'),
    since_hours: int = Query(0, ge=0, le=24 * 30, description='只看近 N 小时，0=不限'),
    keyword: str = Query('', description='标题/摘要关键词'),
    sort: str = Query('time', description='time=按发布时间 / importance=按重要度'),
    page: int = Query(1, ge=1),
    page_size: int = Query(30, ge=1, le=200),
):
    """资讯列表（分页）。默认按发布时间倒序，未总结条目不隐藏（可见采集进度）。"""
    where, params = ["1=1"], {}
    if category:
        if category not in CATEGORIES:
            raise HTTPException(400, f"category 需为 {CATEGORIES} 之一")
        where.append("category = :cat")
        params['cat'] = category
    if source:
        where.append("source = :src")
        params['src'] = source
    if code:
        where.append("""EXISTS (SELECT 1 FROM news_stock ns
                                WHERE ns.news_id = v_news_feed.id AND ns.stock_code = :code)""")
        params['code'] = code
    if min_importance:
        # 未总结行（importance IS NULL）一并保留：按重要度过滤时它们的等级尚不可知，
        # 直接排除会与「刚采集完还没轮询到总结」的窗口叠加，表现为列表空空而库里明明有数据
        # ——正是本项目静默丢数据的老毛病。前端用「待总结」徽标与之区分。
        where.append("(importance >= :imp OR importance IS NULL)")
        params['imp'] = min_importance
    if only_focus:
        where.append("""EXISTS (SELECT 1 FROM news_stock ns JOIN v_focus_pool f
                                ON f.stock_code = ns.stock_code
                                WHERE ns.news_id = v_news_feed.id)""")
    if since_hours:
        where.append("published_at >= now() - make_interval(hours => :hrs)")
        params['hrs'] = since_hours
    if keyword:
        where.append("(title ILIKE :kw OR summary ILIKE :kw)")
        params['kw'] = f'%{keyword}%'
    w = " AND ".join(where)
    order = ("importance DESC NULLS LAST, published_at DESC" if sort == 'importance'
             else "published_at DESC")

    db = get_sync_db()
    try:
        total = db.execute(text(f"SELECT count(*) FROM v_news_feed WHERE {w}"), params).scalar()
        rows = db.execute(text(f"""
            SELECT id, source, category, published_at, title, summary, importance, topics,
                   sentiment, left(content, 400) AS content_preview, url,
                   (content IS NOT NULL) AS has_content,
                   EXISTS (SELECT 1 FROM news_stock ns JOIN v_focus_pool f
                           ON f.stock_code = ns.stock_code
                           WHERE ns.news_id = v_news_feed.id) AS focus_linked
            FROM v_news_feed WHERE {w}
            ORDER BY {order}
            LIMIT :lim OFFSET :off
        """), {**params, 'lim': page_size, 'off': (page - 1) * page_size}).fetchall()
        ids = [r[0] for r in rows]
        stocks = {}
        if ids:
            for nid, sc, sn, rel in db.execute(text("""
                SELECT ns.news_id, ns.stock_code, m.stock_name, ns.rel
                FROM news_stock ns LEFT JOIN stock_master m
                     ON m.stock_code = ns.stock_code AND m.stock_type = 'stock'
                WHERE ns.news_id = ANY(:ids)
            """), {'ids': ids}).fetchall():
                stocks.setdefault(nid, []).append(
                    {'code': sc, 'name': sn or sc, 'rel': rel})
        return {
            'total': total, 'page': page, 'page_size': page_size,
            'data': [{
                'id': r[0], 'source': r[1], 'category': r[2],
                'published_at': r[3].strftime('%Y-%m-%d %H:%M:%S') if r[3] else None,
                'title': r[4], 'summary': r[5], 'importance': r[6],
                'topics': [t for t in (r[7] or '').split(',') if t],
                'sentiment': r[8], 'content_preview': r[9], 'url': r[10],
                'has_content': r[11], 'focus_linked': r[12],
                'stocks': stocks.get(r[0], []),
            } for r in rows],
        }
    finally:
        db.close()


@router.get("/news/health")
def news_health():
    """各源采集健康（状态页卡片数据源）。

    三态判定按「最后一次成功取数距今」：快讯 3 个源按 20 分钟轮询节奏，>2 轮未更新即
    degraded、>6 轮即 down；关注池分片因每轮只取一小片，按 2 小时/6 小时放宽。
    """
    db = get_sync_db()
    try:
        rows = db.execute(text("""
            SELECT source, cursor, last_published_at, last_fetched_at, last_ok,
                   fail_streak, note
            FROM news_crawl_state ORDER BY source
        """)).fetchall()
        now = db.execute(text("SELECT now()")).scalar()
        sources, worst = [], 'ok'
        rank = {'ok': 0, 'degraded': 1, 'down': 2}
        for r in rows:
            src, _, last_pub, last_fetch, ok, streak, note = r
            if last_fetch is None:
                state = 'down' if src in ('cls', 'em724', 'wscn') else 'ok'
            else:
                age_min = (now - last_fetch).total_seconds() / 60
                if src == 'em_stock':
                    state = 'ok' if age_min <= 120 else ('degraded' if age_min <= 360 else 'down')
                elif src in ('em_report_industry', 'cninfo_orgid'):
                    state = 'ok' if age_min <= 720 else ('degraded' if age_min <= 1440 else 'down')
                else:
                    state = 'ok' if age_min <= 40 else ('degraded' if age_min <= 120 else 'down')
            if not ok:
                state = 'degraded' if state == 'ok' else state
            sources.append({
                'source': src, 'state': state, 'last_ok': ok, 'fail_streak': streak or 0,
                'last_fetched_at': last_fetch.strftime('%Y-%m-%d %H:%M:%S') if last_fetch else None,
                'last_published_at': last_pub.strftime('%Y-%m-%d %H:%M:%S') if last_pub else None,
                'age_min': int((now - last_fetch).total_seconds() / 60) if last_fetch else None,
                'note': note or '',
            })
            if rank[state] > rank[worst]:
                worst = state
        stats = db.execute(text("""
            SELECT count(*),
                   count(*) FILTER (WHERE published_at >= now() - interval '24 hours'),
                   count(*) FILTER (WHERE published_at >= now() - interval '24 hours'
                                      AND category = 'flash')
            FROM news_item
        """)).fetchone()
        summ = db.execute(text("""
            SELECT count(*), count(*) FILTER (WHERE created_at >= now() - interval '24 hours')
            FROM news_summary
        """)).fetchone()
        pending = db.execute(text("""
            SELECT count(*) FROM news_item n
            LEFT JOIN news_summary s ON s.news_id = n.id
            WHERE s.news_id IS NULL AND n.content IS NOT NULL AND length(n.content) >= 20
        """)).scalar()
        return {
            'status': worst,
            'sources': sources,
            'total': stats[0], 'today': stats[1], 'today_flash': stats[2],
            'summarized': summ[0], 'summarized_today': summ[1], 'pending_summary': pending,
            'focus_n': db.execute(text("SELECT count(*) FROM v_focus_pool")).scalar(),
        }
    finally:
        db.close()


@router.get("/news/{news_id}")
def news_detail(news_id: int):
    """单条资讯全文 + 总结 + 关联个股。"""
    db = get_sync_db()
    try:
        r = db.execute(text("""
            SELECT id, source, category, published_at, title, content, summary, importance,
                   topics, sentiment, url, extra, summary_model, prompt_ver
            FROM v_news_feed WHERE id = :i
        """), {'i': news_id}).fetchone()
        if not r:
            raise HTTPException(404, f'资讯 {news_id} 不存在')
        stocks = db.execute(text("""
            SELECT ns.stock_code, m.stock_name, ns.rel
            FROM news_stock ns LEFT JOIN stock_master m
                 ON m.stock_code = ns.stock_code AND m.stock_type = 'stock'
            WHERE ns.news_id = :i ORDER BY ns.rel, ns.stock_code
        """), {'i': news_id}).fetchall()
        return {
            'id': r[0], 'source': r[1], 'category': r[2],
            'published_at': r[3].strftime('%Y-%m-%d %H:%M:%S') if r[3] else None,
            'title': r[4], 'content': r[5], 'summary': r[6], 'importance': r[7],
            'topics': [t for t in (r[8] or '').split(',') if t], 'sentiment': r[9],
            'url': r[10], 'extra': r[11],
            'summary_model': r[12], 'prompt_ver': r[13],
            'stocks': [{'code': s[0], 'name': s[1] or s[0], 'rel': s[2]} for s in stocks],
        }
    finally:
        db.close()
