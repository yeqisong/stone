"""资讯采集编排（DB 感知）——DAG 节点 news_crawl 的实体。

设计要点：
- **快讯主备**：cls → em724 → wscn，第一个成功即止。互备的用意是可用性，不是叠加——
  三源同时全收只会把同一事件的三份近重复内容灌进库并把 LLM 成本翻三倍。
- **关注池分片轮转**：每轮取 last_fetched_at 最老的 focus_shard 只，出池周期 ≈
  (池大小 / shard) × 轮询间隔，把请求速率压在东财风控线（5/s、200/min）以内。
  个股新闻/研报/公告都**只采池内**，不采全市场 5000 只（价值密度低、请求量不可控）。
- **行业研报单独节流**：它全市场共享，按股票轮转查会把同一份研报查 N 次，
  故用独立的 news_crawl_state 行按小时节流。
- **单步失败不杀整轮**：各源独立记 fail_streak，并由 attempts/errors 计数交给节点
  按失败率判 failed（「全夜 100% 失败却报 success」的静默故障教训）。
- **幂等**：content_hash 前置过滤 + UNIQUE(source, source_id) + 轮转状态推进，
  故轮次密集不会重复、机器关机漏轮次由下一轮自愈。
"""
import datetime
import json

from loguru import logger
from sqlalchemy import text

from crawler.news import match as nmatch
from crawler.news import sources as nsrc
from crawler.writers import (batch_upsert_news, batch_upsert_news_stock,
                             news_existing_keys, news_hash_sources)

DEFAULT_CFG = {
    'flash_enabled': True,
    'focus_shard': 20,
    'em_min_interval': 1.2,
    'max_pages': 4,
    'report_enabled': True,
    'notice_enabled': True,
    'fetch_article_body': True,
    'body_max_chars': 6000,
    'report_lookback_days': 180,
    'industry_report_lookback_days': 7,
}
INDUSTRY_REPORT_INTERVAL_H = 6    # 行业研报节流
ORGID_REFRESH_DAYS = 7            # 巨潮 orgId 映射重拉周期


def load_cfg(db) -> dict:
    """读 strategy_config.news_collect（params 是 JSONB，驱动可能返回 dict 或 str）。"""
    row = db.execute(text(
        "SELECT params FROM strategy_config WHERE strategy_name='news_collect'"
    )).fetchone()
    cfg = dict(DEFAULT_CFG)
    if row and row[0]:
        try:
            cfg.update(json.loads(row[0]) if isinstance(row[0], str) else (row[0] or {}))
        except Exception as e:
            logger.warning(f'[news] news_collect 参数解析失败，用默认值: {e}')
    nsrc.set_em_interval(cfg.get('em_min_interval', 1.2))
    return cfg


# ── 状态表读写 ──────────────────────────────────────────────────

def state_get(db, source: str) -> dict:
    row = db.execute(text(
        "SELECT cursor, last_published_at, last_fetched_at, last_ok, fail_streak, note "
        "FROM news_crawl_state WHERE source=:s"), {'s': source}).fetchone()
    if not row:
        return {'cursor': '', 'last_published_at': None, 'last_fetched_at': None,
                'last_ok': True, 'fail_streak': 0, 'note': ''}
    return {'cursor': row[0] or '', 'last_published_at': row[1], 'last_fetched_at': row[2],
            'last_ok': row[3], 'fail_streak': row[4] or 0, 'note': row[5] or ''}


def state_age_hours(db, source: str):
    """距上次成功取数的小时数（未取过返回 None）。

    TIMESTAMPTZ 列 psycopg2 返回 tz-aware，与 bj_now() 相减在任何库时区下都正确。
    """
    st = state_get(db, source)
    if not st['last_fetched_at']:
        return None
    ref = st['last_fetched_at']
    if ref.tzinfo is None:                    # 兜底：无时区时按北京时间理解
        ref = ref.replace(tzinfo=nsrc._TZ)
    return (nsrc.bj_now() - ref).total_seconds() / 3600


