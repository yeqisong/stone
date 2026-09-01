"""一次性：8 个基本面特征从 2000 年补算（fund_history 回填完成后）。

ep_ttm/bp_mrq 已有 2016-08 起 10 年，只补 2000~2016-07；
其余 6 个（dv_ttm_f/ps_inv/size_inv/turnover_rate_f/turnover_rev/vr_sentiment）已有 2025-03 起，
只补 2000~2025-03。

compute_feature 分片模式（30 天/片）逐片 fetch→compute→DELETE+COPY 幂等重写，
可随时中断续跑（已写片保留）。
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from loguru import logger

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))
ENG = create_engine(os.getenv("DATABASE_URL_SYNC"))

PLAN = [
    (["ep_ttm", "bp_mrq"], "2000-01-01", "2016-07-31"),
    (["dv_ttm_f", "ps_inv", "size_inv", "turnover_rate_f", "turnover_rev", "vr_sentiment"],
     "2000-01-01", "2025-03-04"),
]


def run_batch(names, start, end):
    from scripts.feature_compute import compute_feature
    from sqlalchemy.orm import Session
    with Session(ENG) as db:  # compute_feature 内部写入用 db.get_bind().raw_connection()，需 Session
        rows = db.execute(text(
            "SELECT feature_name, formula FROM features "
            "WHERE feature_name = ANY(:names) AND status='enabled'"
        ), {"names": names}).fetchall()
        for fn, formula in rows:
            t0 = time.time()
            res = compute_feature(db, fn, formula, "stock", start_date=start, end_date=end)
            if res.get("ok"):
                logger.info(f"[feat] {fn} {start}~{end}: rows={res.get('rows')} chunks={res.get('chunks')} {time.time()-t0:.0f}s")
            else:
                logger.error(f"[feat] {fn} {start}~{end} 失败: {res.get('error')}")


if __name__ == "__main__":
    for names, start, end in PLAN:
        run_batch(names, start, end)
    with ENG.connect() as c:
        r = c.execute(text("""
            SELECT f.feature_name, MIN(fv.trade_date), MAX(fv.trade_date), COUNT(*)
            FROM features f JOIN feature_values fv ON fv.feature_name = f.feature_name
            WHERE f.feature_name = ANY(:names) GROUP BY f.feature_name ORDER BY 1
        """), {"names": [n for n, _, _ in PLAN for n in n]}).fetchall()
        for x in r:
            logger.info(f"[feat] 完成校验 {x[0]}: {x[1]}~{x[2]} {x[3]}行")
