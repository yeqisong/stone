"""资讯源采集（design/07 第 1 层「原文」）。

四类源全部是公开 JSON 接口、零 key、无 WAF（故不需要雪球那套常驻洗白）：

  category      source      端点                              增量方式
  flash         cls         财联社电报（本地签名）             last_time 向旧翻页
  flash(备)     em724       东财全球 7×24                     **不翻页**，pageSize=200 取最新
  flash(备)     wscn        华尔街见闻 7×24                    cursor 向旧翻页
  stock_news    em_stock    东财个股新闻（JSONP）             关注池分片轮转，表内去重
  report        em_report   东财研报（qType 0 个股 / 1 行业）   同上
  notice        cninfo      巨潮公告（需 orgId）               同上

实测结论（2026-09-29 探针，勿凭文档假设）：
- 财联社：last_time=上一页最旧 ctime + refresh_type=1 → 向旧；rt=0 恒返最新页（不翻页）。
- 东财 7×24：sortEnd 试过毫秒/秒时间戳、压缩格式、下划线等六种写法均无法向旧翻页（返回同页或空），
  故放弃翻页，改用 pageSize=200 单请求（实测覆盖约 4 小时，远大于 20 分钟轮询间隔）。
- 见闻：cursor=下一页 attrs['next_cursor'] → 向旧，语义干净。
- 东财个股新闻的 content 只是 ~122 字源摘要，非正文；正文需另开 url 页（<div id="ContentBody">）。
- 财联社自带 level(A/B/C) 与 stock_list（17/50 条带股票代码）、东财 7×24 自带 stockList
  （混个股与板块，需过滤）、见闻 score 1/2/3 —— 都是免费的重要度先验与显式个股标注，
  入 extra 并作为 LLM 的提示上下文（不做预筛：先观测真实数据再决定是否省调用）。

东财风控：实测 5/s、单 IP 并发 ≥10、200/min → 临时封 IP。所有 eastmoney.com 请求必须走 em_get()。
"""
import datetime
import hashlib
import json
import random
import re
import threading
import time
import uuid

from curl_cffi import requests as cffi_requests
from loguru import logger

UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/122.0 Safari/537.36')

_TZ = datetime.timezone(datetime.timedelta(hours=8))  # 北京时间（全模块统一口径）

# 东财串行限流参数（可被 strategy_config.news_collect.em_min_interval 覆盖）
EM_MIN_INTERVAL = 1.2
EM_JITTER = 0.3
_EM_LOCK = threading.Lock()
_EM_LAST = [0.0]
_SESSION = cffi_requests.Session(impersonate='chrome')


def bj_now() -> datetime.datetime:
    """当前北京时间（tz-aware）。"""
    return datetime.datetime.now(_TZ)


def set_em_interval(seconds: float) -> None:
    """由采集编排层按 strategy_config 下发东财请求最小间隔。"""
    global EM_MIN_INTERVAL
    EM_MIN_INTERVAL = max(0.2, float(seconds))


# ── 时间与文本工具 ────────────────────────────────────────────────

def ts_to_dt(ts) -> datetime.datetime | None:
    """unix 秒 → 北京时间 tz-aware（财联社 ctime / 见闻 display_time）。"""
    try:
        return datetime.datetime.fromtimestamp(int(ts), _TZ)
    except (TypeError, ValueError, OSError, OverflowError):
        return None


def ms_to_dt(ts) -> datetime.datetime | None:
    """unix 毫秒 → 北京时间（巨潮 announcementTime）。

    必须显式 +08：用 fromtimestamp 的本地时区会在非东八区机器上错一天，
    而巨潮的时间戳多落在当地 0 点，错一天的后果是公告被归到前一天。
    """
    try:
        return datetime.datetime.fromtimestamp(int(ts) / 1000.0, _TZ)
    except (TypeError, ValueError, OSError, OverflowError):
        return None


_DT_RE = re.compile(r'^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})(?::(\d{2}))?')