def state_set(db, source: str, *, cursor=None, published_at=None, ok=True,
              fail_streak=0, note='', touch_fetched=True) -> None:
    db.execute(text("""
        INSERT INTO news_crawl_state (source, cursor, last_published_at, last_fetched_at,
                                      last_ok, fail_streak, note, updated_at)
        VALUES (:s, :c, :p, CASE WHEN :touch THEN CURRENT_TIMESTAMP ELSE NULL END,
                :ok, :fs, :n, CURRENT_TIMESTAMP)
        ON CONFLICT (source) DO UPDATE SET
            cursor = COALESCE(EXCLUDED.cursor, news_crawl_state.cursor),
            last_published_at = COALESCE(EXCLUDED.last_published_at,
                                         news_crawl_state.last_published_at),
            last_fetched_at = CASE WHEN :touch THEN CURRENT_TIMESTAMP
                                   ELSE news_crawl_state.last_fetched_at END,
            last_ok = EXCLUDED.last_ok,
            fail_streak = EXCLUDED.fail_streak,
            note = EXCLUDED.note,
            updated_at = CURRENT_TIMESTAMP
    """), {'s': source, 'c': cursor, 'p': published_at, 'ok': ok,
           'fs': fail_streak, 'n': note[:200], 'touch': touch_fetched})
    db.commit()


# ── 关注池 ──────────────────────────────────────────────────────

def load_focus_pool(db) -> tuple:
    """返回 (codes, name_index)：codes 用于分片轮转与匹配范围，name_index 供长名优先扫描。

    名称取自 stock_master 且限定 stock_type='stock'——关注池只存 6 位代码，
    不 join 就无法排除指数/基金混入（000001 既是平安银行又是上证指数）。
    """
    rows = db.execute(text("""
        SELECT m.stock_code, m.stock_name
        FROM v_focus_pool f
        JOIN stock_master m ON m.stock_code = f.stock_code AND m.stock_type = 'stock'
        ORDER BY m.stock_code
    """)).fetchall()
    codes = [r[0] for r in rows]
    return codes, nmatch.build_name_index(rows)


def ensure_poll_rows(db) -> int:
    """为关注池成员播种 poll_state 行（否则新加入自选的股票永远排不上轮转）。

    必须在 refresh_orgid_map 之前调用：orgId 回填是按 stock_code UPDATE 已有行，
    池行不存在时 UPDATE 命中 0 行，会让公告采集整轮拿到空 orgId（已踩过）。
    """
    res = db.execute(text("""
        INSERT INTO news_poll_state (stock_code)
        SELECT stock_code FROM v_focus_pool
        ON CONFLICT (stock_code) DO NOTHING
    """))
    db.commit()
    return max(res.rowcount or 0, 0)


def pick_shard(db, shard: int) -> list:
    """取「最久未查」的 N 只（新进池的 NULLS FIRST 优先）。"""
    rows = db.execute(text("""
        SELECT stock_code FROM news_poll_state
        ORDER BY last_fetched_at NULLS FIRST, stock_code
        LIMIT :n
    """), {'n': max(1, int(shard))}).fetchall()
    return [r[0] for r in rows]


def poll_mark(db, code: str, *, ok: bool, latest=None) -> None:
    db.execute(text("""
        UPDATE news_poll_state SET
            last_fetched_at = CURRENT_TIMESTAMP,
            last_news_at = COALESCE(:latest, last_news_at),
            fetched_count = fetched_count + 1,
            fail_count = fail_count + CASE WHEN :ok THEN 0 ELSE 1 END,
            updated_at = CURRENT_TIMESTAMP
        WHERE stock_code = :c
    """), {'c': code, 'ok': ok, 'latest': latest})
    db.commit()


# ── 入库 ────────────────────────────────────────────────────────

def _valid_codes(db, codes) -> set:
    """源自带股票标注的合法性校验（限定 stock_type='stock'，剔除板块/指数/内部码）。"""
    codes = sorted({c for c in codes if c})
    if not codes:
        return set()
    rows = db.execute(text("""
        SELECT stock_code FROM stock_master
        WHERE stock_code = ANY(:cs) AND stock_type = 'stock'
    """), {'cs': codes}).fetchall()
    return {r[0] for r in rows}


