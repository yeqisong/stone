"""资讯 LLM 总结（design/07 第 2 层）——一次调用同出四字段：摘要 / 重要度 / 主题 / 情绪。

与雪球情绪打分同构（共用 scripts/llm_batch 的主循环与容错解析）：
- **无日期窗口，只取「未总结」的**：幂等、自愈、无需游标，某轮 LLM 不可用漏掉的
  下次运行自动补上（失败条目保持未总结状态）。
- 排序把关注池关联的排在前面：backlog 时先出与持仓/自选相关的。
- 情绪字段（1/0/-1）是为第 3 层「与雪球帖子交叉分析」预留的——同一批请求里顺带产出，
  不额外花调用；今日先只存与展示，是否特征化由 2026-10-08 后的 IC 体检门禁决定。

成本边界：数千条/天按 glm-4-flash 计约每天几毛，真正的约束是耗时（约 15-20 分钟），
分散在每轮 DAG 批次内跑，因此 max_items 只作单轮预算护栏、不作日限额。
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

# n1 → n2（2026-09-29）：加 id 硬约束（原样照抄、不合并同题条目）。改口径必升版本号，
# 否则新旧两套口径的标签混在一列里，第 3 层做 IC 体检时分不出来。
PROMPT_VER = 'n2'

# 注意：本模板用 .replace('{items}', ...) 取值，不是 .format()，故花括号**不需要转义**。
# 曾写成 {{...}}（沿用旧模板的转义习惯）——模型会把 few-shot 里的双花括号原样复制进输出，
# 产生 {{ "id": 1 }} 这种非法 JSON，整批解析失败（实测 40 条 0 解析）。单花括号即可。
PROMPT = """你是A股财经资讯编辑。逐条判断并输出，只返回 JSON 数组，每项形如
{"id":..,"s":"摘要","imp":2,"t":"主题,主题","sent":0}

字段规则：
- id：**原样照抄输入里每条的 id**，不得改写、不得省略——缺 id 的条目会被整条丢弃。
  输入几条就输出几条；**内容几乎相同的两条也要各出一条，严禁合并去重**
  （实测：研报类一批里多份同股同题报告，模型会自作主张合并并顺手丢掉 id 字段，
  整批 10 条回收 0 条，只能靠折半重试救回）。
- s：一句话摘要，≤60字，突出「谁、做了什么、对什么标的/行业有何影响」。
  必须落到**具体结论**（数字/主体/方向）；正文确实没有超出标题的信息时直接陈述该事实。
- t：主题标签 1-3 个，逗号分隔（如「固态电池,涨停」「减持,高管」「货币政策」）。
- sent：对该条涉及标的方向的影响 —— 1 利好 / 0 中性或无关 / -1 利空。
  仅在有明确方向时给 ±1；纯行情播报、数据罗列、行业综述给 0。

重要度 imp（按下列锚点判定，**不要一律给 2/3**）：
- 3：全市场级或个股重大。重大政策定调/监管新规、重大重组、业绩暴雷、立案处罚、
  指数纳入调出、龙头公司重大减持或回购。
- 2：行业级或有实质信息。个股研报与评级调整、行业研报中的明确判断、订单/中标、
  经营数据预告、公司定期报告/回购/分红、董事高管减持、诉讼与问询函。
- 1：例行与噪音。**行情播报与资金统计**（「XX概念涨/跌X%」「主力资金净流出N股」
  「N家公司获机构调研」「融资余额增加X亿」「N只股涨停」）、行业周报/例行跟踪报告
  （只有数据汇总无明确判断）、日常经营公告（担保进展、投资者关系记录表、
  股东会法律意见书）、无实质信息的评论。
  这类在快讯与个股新闻里占比很高，判 1 是正常的、也是期望的。

示例（照此风格，务必落到具体结论）：
- 标题「房地产行业第39周周报：本周楼市成交同环比均走弱；北京商品住房销售制度细则落地」，
  正文「行业：房地产开发｜机构：中银证券｜评级：增持」
  → {"s":"北京落地商品住房销售制度细则，本周楼市成交同环比双双走弱","imp":2,
      "t":"房地产,楼市成交","sent":-1}
- 标题「关于公司部分董事及高级管理人员股份减持计划的预披露公告」
  → {"s":"公司部分董监高预披露减持计划","imp":2,"t":"减持,高管","sent":-1}
- 标题「2026年半年度权益分派实施公告」
  → {"s":"公司实施2026年半年度权益分派","imp":2,"t":"分红","sent":1}
