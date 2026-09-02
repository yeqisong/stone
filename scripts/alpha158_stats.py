"""Alpha158 特征完整度统计刷新（全量计算完成后执行）。

逐个调用 _update_feature_stats_after_compute（与页面「重算诊断」同函数）：
完整度 = 该因子 feature_values 行数 / entity_stats(stock) 总格子。
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main():
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.env'))
    from sqlalchemy import create_engine, text
    from app.api.features import _update_feature_stats_after_compute

    eng = create_engine(os.environ['DATABASE_URL_SYNC'])
    with eng.connect() as c:
        rows = c.execute(text(
            "SELECT id, feature_name FROM features WHERE feature_name LIKE 'a158\\_%' "
            "ORDER BY feature_name")).fetchall()
    print(f'刷新 {len(rows)} 个 a158 因子统计…')
    for fid, fn in rows:
        _update_feature_stats_after_compute(fid, force_recompute=True)
    # 汇总
    with eng.connect() as c:
        st = c.execute(text(
            "SELECT data_completeness, COUNT(*) FROM features "
            "WHERE feature_name LIKE 'a158\\_%' GROUP BY CASE "
            "WHEN data_completeness >= 0.9 THEN 0.9 WHEN data_completeness >= 0.6 THEN 0.6 "
            "ELSE 0 END ORDER BY 1 DESC")).fetchall()
        sample = c.execute(text(
            "SELECT feature_name, total_effective_cells, data_completeness, latest_computed_date "
            "FROM features WHERE feature_name IN ('a158_KMID','a158_MA_5','a158_CORR_20','a158_RSQR_60') "
            "ORDER BY feature_name")).fetchall()
    for r in st:
        print(f'  完整度 {r[0]}: {r[1]} 个')
    for r in sample:
        print(f'  {r[0]}: {r[1]:,} 格 完整度 {r[2]} 最新 {r[3]}')


if __name__ == '__main__':
    main()
