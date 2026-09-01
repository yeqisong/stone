"""KEPL 解析器 API（v2.0 重构 迭代 2.1；v2.3 + 算子目录单一事实源）。"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/kepl", tags=["kepl"])


class ParseRequest(BaseModel):
    formula: str
    entity: str = "stock"  # stock/etf/index/global


@router.post("/parse")
def parse_formula(body: ParseRequest):
    """解析 KEPL 公式，返回 AST 和错误列表。"""
    from app.kepl.parser import parse_kepl
    return parse_kepl(body.formula, body.entity)


# ── 算子目录（v2.3）：内置算子文档单一事实源 ──
# 前端编辑器提示与 AI 生成上下文均从 /kepl/functions 拉取。
# 强校验：此字典必须与 parser 注册表完全一致（端点即查，防加算子忘补文档）。

_OPERATOR_DOCS = {
    # 时序：均线/经典指标
    'ma':          {'sig': 'ma(close, N)', 'desc': 'N 日简单均线', 'eg': 'ma(close, 20)/close'},
    'ema':         {'sig': 'ema(close, N)', 'desc': 'N 日指数均线（span=N）', 'eg': 'ema(close, 12) - ema(close, 26)'},
    'std':         {'sig': 'std(close, N)', 'desc': 'N 日滚动标准差（样本，ddof=1）', 'eg': 'std(close, 20)/(close+1e-12)'},
    'rsi':         {'sig': 'rsi(close, N)', 'desc': 'N 日 RSI（0~100）', 'eg': 'rsi(close, 14)'},
    'atr':         {'sig': 'atr(close, N)', 'desc': 'N 日真实波幅均值（自动补 high/low）', 'eg': 'atr(close, 14)/close'},
    'pct_change':  {'sig': 'pct_change(close, N)', 'desc': 'N 日涨跌幅', 'eg': 'pct_change(close, 5)'},
    'boll_upper':  {'sig': 'boll_upper(close)', 'desc': '布林上轨（20 日，2σ）', 'eg': 'close/boll_upper(close)'},
    'boll_mid':    {'sig': 'boll_mid(close)', 'desc': '布林中轨', 'eg': 'close/boll_mid(close)'},
    'boll_lower':  {'sig': 'boll_lower(close)', 'desc': '布林下轨', 'eg': '(close-boll_lower(close))/(boll_upper(close)-boll_lower(close)+1e-12)'},
    'dif':         {'sig': 'dif(close)', 'desc': 'MACD DIF（EMA12-EMA26）', 'eg': 'dif(close) - dea(close)'},
    'dea':         {'sig': 'dea(close)', 'desc': 'MACD DEA（DIF 的 9 日 EMA）', 'eg': 'dea(close)'},
    'macd_hist':   {'sig': 'macd_hist(close)', 'desc': 'MACD 红绿柱（2×(DIF-DEA)）', 'eg': 'macd_hist(close)/(close+1e-12)'},
    # 时序：Alpha158 移植（M5）
    'ref':         {'sig': 'ref(close, N)', 'desc': 'N 日前值；负 N = 未来值（显式语义）', 'eg': 'ref(close, 5)/(close+1e-12)'},
    'hhv':         {'sig': 'hhv(high, N)', 'desc': 'N 日窗口最高（未满窗为空）', 'eg': 'hhv(high, 60)/(close+1e-12)'},
    'llv':         {'sig': 'llv(low, N)', 'desc': 'N 日窗口最低（未满窗为空）', 'eg': 'llv(low, 20)/(close+1e-12)'},
    'ts_quantile': {'sig': 'ts_quantile(close, N, Q)', 'desc': 'N 日滚动分位数（Q∈[0,1]）', 'eg': 'ts_quantile(close, 20, 0.8)/close'},
    'ts_rank':     {'sig': 'ts_rank(close, N)', 'desc': '当前值在 N 日窗口内的分位（0~1，Qlib Rank）', 'eg': 'ts_rank(close, 10)'},
    'slope':       {'sig': 'slope(close, N)', 'desc': 'N 日窗口线性回归斜率', 'eg': 'slope(close, 20)/(close+1e-12)'},
    'rsquare':     {'sig': 'rsquare(close, N)', 'desc': '窗口线性回归 R²（趋势显著度）', 'eg': 'rsquare(close, 30)'},
    'resi':        {'sig': 'resi(close, N)', 'desc': '窗口回归残差（当日值−拟合值）', 'eg': 'resi(close, 20)/(close+1e-12)'},
    'imax':        {'sig': 'imax(high, N)', 'desc': '距窗口最高值的天数（0=当日即最高）', 'eg': 'imax(high, 20)/20'},
    'imin':        {'sig': 'imin(low, N)', 'desc': '距窗口最低值的天数', 'eg': 'imin(low, 20)/20'},
    'ts_corr':     {'sig': 'ts_corr(close, volume, N)', 'desc': '两序列 N 日滚动相关（-1~1）', 'eg': 'ts_corr(close, volume, 20)'},
    'max2':        {'sig': 'max2(a, b)', 'desc': '逐元素取大（序列或标量）', 'eg': '(high-max2(open, close))/open'},
    'min2':        {'sig': 'min2(a, b)', 'desc': '逐元素取小', 'eg': '(min2(open, close)-low)/open'},
    # 截面
    'avg':         {'sig': 'avg(字段)', 'desc': '同交易日截面均值', 'eg': 'close/avg(close)'},
    'sum':         {'sig': 'sum(字段)', 'desc': '截面求和', 'eg': 'volume/sum(volume)'},
    'max':         {'sig': 'max(字段)', 'desc': '截面最大', 'eg': 'close/max(close)'},
    'min':         {'sig': 'min(字段)', 'desc': '截面最小', 'eg': 'close/min(close)'},
    'rank':        {'sig': 'rank(字段)', 'desc': '截面分位（0~1，逐日排名）', 'eg': 'rank(close)'},
    'quantile':    {'sig': 'quantile(字段, Q)', 'desc': '截面分位数', 'eg': 'close - quantile(close, 0.5)'},
    'zscore':      {'sig': 'zscore(字段)', 'desc': '截面 z 标准化', 'eg': 'zscore(close/ma(close, 20))'},
}


@router.get("/functions")
def list_functions():
    """内置算子目录（编辑器提示 + AI 生成上下文的单一事实源）。

    返回按类别分组的算子文档；文档字典与 parser 注册表强校验——
    新增算子未补文档时此端点直接报错，杜绝文档漂移。
    """
    from app.kepl.parser import TIME_SERIES_FUNCTIONS, CROSS_SECTIONAL_FUNCTIONS

    documented = set(_OPERATOR_DOCS)
    ts, cs = set(TIME_SERIES_FUNCTIONS), set(CROSS_SECTIONAL_FUNCTIONS)
    missing = (ts | cs) - documented
    unknown = documented - (ts | cs)
    if missing or unknown:
        raise HTTPException(500, f"算子文档与注册表不一致: 缺文档 {sorted(missing)} 多余 {sorted(unknown)}"
                                 f"——请在 app/api/kepl.py _OPERATOR_DOCS 补齐")

    return {
        'time_series': [dict(_OPERATOR_DOCS[n], name=n) for n in sorted(ts)],
        'cross_sectional': [dict(_OPERATOR_DOCS[n], name=n) for n in sorted(cs)],
        'syntax': {
            'fields': 'close, open, high, low, volume, amount（裸字段，另支持基本面字段 pe_ttm/pb_mrq 等，见 /api/features/fields）',
            'arith': '+ - * /（除零自动置空）',
            'literal': '数字字面量；函数参数位支持负数（如 ref(close, -1) 表示未来值）',
            'note': '不支持 if/比较/逻辑运算——条件逻辑请用自定义 Python 函数（functions 表）',
        },
    }
