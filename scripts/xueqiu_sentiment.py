"""雪球帖子 LLM 情绪打分（design/06 P3 前置试点）。

数据流：v_xueqiu_clean_status（已排除公告帖与水军）→ 未打分热帖 → LLM 三分类 → xueqiu_sentiment。

用法：
    venv/bin/python -m scripts.xueqiu_sentiment                 # 全市场热帖 300 条
    venv/bin/python -m scripts.xueqiu_sentiment --limit 500 --min-heat 10
    venv/bin/python -m scripts.xueqiu_sentiment --codes 600519,000002 --limit 100

情绪定义（A股语境）：
    1  = 看多（含调侃式看好、抄底意愿、利好解读）
    0  = 中性 / 闲聊 / 提问 / 与涨跌无关
    -1 = 看空（含愤怒式看空、割肉抱怨、利空解读）
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

PROMPT = """你是A股散户情绪标注员。对下面每条雪球帖子判断发帖者对该股的立场：
1=看多（看好、想买、抄底、利好解读、调侃式看好）
0=中性（陈述事实、提问、闲聊、与涨跌无关）
-1=看空（看空、想卖、割肉抱怨、利空解读、愤怒式唱空）

帖子（JSON 数组，id 为帖子标识）：
{posts}

只返回 JSON 数组，每项 {{"id": 帖子id, "s": 1或0或-1, "c": 0到1置信度, "r": "不超过15字的依据"}}，不要其它文字。"""

BATCH = 10           # 每次请求打包帖数（省 token；情绪三分类批量不损准确率）
RPM_LIMIT = 25       # 每分钟请求数上限（DeepSeek/GLM 通用保守值）
FAIL_MAX = 3


def fetch_pending(db, limit: int, min_heat: int, codes: list[str] | None):
    """取未打分帖子：干净帖视图 + 互动热度排序，热帖优先。"""
    code_filter = "AND s.code = ANY(:codes) " if codes else ""
    sql = text(f"""
        SELECT s.status_id, s.code, s.text_clean,
               COALESCE(s.like_count,0) + COALESCE(s.reply_count,0)*3 AS heat
        FROM v_xueqiu_clean_status s
        LEFT JOIN xueqiu_sentiment se ON se.status_id = s.status_id
        WHERE se.status_id IS NULL AND COALESCE(s.text_clean, '') != ''
          AND LENGTH(s.text_clean) BETWEEN 10 AND 800
          AND COALESCE(s.like_count,0) + COALESCE(s.reply_count,0)*3 >= :heat
          {code_filter}
        ORDER BY heat DESC LIMIT :l
    """)
    params = {'heat': min_heat, 'l': limit}
    if codes:
        params['codes'] = codes
    return db.execute(sql, params).fetchall()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--limit', type=int, default=300, help='本次最多打分帖数')
    ap.add_argument('--min-heat', type=int, default=5, help='最低互动热度（like+reply*3）')
    ap.add_argument('--codes', type=str, default='', help='逗号分隔股票码，空=全市场')
    args = ap.parse_args()
    codes = [c.strip() for c in args.codes.split(',') if c.strip()]

    llm = settings.llm_config
    if not llm['api_key']:
        logger.error('LLM key 未配置。任选其一：'
                     '① .env 加 LLM_API_KEY/LLM_BASE_URL/LLM_MODEL（GLM: https://open.bigmodel.cn/api/paas/v4, glm-4-flash 免费）；'
                     '② .env 填 DEEPSEEK_API_KEY')
        sys.exit(1)
    client = OpenAI(api_key=llm['api_key'], base_url=llm['base_url'])
    logger.info(f"情绪打分启动: model={llm['model']} base={llm['base_url']}")

    db = get_sync_db()
    rows = fetch_pending(db, args.limit, args.min_heat, codes)
    logger.info(f'待打分 {len(rows)} 帖（min_heat={args.min_heat}, codes={codes or "全市场"}）')
    if not rows:
        return

    done = fail = 0
    t_req: list[float] = []
    for i in range(0, len(rows), BATCH):
        batch = rows[i:i + BATCH]
        posts_json = json.dumps([{'id': r[0], 'code': r[1], 'text': r[2][:400]} for r in batch],
                                ensure_ascii=False)
        # RPM 限速：滑动 60s 窗口
        now = time.time()
        t_req[:] = [t for t in t_req if now - t < 60]
        if len(t_req) >= RPM_LIMIT:
            time.sleep(60 - (now - t_req[0]) + 0.5)
        t_req.append(time.time())

        ok = False
        for attempt in range(1, FAIL_MAX + 1):
            try:
                resp = client.chat.completions.create(
                    model=llm['model'],
                    messages=[{'role': 'user', 'content': PROMPT.replace('{posts}', posts_json)}],
                    temperature=0,
                    max_tokens=1500,
                )
                content = resp.choices[0].message.content.strip()
                content = content[content.find('['): content.rfind(']') + 1]
                results = json.loads(content)
                for it in results:
                    sid = int(it['id'])
                    if not any(r[0] == sid for r in batch):
                        continue   # LLM 幻觉 id 防御：只收本批的
                    s = int(it['s'])
                    if s not in (1, 0, -1):
                        continue
                    db.execute(text(
                        "INSERT INTO xueqiu_sentiment (status_id, sentiment, confidence, reason, model) "
                        "VALUES (:i, :s, :c, :r, :m) ON CONFLICT (status_id) DO NOTHING"),
                        {'i': sid, 's': s, 'c': float(it.get('c') or 0.5),
                         'r': str(it.get('r') or '')[:200], 'm': llm['model']})
                    db.commit()
                done += len(batch)
                ok = True
                break
            except Exception as e:
                logger.warning(f'批次 {i//BATCH+1} 第 {attempt} 次失败: {str(e)[:100]}')
                time.sleep(3 * attempt)
        if not ok:
            fail += len(batch)
            logger.error(f'批次 {i//BATCH+1} 连续 {FAIL_MAX} 次失败，跳过 {len(batch)} 帖')
        if (i // BATCH + 1) % 5 == 0:
            logger.info(f'进度 {done + fail}/{len(rows)} (done {done}, fail {fail})')

    # 汇总
    stat = db.execute(text("""
        SELECT sentiment, COUNT(*) FROM xueqiu_sentiment GROUP BY 1 ORDER BY 1""")).fetchall()
    logger.info(f'打分完成: done={done} fail={fail} | 全表分布(看多/中性/看空)='
                f'{ {r[0]: r[1] for r in stat} }')
    db.close()


if __name__ == '__main__':
    main()
