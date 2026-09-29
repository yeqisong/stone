"""雪球帖子 LLM 情绪打分（design/06 P3 前置试点）。

数据流：v_xueqiu_clean_status（已排除公告帖与水军）→ 未打分热帖 → LLM 三分类 → xueqiu_sentiment。

用法：
    venv/bin/python -m scripts.xueqiu_sentiment                    # 全市场热帖 300 条
    venv/bin/python -m scripts.xueqiu_sentiment --limit 500 --min-heat 10
    venv/bin/python -m scripts.xueqiu_sentiment --codes 600519,000002 --limit 100
    venv/bin/python -m scripts.xueqiu_sentiment --rescore --limit 1000   # 重打分已打过的帖（A/B 对照）

情绪定义（A股语境）：
    1  = 看多（含调侃式看好、抄底意愿、利好解读）
    0  = 中性 / 闲聊 / 提问 / 与涨跌无关
    -1 = 看空（含愤怒式看空、割肉抱怨、利空解读）

prompt 版本（落库 prompt_ver 字段，便于 A/B 与回滚）：
    p1 = 首版（规则+一句口吻注意）
    p2 = 加 few-shot（针对 p1 实测暴露的「反驳/质疑型」系统性误判：模型理由写对了、标签发反）
"""
import argparse
import json
import sys
import time

sys.path.insert(0, '/home/bnbnyu/projects/stone')

from loguru import logger
from openai import OpenAI
from sqlalchemy import text

from app.config import settings
from app.db.connection import get_sync_db

PROMPT_VER = 'p2'

PROMPT = """你是A股散户情绪标注员。判断【发帖者本人】对该股的真实立场，不是判断帖子里被提到观点的方向。

判断步骤：
1. 先找发帖者在评论/反驳/赞同谁的什么观点——引用他人观点（含"有人说""某大V""唱空的"）时，立场属于发帖者，不属于被引用者。
2. 再看发帖者本人站在哪一边，据此定标签。反问句、反讽、调侃按真实立场判。

1=看多：看好、想买、抄底、利好解读；反驳/嘲讽唱空者、驳斥利空担忧、下跌中打气
0=中性：陈述事实、提问、闲聊、与涨跌无关；只讲方法/纪律不给方向
-1=看空：看空、想卖、割肉抱怨、利空解读；反驳/嘲讽看多者

示例（务必照此口径）：
- "现在唱空的，怕是失了智了" → 1（嘲讽空头，作者看多）
- "某大V喊要跌到10元，我拉黑他了，信他的这两年少赚几千万" → 1（反驳唱空，作者看多）
- "有人说铜要跌，我们逐条拆解这几个担忧" → 1（驳斥看空理由，作者看多）
- "跌破均线那一刻我已经关闭软件了" → -1（割肉回避）
- "朋友问我抄底怎么选标的，我说标准是有矿" → 0（方法讨论，无方向判断）

帖子（JSON 数组，id 为帖子标识）：
{posts}

只返回 JSON 数组，每项 {{"id": 帖子id, "s": 1或0或-1, "c": 0到1置信度, "r": "不超过15字的依据"}}，不要其它文字。"""

BATCH = 10           # 每次请求打包帖数（省 token；情绪三分类批量不损准确率）
RPM_LIMIT = 25       # 每分钟请求数上限（DeepSeek/GLM 通用保守值）
FAIL_MAX = 3
WORKERS = 8          # 并发请求数。实测单请求 ~28s，串行仅 2.1 rpm（用掉 8% 预算）；
                     # 8 并发 → ~17 rpm，接近日预算上限。不设更高：RPM 闸门会自动兜住。