def _existing_ids(db, source: str, ids) -> set:
    """该源下已入库的 source_id 集合（用于跳过重复抓正文页）。"""
    ids = sorted({str(i) for i in (ids or []) if i})
    if not ids:
        return set()
    out = set()
    for i in range(0, len(ids), 1000):
        rows = db.execute(text(
            "SELECT source_id FROM news_item WHERE source=:s AND source_id = ANY(:ids)"
        ), {'s': source, 'ids': ids[i:i + 1000]}).fetchall()
        out.update(str(r[0]) for r in rows)
    return out


def flush(db, items: list, name_index: list, stats: dict) -> int:
    """一批原文入库 + 建个股关联。返回新增条数。

    关联优先级：源自带股票标注（rel='src'，可信）优先；该条没有源标注时才做
    关注池名称匹配（rel='match'），避免同一条同时挂两份关联。
    """
    if not items:
        return 0
    # 批内去重（ON CONFLICT DO UPDATE 不能在一批里命中同一行两次 → Postgres 报错）
    uniq, seen_ids, seen_hash = [], set(), set()
    for it in items:
        key = (it['source'], str(it['source_id']))
        if key in seen_ids:
            continue
        h = nsrc.content_hash(it.get('title'), it.get('content'))
        if (it['source'], h) in seen_hash:
            continue
        it['content_hash'] = h
        seen_ids.add(key)
        seen_hash.add((it['source'], h))
        uniq.append(it)
    # 库内去重分两层，顺序不能反：
    #  ① 主幂等按唯一键 (source, source_id) —— 同源重取必然命中，且正文改写过也不受影响
    #  ② 跨源去重按 content_hash（仅当「别的源」已收录同一内容）—— 主备切换窗口的重叠
    keys = news_existing_keys(db, [(it['source'], str(it['source_id'])) for it in uniq])
    new = [it for it in uniq if (it['source'], str(it['source_id'])) not in keys]
    stats['already'] = stats.get('already', 0) + (len(uniq) - len(new))
    if not new:
        return 0
    hs = news_hash_sources(db, [it['content_hash'] for it in new])
    fresh = [it for it in new
             if not (hs.get(it['content_hash'], set()) - {it['source']})]
    stats['dup_skipped'] = stats.get('dup_skipped', 0) + (len(new) - len(fresh))
    if not fresh:
        return 0

    mapping = batch_upsert_news(db, fresh)
    valid = _valid_codes(db, {c for it in fresh for c in (it.get('stock_codes') or [])})

    rel_rows, n_match = [], 0
    for it in fresh:
        nid = mapping.get((it['source'], str(it['source_id'])))
        if not nid:
            continue
        codes = {c: 'src' for c in (it.get('stock_codes') or []) if c in valid}
        if not codes and name_index:
            blob = f"{it.get('title') or ''} {it.get('content') or ''}"
            for c in nmatch.extract_matches(blob, name_index):
                codes.setdefault(c, 'match')
        for c, rel in codes.items():
            rel_rows.append({'news_id': nid, 'stock_code': c, 'rel': rel,
                             'confidence': 1.0 if rel == 'src' else 0.8})
        n_match += sum(1 for r in codes.values() if r == 'match')
    if rel_rows:
        batch_upsert_news_stock(db, rel_rows)
    stats['linked'] = stats.get('linked', 0) + len(rel_rows)
    stats['matched'] = stats.get('matched', 0) + n_match
    return len(fresh)


# ── 快讯（主备）─────────────────────────────────────────────────

def collect_flash(db, cfg: dict, stats: dict, name_index: list) -> str:
    """按主备顺序取快讯，第一个成功即止。返回实际使用的源名（全失败返回 ''）。"""
    errs = []
    for name, fn, label in nsrc.FLASH_SOURCES:
        st = state_get(db, name)
        stats['attempts'] = stats.get('attempts', 0) + 1
        try:
            items, cursor, note = fn(st['cursor'], int(cfg.get('max_pages', 4)))
        except Exception as e:
            streak = (st['fail_streak'] or 0) + 1
            errs.append(f'{name}:{type(e).__name__}')
            stats['errors'] = stats.get('errors', 0) + 1
            state_set(db, name, ok=False, fail_streak=streak,
                      note=f'{type(e).__name__}: {e}', touch_fetched=False)
            logger.warning(f'[news] 快讯源 {label} 失败（连续 {streak} 轮）: {e}')
            continue
        n = flush(db, items, name_index, stats)
        latest = max((it['published_at'] for it in items), default=None)
        state_set(db, name, cursor=cursor, published_at=latest, ok=True, fail_streak=0,
                  note=f'{n} 新增/{len(items)} 取回 {note}'.strip())
        stats['flash_source'] = name
        stats['flash'] = stats.get('flash', 0) + n
        if name != 'cls':
            stats['flash_fallback'] = name
        return name
    stats['flash_errors'] = errs
    return ''


