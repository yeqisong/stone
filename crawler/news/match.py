"""文本 → 个股匹配（design/07：快讯无源自带股票标注时的关联手段）。

**只在关注池内匹配**，这是关键设计：全市场 5000 个名字做子串匹配会遇到两类必然的
误报——① 重名歧义（A/B 股、更名后的同名），② 短名/通用片段被任意文本命中。
限定在关注池（持仓 ∪ 自选，通常几十到几百只）后，规模允许做「长名优先」的暴力扫描，
且池内成员是用户真正关心的标的，误报代价从"污染全库关联"降到"多一条可忽略的关联"。

纯函数、零 DB、零 IO，故可单测（tests/unit/test_news_match.py）。
"""
import re

# 名称长度下限：2 字名（如"万科"实为"万科A"）歧义面太大，不参与匹配。
# 关注池里的正式名称几乎都 ≥3 字（贵州茅台/中国平安/万科A）。
MIN_NAME_LEN = 3

# 逃生口：确知会造成误报的名称（如与常用词同形的短名）放这里整体剔除。
# 默认空——先观测真实误报再往这里加，不预先臆测。
STOP_NAMES: set = set()

# 归一化：去括号内容（招商银行(香港)→招商银行）、去末尾单字母（万科A→万科）、去空白
_SUFFIX_RE = re.compile(r'[（(].*?[)）]|[A-Z]$|\s+')
_PLACEHOLDER = '\x00'


def normalize_name(name: str) -> str:
    """名称归一化，使「万科A」与文本中的「万科」可互命中。"""
    if not name:
        return ''
    return _SUFFIX_RE.sub('', str(name)).strip()


def build_name_index(rows) -> list:
    """构建 [(名称, 代码), ...]，按名称长度降序（供长名优先扫描，建一次复用多轮）。

    rows: [(stock_code, stock_name), ...]，通常来自 stock_master 与关注池的连接
    （调用方需自行限定 stock_type='stock'，避免指数/基金混入）。
    跳过太短的名字；同一名称对应多个代码时整体丢弃——歧义名宁可漏，不可错。
    """
    idx, dup = {}, set()
    for code, name in rows:
        nm = normalize_name(name)
        if len(nm) < MIN_NAME_LEN or nm in STOP_NAMES:
            continue
        if nm in idx and idx[nm] != code:
            dup.add(nm)
            continue
        idx[nm] = code
    for nm in dup:
        idx.pop(nm, None)
    return sorted(idx.items(), key=lambda kv: len(kv[0]), reverse=True)


def extract_matches(text: str, name_index: list, limit: int = 10) -> list:
    """从文本中抽出命中的股票代码（长名优先，避免「中国平安」被「平安银行」截断误配）。

    按名称长度降序扫描，命中后用等长占位符抹掉该片段，
    这样后扫的短名不会落在已被长名吃掉的区域里。
    """
    if not text or not name_index:
        return []
    hits = []
    for nm, code in name_index:
        if nm in text:
            hits.append(code)
            text = text.replace(nm, _PLACEHOLDER * len(nm))
            if len(hits) >= limit:
                break
    return hits