- 标题「固态电池概念再度拉升 武汉蓝电30cm涨停」
  → {"s":"固态电池概念盘中再度拉升，武汉蓝电30cm涨停","imp":1,"t":"固态电池,涨停","sent":1}
- 标题「央行：引导全国性银行、地方银行分赛道支持服务业扩能提质」
  → {"s":"央行要求银行分赛道支持服务业扩能提质","imp":2,"t":"货币政策,服务业","sent":1}
- 标题「86家公司获海外机构调研」
  → {"s":"近10日海外机构调研86家公司，广合科技最受关注","imp":1,"t":"机构调研","sent":0}
- 标题「关于为子公司提供担保的进展公告」
  → {"s":"公司披露为子公司提供担保的进展","imp":1,"t":"担保","sent":0}

严禁：只写「XX机构发布XX报告，评级X」而不说结论；给摘要加「关注XX变化/动态」式
空话尾缀；把标题原样复制进摘要。

注意：
- 素材含「src_level」（财联社自带 A/B/C 等级）可作参考但**不照搬**：C 级里也有
  重大信息，B/A 级也有例行播报，以内容本身判定。
- 研报类素材的正文是结构化标注行（机构/评级/EPS 预测）；**摘要的主语是标题里的
  结论，不是机构名**。
- 公告类素材只有标题，按公告类型判定（见上）。