def parse_dt(s) -> datetime.datetime | None:
    """'YYYY-MM-DD HH:MM[:SS]' → 北京时间 tz-aware（东财 showTime / publishDate / date）。"""
    if not s:
        return None
    m = _DT_RE.match(str(s).strip())
    if not m:
        return None
    y, mo, d, h, mi, sec = m.groups()
    return datetime.datetime(int(y), int(mo), int(d), int(h), int(mi),
                             int(sec or 0), tzinfo=_TZ)


_TAG_RE = re.compile(r'<[^>]+>')
_SCRIPT_RE = re.compile(r'<script.*?</script>|<style.*?</style>', re.S | re.I)
_WS_RE = re.compile(r'[ \t\u3000]+')


def clean_html(raw: str, limit: int = 0) -> str:
    """HTML → 纯文本：剥 script/style/标签、压空白、去空行。

    与 crawler/xueqiu/adapter.py 的 clean_text 有意不同：这里**不剥** $xxx$ / #xxx#，
    新闻正文里的 # 与 $ 是正常字符（雪球那两个是选股/话题标记）。
    代码提取在清洗前完成，故清洗不丢个股标注。
    """
    if not raw:
        return ''
    t = _SCRIPT_RE.sub(' ', raw)
    t = _TAG_RE.sub(' ', t)
    t = t.replace('&nbsp;', ' ').replace('\u3000', ' ')
    for a, b in (('&amp;', '&'), ('&lt;', '<'), ('&gt;', '>'),
                 ('&quot;', '"'), ('&#39;', "'")):
        t = t.replace(a, b)
    lines = [_WS_RE.sub(' ', ln).strip() for ln in t.splitlines()]
    out = '\n'.join(ln for ln in lines if ln)
    return out[:limit] if limit else out


def content_hash(title: str, content: str) -> str:
    """跨源同文去重键：sha1(标题|正文) 前 20 位。

    用于主备源切换窗口的重叠内容（备用源启用时同一事件可能两条），
    入库前一次性 SELECT 过滤，避免三份近重复内容把 LLM 成本放大。
    """
    h = hashlib.sha1()
    h.update((title or '').strip().encode('utf-8', 'replace'))
    h.update(b'|')
    h.update((content or '').strip().encode('utf-8', 'replace'))
    return h.hexdigest()[:20]


_CODE6_RE = re.compile(r'\d{6}')


def norm_code(raw) -> str | None:
    """'sh600487' / '0.300207' / '600519.SH' → '600487' / '300207' / '600519'。"""
    if not raw:
        return None
    digits = _CODE6_RE.findall(str(raw))
    return digits[0] if digits else None


# ── 东财统一出口 ─────────────────────────────────────────────────

def em_get(url, *, params=None, data=None, headers=None, timeout=20, method='GET'):
    """eastmoney.com 系请求唯一出口：串行限流（最小间隔 + 抖动）+ 连接复用。

    实测风控：每秒 >5 次 / 单 IP 并发 ≥10 / 1 分钟 ≥200 次 → 临时封 IP。
    锁内 sleep + 发请求（而非只锁 sleep）——本模块消费者是单线程采集节点，
    这样最简且不会出现两个线程同时越过闸门。
    """
    h = {'User-Agent': UA}
    if headers:
        h.update(headers)
    with _EM_LOCK:
        wait = EM_MIN_INTERVAL + random.uniform(0, EM_JITTER) - (time.time() - _EM_LAST[0])
        if wait > 0:
            time.sleep(wait)
        try:
            if method == 'POST':
                return _SESSION.post(url, data=data, headers=h, timeout=timeout)
            return _SESSION.get(url, params=params, headers=h, timeout=timeout)
        finally:
            _EM_LAST[0] = time.time()


# ── 快讯：财联社（主源）─────────────────────────────────────────

CLS_URL = 'https://www.cls.cn/v1/roll/get_roll_list'


def _cls_sign(params: dict) -> str:
    """签名 = md5(sha1(按 key 字典序拼接的 query 串))。纯本地计算，无需 key。"""
    qs = '&'.join(f'{k}={params[k]}' for k in sorted(params))
    return hashlib.md5(hashlib.sha1(qs.encode()).hexdigest().encode()).hexdigest()