# ── 关注池分片 ──────────────────────────────────────────────────

def collect_pool(db, cfg: dict, stats: dict, codes: list, name_index: list,
                 progress_cb=None) -> None:
    """个股新闻 + 研报 + 公告，按关注池分片轮转。"""
    if not codes:
        stats['pool_note'] = '关注池为空（持仓与自选分组均无标的）'
        return
    shard = pick_shard(db, int(cfg.get('focus_shard', 20)))
    lookback = nsrc.bj_now() - datetime.timedelta(days=int(cfg.get('report_lookback_days', 180)))
    if progress_cb:
        progress_cb(0, len(shard), f'关注池分片 {len(shard)}/{len(codes)} 只')
    for i, code in enumerate(shard):
        acc, latest, ok = [], None, True
        # 个股新闻（+ 正文页）
        stats['attempts'] = stats.get('attempts', 0) + 1
        try:
            news = nsrc.fetch_em_stock_news(code)
            if news and cfg.get('fetch_article_body'):
                # 只为新条目抓正文页：该接口每轮都返回最新 20 条（多数已入库），
                # 不筛就会把 20 个正文页每轮重复拉一遍（每只每天上千次无效请求）
                known = _existing_ids(db, 'em_stock', [x['source_id'] for x in news])
                for it in news:
                    if it['source_id'] in known:
                        continue
                    body = nsrc.fetch_article_body(
                        it['url'], int(cfg.get('body_max_chars', 6000)))
                    if body:
                        it['content'] = body
                        it['extra']['body_chars'] = len(body)
            acc.extend(news)
            if news:
                latest = max(x['published_at'] for x in news)
        except Exception as e:
            ok = False
            stats['errors'] = stats.get('errors', 0) + 1
            stats['stock_news_fail'] = stats.get('stock_news_fail', 0) + 1
            logger.warning(f'[news] 个股新闻 {code} 失败: {e}')
        # 个股研报
        if cfg.get('report_enabled'):
            stats['attempts'] = stats.get('attempts', 0) + 1
            try:
                acc.extend(nsrc.fetch_em_reports(code, '0', 1, 20,
                                                 begin=lookback.strftime('%Y-%m-%d')))
            except Exception as e:
                ok = False
                stats['errors'] = stats.get('errors', 0) + 1
                stats['report_fail'] = stats.get('report_fail', 0) + 1
                logger.warning(f'[news] 研报 {code} 失败: {e}')
        # 公告（orgId 必须来自官方映射；缺失时 fetch_cninfo_notices 主动报错，不静默漏采）
        if cfg.get('notice_enabled'):
            stats['attempts'] = stats.get('attempts', 0) + 1
            try:
                org = db.execute(text(
                    "SELECT org_id FROM news_poll_state WHERE stock_code=:c"
                ), {'c': code}).scalar()
                acc.extend(nsrc.fetch_cninfo_notices(code, org))
            except Exception as e:
                ok = False
                stats['errors'] = stats.get('errors', 0) + 1
                stats['notice_fail'] = stats.get('notice_fail', 0) + 1
                logger.warning(f'[news] 公告 {code} 失败: {e}')

        stats['pool'] = stats.get('pool', 0) + flush(db, acc, name_index, stats)
        poll_mark(db, code, ok=ok, latest=latest)
        if progress_cb:
            progress_cb(i + 1, len(shard),
                        f'关注池 {i + 1}/{len(shard)} 只 · 新增 {stats.get("pool", 0)}')
    state_set(db, 'em_stock', ok=True, fail_streak=0, note=f'分片 {len(shard)}/{len(codes)} 只')


