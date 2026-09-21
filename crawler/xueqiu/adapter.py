"""雪球个股讨论采集 adapter：纯 HTTP（curl_cffi + cookie），不依赖浏览器。

P0 标定结论（design/06 §9.1）：
- 端点必须走 api.xueqiu.com 子域（主域被 WAF 按路径差异化拦截）；
- count 上限 20，page 1..maxPage（统一 50，每股回溯 1000 帖），sort=time 严格倒序；
- md5__1038 签名参数非必需；
- 评分触发表现为 HTTP 200 + 挑战 HTML（非 429）——成败必须按响应体是否 JSON 判定。

登录 token（形态 C）：环境变量 XUEQIU_TOKENS，格式 'k1=v1;k2=v2'（浏览器 F12 复制），
配置后优先生效且不做匿名洗白（Guard 侧依据 login_mode 切换策略）。
"""
import datetime
import html
import os
import re
from dataclasses import dataclass, field

from curl_cffi import requests as cffi_requests
from loguru import logger

UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
API = 'https://api.xueqiu.com/query/v1/symbol/search/status.json'

_TZ = datetime.timezone(datetime.timedelta(hours=8))  # 北京时间


def to_xq_symbol(code: str) -> str:
    """6 位码 → 雪球 symbol（SH/SZ/BJ 前缀）。"""
    code = code.strip()
    if code.startswith(('SH', 'SZ', 'BJ')):
        return code
    if code.startswith(('60', '68', '90')):
        return 'SH' + code
    if code.startswith(('00', '30', '20')):
        return 'SZ' + code
    if code.startswith(('83', '87', '43', '92', '88')):
        return 'BJ' + code
    return 'SH' + code  # 兜底，异常码在试点池外不会出现


_TAG_RE = re.compile(r'<[^>]+>')
_MENTION_RE = re.compile(r'\$[^$\n]{1,40}\$')
_TOPIC_RE = re.compile(r'#[^#\n]{1,40}#')
_WS_RE = re.compile(r'\s+')


def clean_text(raw: str) -> str:
    """富文本 → 纯文本：剥 HTML 标签、$股票$、#话题#，压空白。"""
    if not raw:
        return ''
    t = _TAG_RE.sub(' ', html.unescape(raw))
    t = _MENTION_RE.sub(' ', t)
    t = _TOPIC_RE.sub(' ', t)
    return _WS_RE.sub(' ', t).strip()[:4000]


@dataclass
class FetchResult:
    ok: bool = False
    challenged: bool = False       # True = 命中 WAF 行为评分（200 + 挑战 HTML）
    error: str = ''
    items: list = field(default_factory=list)   # 原始 status dict 列表
    max_page: int = 0


@dataclass
class StatusRow:
    """xueqiu_status 行（写缓冲单元）。"""
    status_id: int
    code: str
    user_id: int | None
    created_at: datetime.datetime
    title: str
    text_raw: str
    text_clean: str
    mark: int
    view_count: int
    like_count: int
    retweet_count: int
    reply_count: int
    fav_count: int
    source: str
    status_type: str


def map_item(item: dict, code: str) -> StatusRow:
    created_ms = item.get('created_at') or 0
    return StatusRow(
        status_id=int(item['id']),
        code=code,
        user_id=item.get('user_id'),
        created_at=datetime.datetime.fromtimestamp(created_ms / 1000, tz=_TZ),
        title=(item.get('title') or '')[:512],
        text_raw=item.get('description') or item.get('text') or '',
        text_clean=clean_text(item.get('description') or item.get('text') or ''),
        mark=int(item.get('mark') or 0),
        view_count=int(item.get('view_count') or 0),
        like_count=int(item.get('like_count') or 0),
        retweet_count=int(item.get('retweet_count') or 0),
        reply_count=int(item.get('reply_count') or 0),
        fav_count=int(item.get('fav_count') or 0),
        source=(item.get('source') or '')[:32],
        status_type=str(item.get('type') or '')[:16],
    )


def load_login_cookies() -> dict[str, str] | None:
    """读 XUEQIU_TOKENS（'k1=v1;k2=v2'）→ dict；未配置返回 None（匿名模式）。"""
    raw = os.environ.get('XUEQIU_TOKENS', '').strip()
    if not raw:
        return None
    out: dict[str, str] = {}
    for pair in raw.split(';'):
        pair = pair.strip()
        if '=' in pair:
            k, v = pair.split('=', 1)
            out[k.strip()] = v.strip()
    return out if 'xq_a_token' in out else None


class XueqiuAdapter:
    """单会话采集器（P1 单线程串行消费，无并发）。"""

    def __init__(self):
        self._cookies: dict[str, str] = {}
        self._session = cffi_requests.Session(impersonate='chrome')  # 连接复用 + Chrome TLS 指纹
        self.set_cookies(load_login_cookies() or {})

    @property
    def login_mode(self) -> bool:
        """登录 token 模式（形态 C）：环境变量提供了有效 cookie。"""
        return bool(os.environ.get('XUEQIU_TOKENS', '').strip())

    def set_cookies(self, cookies: dict[str, str]):
        self._cookies = cookies or {}

    @property
    def cookie_header(self) -> str:
        return '; '.join(f'{k}={v}' for k, v in self._cookies.items())

    def ready(self) -> bool:
        return 'xq_a_token' in self._cookies

    def fetch_page(self, code: str, page: int = 1, count: int = 20) -> FetchResult:
        """拉一页个股讨论列表（sort=time 时间倒序）。"""
        if not self.ready():
            return FetchResult(error='no cookie')
        try:
            r = self._session.get(
                API,
                params={'symbol': to_xq_symbol(code), 'count': count, 'page': page,
                        'sort': 'time', 'source': 'all', 'q': ''},
                headers={'User-Agent': UA,
                         'Referer': f'https://xueqiu.com/S/{to_xq_symbol(code)}',
                         'Accept': 'application/json',
                         'Cookie': self.cookie_header},
                timeout=15,
            )
        except Exception as e:
            return FetchResult(error=f'req exc: {e}')

        ct = r.headers.get('content-type', '')
        if r.status_code != 200 or 'json' not in ct:
            # 200 + HTML = WAF 行为评分挑战（P0 实测形态）
            challenged = r.status_code == 200 and 'html' in ct
            logger.debug(f'雪球 {code} p{page} 非预期响应 code={r.status_code} ct={ct[:30]} '
                         f'challenged={challenged}')
            return FetchResult(challenged=challenged,
                               error=f'http {r.status_code} ct {ct[:30]}')
        try:
            d = r.json()
        except Exception as e:
            return FetchResult(error=f'json exc: {e}')
        items = d.get('list') or []
        return FetchResult(ok=True, items=items, max_page=int(d.get('maxPage') or 0))