def _map_cls(item: dict) -> dict | None:
    sid = item.get('id')
    pub = ts_to_dt(item.get('ctime'))
    if not sid or not pub:
        return None
    # 广告位（财联社 roll 里夹带）不入库
    if item.get('is_ad') or item.get('is_fad'):
        return None
    title = clean_html(item.get('title') or '')
    brief = clean_html(item.get('brief') or '')
    body = clean_html(item.get('content') or '')
    if not title and brief:
        title = brief.split('】')[0].lstrip('【') if '】' in brief else brief[:60]
    content = max((c for c in (body, brief) if c), key=len, default='')
    subjects = [(s.get('subject_name') or '') for s in (item.get('subjects') or [])]
    codes = [c for c in (norm_code(s.get('StockID')) for s in (item.get('stock_list') or [])) if c]
    extra = {
        'src_level': item.get('level'),          # 财联社自带等级 A/B/C（免费重要度先验）
        'subjects': [s for s in subjects if s][:6],
        'brief': brief[:400],
        'type': item.get('type'),
    }
    return {
        'source': 'cls', 'category': 'flash', 'source_id': str(sid),
        'published_at': pub, 'title': title[:500], 'content': content,
        'url': item.get('shareurl') or f'https://www.cls.cn/detail/{sid}',
        'extra': extra, 'stock_codes': sorted(set(codes)),
    }


def fetch_cls(cursor: str = '', max_pages: int = 4) -> tuple:
    """财联社电报增量：向旧翻页直至追上游标或达页数上限。

    返回 (items, new_cursor, note)。cursor 语义 = 「已入库的最新一条的 ctime」，
    本轮把所有 ctime > cursor 的条目收下；若游标为空（首次）只取第一页，避免首轮灌进历史。
    """
    items, note = [], ''
    seen_floor = int(cursor) if str(cursor or '').isdigit() else None
    last_time = ''
    max_ctime = None
    for page in range(max(1, max_pages)):
        params = {'appName': 'CailianpressWeb', 'os': 'web', 'sv': '7.7.5',
                  'last_time': last_time, 'refresh_type': '1', 'rn': '50'}
        qs = '&'.join(f'{k}={params[k]}' for k in sorted(params))
        r = _SESSION.get(f'{CLS_URL}?{qs}&sign={_cls_sign(params)}',
                         headers={'User-Agent': UA, 'Referer': 'https://www.cls.cn/'},
                         timeout=20)
        d = r.json()
        if d.get('errno') not in (0, None):
            raise RuntimeError(f'财联社 errno={d.get("errno")} msg={d.get("msg")}')
        rows = (d.get('data') or {}).get('roll_data') or []
        if not rows:
            note = f'第{page + 1}页空'
            break
        ctimes = [x.get('ctime') or 0 for x in rows]
        page_min, page_max = min(ctimes), max(ctimes)
        if page == 0:
            max_ctime = page_max
        # 追上游标即停：本页最旧已 ≤ 游标，更旧的页不必再拉
        hit_cursor = seen_floor is not None and page_min <= seen_floor
        for x in rows:
            if seen_floor is not None and (x.get('ctime') or 0) <= seen_floor:
                continue
            m = _map_cls(x)
            if m:
                items.append(m)
        if seen_floor is None or hit_cursor:
            break
        last_time = str(page_min)
    if max_ctime and seen_floor is not None and max_ctime <= seen_floor:
        note = note or '无新增'
    return items, (str(max_ctime) if max_ctime else cursor), note


# ── 快讯：东财 7×24（备源，不翻页）─────────────────────────────

EM724_URL = 'https://np-weblist.eastmoney.com/comm/web/getFastNewsList'


def _map_em724(item: dict) -> dict | None:
    sid = item.get('code')
    pub = parse_dt(item.get('showTime'))
    if not sid or not pub:
        return None
    title = clean_html(item.get('title') or '')
    content = clean_html(item.get('summary') or '')
    # stockList 混个股与板块（'0.300207' 是个股，'90.BK0433' 是板块，'999.x' 是内部码）
    codes = []
    for s in (item.get('stockList') or []):
        s = str(s)
        if re.fullmatch(r'[012]\.\d{6}', s):
            codes.append(s.split('.')[1])
    return {
        'source': 'em724', 'category': 'flash', 'source_id': str(sid),
        'published_at': pub, 'title': title[:500], 'content': content,
        'url': None,
        'extra': {'stockList': list(item.get('stockList') or [])[:10],
                  'title_color': item.get('titleColor')},
        'stock_codes': sorted(set(codes)),
    }