def collect_industry_reports(db, cfg: dict, stats: dict, name_index: list) -> None:
    """行业研报（全市场共享）：独立小时级节流，不随股票分片轮转。"""
    if not cfg.get('report_enabled'):
        return
    age = state_age_hours(db, 'em_report_industry')
    if age is not None and age < INDUSTRY_REPORT_INTERVAL_H:
        return
    st = state_get(db, 'em_report_industry')
    stats['attempts'] = stats.get('attempts', 0) + 1
    try:
        begin = (nsrc.bj_now() - datetime.timedelta(
            days=int(cfg.get('industry_report_lookback_days', 7)))).strftime('%Y-%m-%d')
        items = nsrc.fetch_em_reports('', '1', 2, 50, begin=begin)
        n = flush(db, items, name_index, stats)
        stats['industry_report'] = n
        state_set(db, 'em_report_industry', ok=True, fail_streak=0, note=f'{n} 新增')
    except Exception as e:
        stats['errors'] = stats.get('errors', 0) + 1
        stats['industry_report_fail'] = 1
        state_set(db, 'em_report_industry', ok=False,
                  fail_streak=(st['fail_streak'] or 0) + 1, note=str(e)[:150],
                  touch_fetched=False)
        logger.warning(f'[news] 行业研报失败: {e}')


def refresh_orgid_map(db, stats: dict = None) -> int:
    """巨潮 orgId 映射：>7 天重拉一次并回填 news_poll_state.org_id。

    orgId 非统一格式（601318→9900002221 / 601398→jjxt0000019 / 688017→9900041602），
    硬编码会让 601xxx 段大量返回 totalAnnouncement=0 而看起来像「该股没公告」。
    """
    age_d = state_age_hours(db, 'cninfo_orgid')
    stale = age_d is None or age_d / 24 > ORGID_REFRESH_DAYS
    # 除了「映射陈旧」，只要还有池内股票没有 orgId 也必须重拉：时间闸门只挡住重复下载，
    # 不能挡住回填——否则新加入自选的股票要等 7 天才有 orgId（表现为公告静默漏采）。
    missing = db.execute(text(
        "SELECT 1 FROM news_poll_state WHERE org_id IS NULL LIMIT 1")).fetchone()
    if not stale and not missing:
        return 0
    st = state_get(db, 'cninfo_orgid')
    if stats is not None:
        stats['attempts'] = stats.get('attempts', 0) + 1
    try:
        m = nsrc.fetch_cninfo_orgid_map()
        rows = [{'c': c, 'o': o} for c, o in m.items()]
        for i in range(0, len(rows), 1000):
            db.execute(text("""
                UPDATE news_poll_state SET org_id = :o
                WHERE stock_code = :c AND (org_id IS NULL OR org_id <> :o)
            """), rows[i:i + 1000])
        db.commit()
        state_set(db, 'cninfo_orgid', ok=True, fail_streak=0, note=f'{len(m)} 条映射')
        return len(m)
    except Exception as e:
        if stats is not None:
            stats['errors'] = stats.get('errors', 0) + 1
        state_set(db, 'cninfo_orgid', ok=False,
                  fail_streak=(st['fail_streak'] or 0) + 1, note=str(e)[:150],
                  touch_fetched=False)
        logger.warning(f'[news] 巨潮 orgId 映射刷新失败: {e}')
        return 0


# ── 对外入口 ────────────────────────────────────────────────────

def collect_once(db, cfg: dict = None, progress_cb=None) -> dict:
    """一轮采集。返回统计 dict（供 DAG 节点写日志/进度/判失败）。"""
    cfg = cfg or load_cfg(db)
    stats = {'flash': 0, 'pool': 0, 'linked': 0, 'matched': 0, 'dup_skipped': 0,
             'attempts': 0, 'errors': 0}
    codes, name_index = load_focus_pool(db)
    stats['focus_n'] = len(codes)
    ensure_poll_rows(db)                       # 必须在 refresh_orgid_map 之前
    refresh_orgid_map(db, stats)
    if cfg.get('flash_enabled', True):
        collect_flash(db, cfg, stats, name_index)
    else:
        stats['flash_skipped'] = True
    collect_industry_reports(db, cfg, stats, name_index)
    collect_pool(db, cfg, stats, codes, name_index, progress_cb=progress_cb)
    stats['inserted'] = (stats['flash'] + stats['pool'] + stats.get('industry_report', 0))
    return stats