def fetch_pending(db, limit: int, min_heat: int, codes: list[str] | None, rescore: bool):
    """取待打分帖子：干净帖视图（已排除公告/水军），按「发帖日倒序 → 热度倒序」。

    排序口径（2026-09-24 修订）：原为纯 heat 倒序，但存在历史积压时（首次上线
    积压 1.3 万帖），近期帖会排在多年前的高热老帖后面——展示层要新帖、IC 体检
    要**逐日截面完整**，两者都被纯热度序拖坏。改为日期优先：先填满最近一天，
    再填前一天，日内在按热度取。积压期（8000/夜 vs 稳态新增 ~5000/夜）约 3 夜填平。

    rescore=True 时改为「重打已有分数的帖」，且仍按 heat 倒序——那是 prompt A/B
    的固定对照集，改排序会让两次对照的样本不一致。
    """
    code_filter = "AND s.code = ANY(:codes) " if codes else ""
    if rescore:
        sql = text(f"""
            SELECT s.status_id, s.code, s.text_clean,
                   COALESCE(s.like_count,0) + COALESCE(s.reply_count,0)*3 AS heat
            FROM v_xueqiu_clean_status s
            JOIN xueqiu_sentiment se ON se.status_id = s.status_id
            WHERE COALESCE(s.text_clean, '') != ''
              AND LENGTH(s.text_clean) BETWEEN 10 AND 800
              {code_filter}
            ORDER BY heat DESC LIMIT :l
        """)
    else:
        sql = text(f"""
            SELECT s.status_id, s.code, s.text_clean,
                   COALESCE(s.like_count,0) + COALESCE(s.reply_count,0)*3 AS heat
            FROM v_xueqiu_clean_status s
            LEFT JOIN xueqiu_sentiment se ON se.status_id = s.status_id
            WHERE se.status_id IS NULL AND COALESCE(s.text_clean, '') != ''
              AND LENGTH(s.text_clean) BETWEEN 10 AND 800
              AND COALESCE(s.like_count,0) + COALESCE(s.reply_count,0)*3 >= :heat
              {code_filter}
            ORDER BY (s.created_at AT TIME ZONE 'Asia/Shanghai')::date DESC, heat DESC
            LIMIT :l
        """)
    params = {'heat': min_heat, 'l': limit}
    if codes:
        params['codes'] = codes
    return db.execute(sql, params).fetchall()


def _try_batch(client, model: str, batch, fail_max: int):
    """单批 LLM 调用 + 解析。成功返回结果列表，解析/调用失败返回 None。"""
    from scripts.llm_batch import llm_json_batch, pick_by_ids
    posts_json = json.dumps([{'id': r[0], 'code': r[1], 'text': r[2][:400]} for r in batch],
                            ensure_ascii=False)
    arr = llm_json_batch(client, model, PROMPT.replace('{posts}', posts_json),
                         fail_max=fail_max, max_tokens=1500, log_tag='xq')
    if arr is None:
        return None
    valid = {r[0] for r in batch}
    out = []
    for it in pick_by_ids(arr, valid):        # LLM 幻觉 id 防御：只收本批的
        try:
            s = int(it['s'])
        except (KeyError, TypeError, ValueError):
            continue
        if s not in (1, 0, -1):
            continue
        out.append((int(it['id']), s, float(it.get('c') or 0.5),
                    str(it.get('r') or '')[:200]))
    if len(out) < len(batch):
        # 同 news：回收不全留痕（模型回了多少、首条长什么样），否则只看到「失败 N 条」
        sample = arr[0] if arr and isinstance(arr[0], dict) else arr[:1]
        logger.warning(f'[xq] 本批 {len(batch)} 条仅解析出 {len(out)} 条'
                       f'（模型返回 {len(arr)} 条，首条 {str(sample)[:150]}）')
    return out


def score_batch(client, model: str, batch, fail_max: int):
    """单批打分（纯 LLM 调用，不碰 DB——供线程池并发）。
    整批失败时折半重试：单条坏 JSON 只牺牲自己，不拖累同批其余帖。"""
    from scripts.llm_batch import retry_split
    return retry_split(lambda b: _try_batch(client, model, b, fail_max), batch,
                       log_tag='xq')