def fetch_em724(cursor: str = '', max_pages: int = 1) -> tuple:
    """东财 7×24 最新一页（pageSize=200 实测覆盖约 4 小时，>20 分钟轮询间隔）。

    该端点 sortEnd 无法向旧翻页（五轮探针验证），故不做增量游标：
    每次取最新 200 条，靠 UNIQUE(source, source_id) 幂等去重。
    """
    r = em_get(EM724_URL, params={'client': 'web', 'biz': 'web_724', 'fastColumn': '102',
                                  'sortEnd': '', 'pageSize': '200',
                                  'req_trace': str(uuid.uuid4())},
               headers={'Referer': 'https://kuaixun.eastmoney.com/'})
    d = r.json()
    if d.get('code') not in (1, '1', 0, '0', None):
        raise RuntimeError(f'东财7x24 code={d.get("code")} msg={d.get("msg")}')
    rows = (d.get('data') or {}).get('fastNewsList') or []
    items = [m for m in (_map_em724(x) for x in rows) if m]
    times = [m['published_at'] for m in items]
    return items, (str(int(max(times).timestamp())) if times else cursor), ''


# ── 快讯：华尔街见闻（备源）────────────────────────────────────

WSCN_URL = 'https://api-one-wscn.awtmt.com/apiv1/content/lives'


def _map_wscn(item: dict) -> dict | None:
    sid = item.get('id')
    pub = ts_to_dt(item.get('display_time'))
    if not sid or not pub:
        return None
    return {
        'source': 'wscn', 'category': 'flash', 'source_id': str(sid),
        'published_at': pub,
        'title': clean_html(item.get('title') or '')[:500],
        'content': clean_html(item.get('content_text') or ''),
        'url': item.get('uri') or '',
        'extra': {'score': item.get('score'),        # 见闻自带重要度 1/2/3
                  'channels': (item.get('channels') or [])[:6],
                  'themes': [t.get('name') for t in (item.get('related_themes') or [])
                             if isinstance(t, dict) and t.get('name')][:6]},
        'stock_codes': [],
    }


def fetch_wscn(cursor: str = '', max_pages: int = 4) -> tuple:
    """见闻 a-stock 频道增量：cursor=next_cursor 向旧翻页。"""
    items, note, last_cursor = [], '', cursor
    cur = cursor or None
    for page in range(max(1, max_pages)):
        params = {'channel': 'a-stock-channel', 'limit': '100'}
        if cur:
            params['cursor'] = cur
        r = _SESSION.get(WSCN_URL, params=params,
                         headers={'User-Agent': UA, 'Referer': 'https://wallstreetcn.com/'},
                         timeout=20)
        d = r.json()
        if d.get('code') != 20000:
            raise RuntimeError(f'见闻 code={d.get("code")} msg={d.get("message")}')
        data = d.get('data') or {}
        rows = data.get('items') or []
        if not rows:
            note = f'第{page + 1}页空'
            break
        if page == 0:
            last_cursor = data.get('next_cursor') or cursor
        for x in rows:
            m = _map_wscn(x)
            if m:
                items.append(m)
        nxt = data.get('next_cursor')
        if not nxt or str(nxt) == str(cur):
            break
        cur = nxt
        # 首次无游标：只取一页，避免把历史快讯整批灌进库
        if not cursor:
            break
    return items, str(last_cursor or ''), note


# ── 个股新闻（东财 JSONP + 正文页）─────────────────────────────

EM_STOCK_NEWS_URL = 'https://search-api-web.eastmoney.com/search/jsonp'


