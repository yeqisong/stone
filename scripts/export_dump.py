#!/usr/bin/env python3
"""导出 PostgreSQL 数据为 SQL 压缩包。

⚠️ 已弃用：项目已统一使用 PostgreSQL，此脚本仅用于历史数据迁移。
如需使用，请确保目标环境为 PostgreSQL。
"""
import sys, os, gzip, re, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db.connection import get_sync_db
from sqlalchemy import text

OUT_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'stock_monitor_dump.sql.gz')

TABLES = ['trade_calendar', 'stock_master', 'daily_quote', 'stock_fundamentals',
          'signal_history', 'strategy_config', 'portfolio', 'portfolio_history', 'system_metrics']


def export():
    db = get_sync_db()

    with gzip.open(OUT_PATH, 'wt', compresslevel=9, encoding='utf-8') as f:
        f.write('-- Stock Monitor Data Export\n')
        f.write('-- PostgreSQL 15+ compatible\n\n')
        f.write('BEGIN;\n\n')

        for table in TABLES:
            ok = db.execute(text(f"SELECT table_name FROM information_schema.tables WHERE table_name='{table}'")).fetchone()
            if not ok:
                f.write(f'-- {table}: skipped\n\n')
                continue

            # ── Schema ── (PG 15+ native)
            f.write(f'-- {table}: schema from information_schema\n')
            # 从 information_schema 获取列定义
            cols = db.execute(text(f"""
                SELECT column_name, data_type, character_maximum_length,
                       numeric_precision, numeric_scale, is_nullable,
                       column_default
                FROM information_schema.columns
                WHERE table_name = '{table}'
                ORDER BY ordinal_position
            """)).fetchall()
            raw_sql = f'CREATE TABLE IF NOT EXISTS {table} (\n'
            col_defs = []
            for c in cols:
                dt = c[1]
                if c[2]: dt += f'({c[2]})'
                elif c[3] and c[4]: dt += f'({c[3]},{c[4]})'
                null_str = ' NOT NULL' if c[5] == 'NO' else ''
                def_str = f' DEFAULT {c[6]}' if c[6] and 'nextval' not in str(c[6]) else ''
                col_defs.append(f'    {c[0]} {dt}{null_str}{def_str}')
            raw_sql += ',\n'.join(col_defs) + '\n);'

            # Fixes for PG compatibility
            raw_sql = raw_sql.replace('AUTOINCREMENT', '')
            raw_sql = raw_sql.replace('NUMERIC(', 'DECIMAL(')
            raw_sql = raw_sql.replace('DATETIME', 'TIMESTAMP')
            raw_sql = re.sub(r'(stock_name\s+)VARCHAR\(20\)', r'\1VARCHAR(30)', raw_sql)
            raw_sql = re.sub(r'(direction\s+)VARCHAR\(6\)', r'\1VARCHAR(7)', raw_sql)

            f.write(f'DROP TABLE IF EXISTS {table} CASCADE;\n')
            f.write(f'{raw_sql};\n')

            # ── Data ──
            # 从 information_schema 获取列信息
            col_info = db.execute(text(f"""
                SELECT column_name, data_type FROM information_schema.columns
                WHERE table_name = '{table}' ORDER BY ordinal_position
            """)).fetchall()
            col_names = [ci[0] for ci in col_info]
            col_types = {ci[0]: (ci[1] or '').upper() for ci in col_info}

            result = db.execute(text(f'SELECT * FROM {table}'))
            rows = result.fetchall()
            if not rows:
                f.write(f'\n-- {table}: 0 rows\n\n')
                continue

            # Skip 'id' if all values are NULL → let PG SERIAL generate
            skip_id = False
            if 'id' in col_names:
                id_idx = col_names.index('id')
                all_null = all(row[id_idx] is None for row in rows)
                if all_null:
                    skip_id = True

            cols = [c for c in col_names if not (skip_id and c == 'id')]
            id_idx_orig = col_names.index('id') if 'id' in col_names else -1

            f.write(f'\n-- {table}: {len(rows)} rows\n')

            batch_size = 200
            for i in range(0, len(rows), batch_size):
                batch = rows[i:i + batch_size]
                values_list = []
                for row in batch:
                    vals = []
                    for j, v in enumerate(row):
                        col_name = col_names[j]
                        if skip_id and col_name == 'id':
                            continue
                        col_type = col_types.get(col_name, '')
                        if v is None:
                            vals.append('NULL')
                        elif 'BOOL' in col_type:
                            vals.append('true' if v else 'false')
                        elif isinstance(v, bool):
                            vals.append('true' if v else 'false')
                        elif isinstance(v, (int, float)):
                            vals.append(str(v))
                        else:
                            s = str(v).replace("'", "''")
                            # Truncate if needed (shouldn't happen with fixed schema)
                            m = re.search(r'VARCHAR\((\d+)\)', col_type)
                            if m and 'name' in col_name.lower() and int(m.group(1)) == 20:
                                limit = 30  # already fixed in schema, but safety net
                            elif m:
                                limit = int(m.group(1))
                            else:
                                limit = 999
                            if len(s) > limit:
                                s = s[:limit]
                            vals.append(f"E'{s}'")
                    values_list.append(f"({', '.join(vals)})")

                col_str = ', '.join(f'"{c}"' for c in cols)
                f.write(f'INSERT INTO {table} ({col_str}) VALUES\n')
                f.write(',\n'.join(values_list))
                f.write(';\n\n')

            print(f'  {table}: {len(rows)} rows')

        f.write('COMMIT;\n')

    db.close()
    size_mb = os.path.getsize(OUT_PATH) / 1024 / 1024
    print(f'\nDone: {OUT_PATH} ({size_mb:.0f} MB)')


if __name__ == '__main__':
    export()