待处理素材：
{items}"""

BATCH = 10            # 每请求打包条数（省 token；四分类批量不损准确率）
RPM_LIMIT = 25        # 每分钟请求数上限（滑动窗口闸门）
FAIL_MAX = 3
WORKERS = 8           # 实测单请求约 20-30s，8 并发约 17 rpm


FOCUS_LINKED = """EXISTS (SELECT 1 FROM news_stock ns JOIN v_focus_pool f
                              ON f.stock_code = ns.stock_code WHERE ns.news_id = n.id)"""


def fetch_pending(db, limit: int = 3000, min_len: int = 20, only_focus: bool = False):
    """取「未总结」的资讯。

    排序：**关联到关注池个股**的优先 → 再按发布时间倒序（新的先出，实时性优先）。
    注意不能简单写 EXISTS(news_stock)：财联社/东财的源自带股票标注是市场级的，
    随手就挂上关联，那样「优先」等于没优先；必须内连 v_focus_pool 才是真的关注池。
    无日期窗口，故 backlog 逐轮抽干（今日首跑约 5 万条待总结是正常起点）。
    """
    sql = f"""
        SELECT n.id, n.category, n.title, n.content, n.extra
        FROM news_item n
        LEFT JOIN news_summary s ON s.news_id = n.id
        WHERE s.news_id IS NULL
          AND n.content IS NOT NULL
          AND length(n.content) >= :min_len
    """
    params = {'min_len': max(1, int(min_len)), 'limit': max(1, int(limit))}
    if only_focus:
        sql += f" AND {FOCUS_LINKED}"
    sql += f"""
        ORDER BY {FOCUS_LINKED} DESC, n.published_at DESC
        LIMIT :limit
    """
    return db.execute(text(sql), params).fetchall()


def _payload(row) -> dict:
    """单条素材 → LLM 输入（标题 + 截断正文 + 源侧元数据）。"""
    nid, category, title, content, extra = row
    if isinstance(extra, str):
        try:
            extra = json.loads(extra)
        except Exception:
            extra = {}
    extra = extra or {}
    item = {'id': nid, 'cat': category, 'title': (title or '')[:200],
            'text': (content or '')[:1200]}
    if extra.get('src_level'):
        item['src_level'] = extra['src_level']
    if extra.get('score'):
        item['score'] = extra['score']
    if extra.get('org') or extra.get('rating'):
        item['report'] = {k: extra.get(k) for k in ('org', 'rating', 'industry', 'eps')
                          if extra.get(k)}
    if category == 'notice' and extra.get('type'):
        item['notice_type'] = extra['type']
    return item


def _call_batch(client, model: str, batch, fail_max: int):
    """单批总结。成功返回 [(news_id, summary, importance, topics, sentiment), ...]。"""
    from scripts.llm_batch import llm_json_batch, pick_by_ids
    payload = json.dumps([_payload(r) for r in batch], ensure_ascii=False)
    arr = llm_json_batch(client, model, PROMPT.replace('{items}', payload),
                         fail_max=fail_max, max_tokens=2000, log_tag='news')
    if arr is None:
        return None
    out = []
    for it in pick_by_ids(arr, [r[0] for r in batch]):
        try:
            nid = int(it['id'])
            summary = str(it['s']).strip()[:400]
            imp = int(it['imp'])
        except (KeyError, TypeError, ValueError):
            continue
        if not summary:
            continue
        imp = min(3, max(1, imp))
        try:
            sent = int(it.get('sent'))
        except (TypeError, ValueError):
            sent = None
        if sent not in (1, 0, -1):
            sent = None
        topics = str(it.get('t') or '').strip()[:200]
        out.append((nid, summary, imp, topics, sent))
    if len(out) < len(batch):
        # 回收不全必须留痕：模型回了多少条、首条是什么样（id 对不上/字段名不同是最常见的死法，
        # 只看「失败 N 条」无法区分「模型没回」和「回了但 id 不匹配」）
        sample = arr[0] if arr and isinstance(arr[0], dict) else arr[:1]
        logger.warning(f'[news] 本批 {len(batch)} 条仅解析出 {len(out)} 条'
                       f'（模型返回 {len(arr)} 条，首条 {str(sample)[:150]}）')
    return out


def summarize_batch(client, model: str, batch, fail_max: int = FAIL_MAX):
    """单批总结（纯 LLM 调用，不碰 DB——供线程池并发）。整批失败折半重试。"""
    from scripts.llm_batch import retry_split
    return retry_split(lambda b: _call_batch(client, model, b, fail_max), batch,
                       log_tag='news')


def run_summarize(db, client, model: str, rows, *, workers: int = WORKERS,
                  rpm: int = RPM_LIMIT, fail_max: int = FAIL_MAX, batch: int = BATCH,
                  progress_cb=None):
    """总结主循环。CLI 与 DAG 节点共用，返回 {'done','fail','total','req','elapsed'}。"""
    from scripts.llm_batch import run_batches
    sql = text("""INSERT INTO news_summary
                      (news_id, summary, importance, topics, sentiment, model, prompt_ver)
                  VALUES (:i, :s, :imp, :t, :sent, :m, :pv)
                  ON CONFLICT (news_id) DO NOTHING""")

    def _write(b, results):
        for nid, summary, imp, topics, sent in results:
            db.execute(sql, {'i': nid, 's': summary, 'imp': imp, 't': topics,
                             'sent': sent, 'm': model, 'pv': PROMPT_VER})
        db.commit()

    return run_batches(rows, lambda b: summarize_batch(client, model, b, fail_max), _write,
                       workers=workers, rpm=rpm, batch_size=batch,
                       progress_cb=progress_cb, log_tag='news')


def summarize_pending(db, client, model: str, *, limit: int = 3000, min_len: int = 20,
                      only_focus: bool = False, workers: int = WORKERS,
                      rpm: int = RPM_LIMIT, progress_cb=None):
    """取待总结资讯 + 执行总结（CLI 与 DAG 节点共用入口）。"""
    rows = fetch_pending(db, limit, min_len, only_focus)
    logger.info(f'待总结 {len(rows)} 条（min_len={min_len}, prompt_ver={PROMPT_VER}）')
    return run_summarize(db, client, model, rows, workers=workers, rpm=rpm,
                         progress_cb=progress_cb)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--limit', type=int, default=3000, help='本次最多总结条数')
    ap.add_argument('--min-len', type=int, default=20, help='正文最短长度阈值')
    ap.add_argument('--only-focus', action='store_true', help='只总结有关联个股的')
    ap.add_argument('--workers', type=int, default=WORKERS)
    ap.add_argument('--dry-run', action='store_true', help='只统计待总结条数')
    args = ap.parse_args()

    db = get_sync_db()
    try:
        if args.dry_run:
            rows = fetch_pending(db, args.limit, args.min_len, args.only_focus)
            print(f'待总结 {len(rows)} 条（limit={args.limit}）')
            for r in rows[:5]:
                print(f'  [{r[1]}] {(r[2] or "")[:50]}')
            return
        llm = settings.llm_config
        if not llm['api_key']:
            logger.error('LLM key 未配置（.env LLM_API_KEY 或 DEEPSEEK_API_KEY）')
            sys.exit(1)
        client = OpenAI(api_key=llm['api_key'], base_url=llm['base_url'])
        logger.info(f"资讯总结启动: model={llm['model']} prompt_ver={PROMPT_VER} "
                    f"limit={args.limit} workers={args.workers}")
        r = summarize_pending(db, client, llm['model'], limit=args.limit,
                              min_len=args.min_len, only_focus=args.only_focus,
                              workers=args.workers)
        logger.info(f"总结完成: done={r['done']} fail={r['fail']} "
                    f"用时={r['elapsed'] / 60:.1f}min "
                    f"{r['req'] / max(r['elapsed'], 1) * 60:.1f} req/min")
    finally:
        db.close()


if __name__ == '__main__':
    main()