def fetch_em_stock_news(code: str, page_size: int = 20) -> list:
    """东财个股新闻。**空返回必须与「确实没新闻」区分**：

    该接口对住宅 IP 有间歇风控——只回 passportWeb（股民资料）而无 cmsArticleWebOld
    （文章列表）。故 result 里没有该键即视为风控/异常并抛错，由调用方记 fail_streak；
    result 有该键但列表为空才是「该股确实没新闻」。
    """
    inner = json.dumps({
        'uid': '', 'keyword': code, 'type': ['cmsArticleWebOld'], 'client': 'web',
        'clientType': 'web', 'clientVersion': 'curr',
        'param': {'cmsArticleWebOld': {'searchScope': 'default', 'sort': 'default',
                                       'pageIndex': 1, 'pageSize': page_size,
                                       'preTag': '', 'postTag': ''}},
    }, separators=(',', ':'))
    r = em_get(EM_STOCK_NEWS_URL, params={'cb': 'jQuery_news', 'param': inner},
               headers={'Referer': 'https://so.eastmoney.com/'})
    text = r.text
    try:
        d = json.loads(text[text.index('(') + 1: text.rindex(')')])
    except Exception as e:
        raise RuntimeError(f'东财个股新闻 JSONP 解析失败: {e}; body={text[:100]!r}')
    res = d.get('result') or {}
    if 'cmsArticleWebOld' not in res:
        raise RuntimeError(f'东财个股新闻风控/异常：result 缺 cmsArticleWebOld '
                           f'（keys={sorted(res.keys())}）')
    out = []
    for a in (res.get('cmsArticleWebOld') or []):
        pub = parse_dt(a.get('date'))
        if not pub or not a.get('code'):
            continue
        out.append({
            'source': 'em_stock', 'category': 'stock_news', 'source_id': str(a.get('code')),
            'published_at': pub,
            'title': clean_html(a.get('title') or '')[:500],
            'content': clean_html(a.get('content') or ''),   # 源摘要（~122 字），正文另抓
            'url': a.get('url') or '',
            'extra': {'media': a.get('mediaName')},
            'stock_codes': [code],
        })
    return out


_BODY_RE = re.compile(r'<div[^>]+id="ContentBody"[^>]*>(.*?)</div>\s*<div', re.S)


def fetch_article_body(url: str, max_chars: int = 6000) -> str:
    """东财新闻正文页 → 正文（失败返回 ''，调用方回退用源摘要）。

    东财 search-api 给的 content 只有 ~122 字源摘要，若第 2 层再压成 60 字摘要，
    「总结」相对「原文」几乎没有信息增量——故正文必须另开页面取。
    正文容器历年改过版，此处按 id="ContentBody" 取（2026-09-29 实测 697/751 字成功）。
    """
    if not url:
        return ''
    try:
        r = em_get(url, headers={'Referer': 'https://so.eastmoney.com/'}, timeout=20)
        if r.status_code != 200:
            return ''
        html = r.content.decode('utf-8', 'replace')
        m = _BODY_RE.search(html)
        if not m:
            return ''
        return clean_html(m.group(1), limit=max_chars)
    except Exception as e:
        logger.debug(f'[news] 正文抓取失败 {url}: {type(e).__name__}: {e}')
        return ''


# ── 研报（东财 reportapi）───────────────────────────────────────

EM_REPORT_URL = 'https://reportapi.eastmoney.com/report/list'