def run_scoring(db, client, model: str, rows, *, workers: int = WORKERS,
                rpm: int = RPM_LIMIT, fail_max: int = FAIL_MAX, batch: int = BATCH,
                rescore: bool = False, progress_cb=None):
    """打分主循环。CLI 与 DAG 节点共用这一份实现，避免两处口径漂移。

    轮询/并发/心跳的通用部分在 scripts/llm_batch.run_batches（与资讯总结共用）；
    此处只提供本任务的落库写法。返回 {'done','fail','total','req','elapsed'}。
    """
    from scripts.llm_batch import run_batches
    sql = text("""INSERT INTO xueqiu_sentiment
                      (status_id, sentiment, confidence, reason, model, prompt_ver)
                  VALUES (:i, :s, :c, :r, :m, :pv)
                  """ + ("""ON CONFLICT (status_id) DO UPDATE SET
                      sentiment=EXCLUDED.sentiment, confidence=EXCLUDED.confidence,
                      reason=EXCLUDED.reason, model=EXCLUDED.model,
                      prompt_ver=EXCLUDED.prompt_ver, created_at=now()"""
                         if rescore else 'ON CONFLICT (status_id) DO NOTHING'))

    def _write(b, results):
        for sid, s, c, r in results:
            db.execute(sql, {'i': sid, 's': s, 'c': c, 'r': r,
                             'm': model, 'pv': PROMPT_VER})
        db.commit()

    return run_batches(rows, lambda b: score_batch(client, model, b, fail_max), _write,
                       workers=workers, rpm=rpm, batch_size=batch,
                       progress_cb=progress_cb, log_tag='xq')


def score_pending(db, client, model: str, *, limit: int = 300, min_heat: int = 5,
                  codes: list[str] | None = None, workers: int = WORKERS,
                  rpm: int = RPM_LIMIT, rescore: bool = False, progress_cb=None):
    """取待打分帖 + 执行打分（CLI 与 DAG 节点共用入口）。"""
    rows = fetch_pending(db, limit, min_heat, codes, rescore)
    logger.info(f'待打分 {len(rows)} 帖（min_heat={min_heat}, '
                f'codes={codes or "全市场"}, prompt_ver={PROMPT_VER}）')
    return run_scoring(db, client, model, rows, workers=workers, rpm=rpm,
                       rescore=rescore, progress_cb=progress_cb)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--limit', type=int, default=300, help='本次最多打分帖数')
    ap.add_argument('--min-heat', type=int, default=5, help='最低互动热度（like+reply*3）')
    ap.add_argument('--codes', type=str, default='', help='逗号分隔股票码，空=全市场')
    ap.add_argument('--workers', type=int, default=WORKERS, help='并发请求数')
    ap.add_argument('--rescore', action='store_true', help='重打分已打过的帖（覆盖，用于 prompt A/B）')
    args = ap.parse_args()
    codes = [c.strip() for c in args.codes.split(',') if c.strip()]

    llm = settings.llm_config
    if not llm['api_key']:
        logger.error('LLM key 未配置。任选其一：'
                     '① .env 加 LLM_API_KEY/LLM_BASE_URL/LLM_MODEL（GLM: https://open.bigmodel.cn/api/paas/v4, glm-4-flash 免费）；'
                     '② .env 填 DEEPSEEK_API_KEY')
        sys.exit(1)
    client = OpenAI(api_key=llm['api_key'], base_url=llm['base_url'])
    logger.info(f"情绪打分启动: model={llm['model']} prompt_ver={PROMPT_VER} "
                f"workers={args.workers} rescore={args.rescore}")

    db = get_sync_db()
    try:
        r = score_pending(db, client, llm['model'], limit=args.limit,
                          min_heat=args.min_heat, codes=codes or None,
                          workers=args.workers, rescore=args.rescore)
        if not r['total']:
            return
        stat = db.execute(text("""
            SELECT sentiment, COUNT(*) FROM xueqiu_sentiment GROUP BY 1 ORDER BY 1""")).fetchall()
        rate = r['req'] / r['elapsed'] * 60 if r['elapsed'] else 0
        logger.info(f"打分完成: done={r['done']} fail={r['fail']} 用时={r['elapsed']/60:.1f}min "
                    f"({rate:.1f} req/min) | 全表分布(看多/中性/看空)="
                    f"{ {x[0]: x[1] for x in stat} }")
    finally:
        db.close()


if __name__ == '__main__':
    main()