def _map_report(r: dict, qtype: str = '0') -> dict | None:
    sid = r.get('infoCode')
    pub = parse_dt(r.get('publishDate'))
    if not sid or not pub:
        return None
    stock_name = (r.get('stockName') or '').strip()
    # 研报原文是 PDF，列表接口不含正文；此处合成一行「标注摘要」作为 LLM 输入文本，
    # 结构化字段原样入 extra（不解析 PDF 正文——本期明确不做）
    parts = []
    if stock_name:
        parts.append(f'{stock_name}')
    ind = (r.get('indvInduName') or r.get('industryName') or '').strip()
    if ind:
        parts.append(f'行业：{ind}')
    if r.get('orgSName'):
        parts.append(f'机构：{r["orgSName"]}')
    if r.get('emRatingName'):
        parts.append(f'评级：{r["emRatingName"]}')
    eps = '/'.join(str(r.get(k)) for k in ('predictThisYearEps', 'predictNextYearEps',
                                           'predictNextTwoYearEps') if r.get(k))
    if eps:
        parts.append(f'EPS预测：{eps}')
    rtype = str(r.get('reportType') or '').strip()
    if rtype and not rtype.isdigit():        # reportType 多为数字码，拼进正文是噪音
        parts.append(rtype)
    code = norm_code(r.get('stockCode'))
    extra = {
        'org': r.get('orgSName'), 'org_full': r.get('orgName'),
        'rating': r.get('emRatingName'), 'rating_change': r.get('ratingChange'),
        'eps': {'this': r.get('predictThisYearEps'), 'next': r.get('predictNextYearEps'),
                'next2': r.get('predictNextTwoYearEps')},
        'industry': ind, 'report_type': r.get('reportType'),
        'researcher': r.get('researcher'), 'stock_name': stock_name,
        'attach_pages': r.get('attachPages'), 'synthetic_content': True,
    }
    return {
        'source': 'em_report', 'category': 'report', 'source_id': str(sid),
        'published_at': pub,
        'title': clean_html(r.get('title') or '')[:500],
        'content': '｜'.join(parts),
        'url': (f'https://pdf.dfcfw.com/pdf/H3_{sid}_1.pdf'),
        'extra': extra,
        # 行业研报（qType=1）带的 stockCode 是代表股而非研究对象，不建个股关联
        'stock_codes': [code] if (code and qtype == '0') else [],
    }


def fetch_em_reports(code: str = '', qtype: str = '0', pages: int = 1,
                     page_size: int = 30, begin: str = '') -> list:
    """东财研报。qtype=0 个股（需 code）/ 1 行业。begin 可限起始日减少翻页。"""
    out = []
    for page in range(1, max(1, pages) + 1):
        r = em_get(EM_REPORT_URL, params={
            'industryCode': '*', 'pageSize': str(page_size), 'industry': '*',
            'rating': '*', 'ratingChange': '*',
            'beginTime': begin or '2000-01-01', 'endTime': '2030-01-01',
            'pageNo': str(page), 'fields': '', 'qType': qtype,
            'orgCode': '', 'code': code if qtype == '0' else '', 'rcode': '',
            'p': str(page), 'pageNum': str(page), 'pageNumber': str(page),
        }, headers={'Referer': 'https://data.eastmoney.com/'}, timeout=30)
        d = r.json()
        rows = d.get('data') or []
        if not rows:
            break
        out.extend(m for m in (_map_report(x, qtype) for x in rows) if m)
        if page >= (d.get('TotalPage') or 1):
            break
    return out


# ── 公告（巨潮 cninfo）─────────────────────────────────────────

CNINFO_ORGID_URL = 'http://www.cninfo.com.cn/new/data/szse_stock.json'
CNINFO_QUERY_URL = 'https://www.cninfo.com.cn/new/hisAnnouncement/query'


def fetch_cninfo_orgid_map() -> dict:
    """官方「股票→orgId」映射（6258 条，含全市场，非仅深市）。

    orgId 并非统一格式（601318→9900002221 / 601398→jjxt0000019 / 688017→9900041602），
    硬编码会让 601xxx 段大量返回 totalAnnouncement=0，故必须动态取。
    """
    r = _SESSION.get(CNINFO_ORGID_URL, headers={'User-Agent': UA}, timeout=20)
    lst = (r.json() or {}).get('stockList') or []
    return {s['code']: s['orgId'] for s in lst if s.get('code') and s.get('orgId')}


def fetch_cninfo_notices(code: str, org_id: str, page_size: int = 30) -> list:
    """巨潮公告（公告正文是 PDF，本期不解析，只存标题 + 类别 + PDF 链接）。

    **org_id 缺失必须抛错，不能兜底**：2026-09-29 实测「gssh0{code}」式兜底格式对
    300274/301511 这类深市新股一律返回 0 条（官方 orgId 9900021300 则有 2459 条），
    而「0 条」与「该股确实没公告」在响应里长得一模一样 —— 静默少数据的典型形态。
    故调用方必须先拿到官方映射里的 orgId；拿不到就报错让它显性化。
    """
    if not org_id:
        raise RuntimeError(f'巨潮 orgId 缺失（{code} 不在官方映射表中），跳过公告采集')
    r = em_get(CNINFO_QUERY_URL, method='POST',
               data={'stock': f'{code},{org_id}', 'tabName': 'fulltext',
                     'pageSize': str(page_size), 'pageNum': '1', 'column': '',
                     'category': '', 'plate': '', 'seDate': '', 'searchkey': '',
                     'secid': '', 'sortName': '', 'sortType': '', 'isHLtitle': 'true'},
               headers={'Content-Type': 'application/x-www-form-urlencoded',
                        'Referer': 'https://www.cninfo.com.cn/new/disclosure',
                        'Origin': 'https://www.cninfo.com.cn'}, timeout=20)
    d = r.json()
    anns = d.get('announcements') or []
    total = d.get('totalAnnouncement')
    if not anns and total:
        raise RuntimeError(f'巨潮公告异常：totalAnnouncement={total} 但无行（orgId={org_id} 可能失效）')
    out = []
    for a in anns:
        pub = ms_to_dt(a.get('announcementTime'))
        sid = a.get('announcementId')
        if not pub or not sid:
            continue
        atype = (a.get('announcementTypeName') or a.get('announcementType')
                 or a.get('pageColumn') or '').strip()
        adj = a.get('adjunctUrl') or ''
        out.append({
            'source': 'cninfo', 'category': 'notice', 'source_id': str(sid),
            'published_at': pub,
            'title': clean_html(a.get('announcementTitle') or '')[:500],
            'content': f'公告类别：{atype}' if atype else '',
            'url': f'https://static.cninfo.com.cn/{adj}' if adj else
                   f'https://www.cninfo.com.cn/new/disclosure/detail?annoId={sid}',
            'extra': {'type': atype, 'page_column': a.get('pageColumn'),
                      'important': a.get('important'), 'sec_name': a.get('secName'),
                      'synthetic_content': not bool(atype)},
            'stock_codes': [code],
        })
    return out


# ── 主备选源 ────────────────────────────────────────────────────

FLASH_SOURCES = [
    ('cls', fetch_cls, '财联社电报'),          # 主源
    ('em724', fetch_em724, '东财7×24'),        # 备 1
    ('wscn', fetch_wscn, '华尔街见闻'),         # 备 2
]


def probe() -> None:
    """手动连通性自检：python -m crawler.news.sources

    端点死亡率是本领域主要风险（财联社 2026-05 死过一次、2026-07 才复活），
    任何「端点不通」都应先跑这个确认，而不是去改采集逻辑。
    """
    for name, fn, label in FLASH_SOURCES:
        t0 = time.time()
        try:
            items, cur, note = fn('', 1)
            span = f'{items[-1]["published_at"]:%m-%d %H:%M} ~ {items[0]["published_at"]:%m-%d %H:%M}' if items else '-'
            print(f'  [OK]   {label:12s} {len(items):3d} 条 {span} cursor={cur} {note}')
        except Exception as e:
            print(f'  [FAIL] {label:12s} {type(e).__name__}: {e}')
        print(f'         {time.time() - t0:.1f}s')
    try:
        items = fetch_em_stock_news('600519')
        print(f'  [OK]   东财个股新闻 {len(items)} 条：{items[0]["title"][:40] if items else ""}')
        body = fetch_article_body(items[0]['url']) if items else ''
        print(f'  [OK]   正文页 {len(body)} 字')
    except Exception as e:
        print(f'  [FAIL] 东财个股新闻 {type(e).__name__}: {e}')
    try:
        items = fetch_em_reports('600519', '0', 1, 10)
        print(f'  [OK]   东财研报 {len(items)} 条：{items[0]["title"][:40] if items else ""}')
    except Exception as e:
        print(f'  [FAIL] 东财研报 {type(e).__name__}: {e}')
    try:
        m = fetch_cninfo_orgid_map()
        items = fetch_cninfo_notices('600519', m.get('600519') or cninfo_fallback_orgid('600519'))
        print(f'  [OK]   巨潮公告 映射 {len(m)} 条 / {len(items)} 条公告')
    except Exception as e:
        print(f'  [FAIL] 巨潮公告 {type(e).__name__}: {e}')


if __name__ == '__main__':
    print('资讯源连通性自检：')
    probe()
