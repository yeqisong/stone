"""数据库浏览器（只读）— 数据 tab 页后端。

借鉴 Adminer/pgweb 的表浏览模式：库概览 → 表清单（行数/大小估算）→
字段元数据 + 分页数据。只生成 SELECT，表名/列名经白名单校验，不提供任何写入。
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text

from app.db.connection import get_sync_db
from app.auth.auth import get_current_user

router = APIRouter(prefix="/dbex", tags=["dbex"])

_TABLE_NAME_RE = __import__("re").compile(r"^[a-z_][a-z0-9_]*$")


def _assert_table(db, table: str) -> None:
    """表名必须真实存在于 public schema 且符合命名规则（防注入：标识符无法参数化）。"""
    if not _TABLE_NAME_RE.match(table or ""):
        raise HTTPException(400, "非法表名")
    exists = db.execute(text(
        "SELECT 1 FROM information_schema.tables "
        "WHERE table_schema='public' AND table_type='BASE TABLE' AND table_name=:n"
    ), {"n": table}).scalar()
    if not exists:
        raise HTTPException(404, f"表不存在: {table}")


@router.get("/overview")
def overview(user: str = Depends(get_current_user)):
    """库概览：库名/大小/PG 版本/表数量。"""
    db = get_sync_db()
    try:
        name = db.execute(text("SELECT current_database()")).scalar()
        size = db.execute(text("SELECT pg_size_pretty(pg_database_size(current_database()))")).scalar()
        ver = db.execute(text("SHOW server_version")).scalar()
        n_tables = db.execute(text(
            "SELECT COUNT(*) FROM information_schema.tables "
            "WHERE table_schema='public' AND table_type='BASE TABLE'")).scalar()
        db.close()
        return {"database": name, "size": size, "version": ver, "n_tables": n_tables}
    finally:
        try:
            db.close()
        except Exception:
            pass


@router.get("/tables")
def list_tables(user: str = Depends(get_current_user)):
    """public 下全部用户表：估算行数 + 磁盘大小 + 注释。"""
    db = get_sync_db()
    try:
        rows = db.execute(text("""
            SELECT c.relname,
                   GREATEST(c.reltuples, 0)::bigint AS rows_est,
                   pg_size_pretty(pg_total_relation_size(c.oid)) AS size,
                   COALESCE(obj_description(c.oid), '') AS comment
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = 'public' AND c.relkind = 'r'
            ORDER BY pg_total_relation_size(c.oid) DESC
        """)).fetchall()
        db.close()
        return {"tables": [
            {"name": r[0], "rows_est": max(int(r[1]), 0), "size": r[2], "comment": r[3]}
            for r in rows
        ]}
    finally:
        try:
            db.close()
        except Exception:
            pass


@router.get("/tables/{table}/columns")
def table_columns(table: str, user: str = Depends(get_current_user)):
    """表字段元数据：列名/类型/可空/默认/注释。"""
    db = get_sync_db()
    try:
        _assert_table(db, table)
        rows = db.execute(text("""
            SELECT a.attname, format_type(a.atttypid, a.atttypmod),
                   NOT a.attnotnull,
                   COALESCE(pg_get_expr(ad.adbin, ad.adrelid), ''),
                   COALESCE(col_description(c.oid, a.attnum), '')
            FROM pg_attribute a
            JOIN pg_class c ON c.oid = a.attrelid
            JOIN pg_namespace n ON n.oid = c.relnamespace
            LEFT JOIN pg_attrdef ad ON ad.adrelid = a.attrelid AND ad.adnum = a.attnum
            WHERE n.nspname = 'public' AND c.relname = :t
              AND a.attnum > 0 AND NOT a.attisdropped
            ORDER BY a.attnum
        """), {"t": table}).fetchall()
        db.close()
        return {"columns": [
            {"name": r[0], "type": r[1], "nullable": r[2], "default": r[3], "comment": r[4]}
            for r in rows
        ]}
    finally:
        try:
            db.close()
        except Exception:
            pass


@router.get("/tables/{table}/rows")
def table_rows(table: str, offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=200),
               order_by: str = Query(None), order_dir: str = Query("asc", pattern="^(asc|desc)$"),
               user: str = Depends(get_current_user)):
    """分页查看表数据（内部生成 SELECT，只读）。"""
    db = get_sync_db()
    try:
        _assert_table(db, table)
        col_rows = db.execute(text("""
            SELECT a.attname FROM pg_attribute a
            JOIN pg_class c ON c.oid = a.attrelid
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = 'public' AND c.relname = :t
              AND a.attnum > 0 AND NOT a.attisdropped ORDER BY a.attnum
        """), {"t": table}).fetchall()
        valid_cols = [r[0] for r in col_rows]

        order_sql = ""
        if order_by:
            if order_by not in valid_cols:
                raise HTTPException(400, f"非法排序列: {order_by}")
            order_sql = f" ORDER BY {order_by} {order_dir} NULLS LAST"

        total = db.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar()
        rows = db.execute(text(
            f"SELECT * FROM {table}{order_sql} LIMIT :lim OFFSET :off"
        ), {"lim": limit, "off": offset}).mappings().all()
        db.close()
        # Decimal/date/datetime/JSONB 由 FastAPI jsonable_encoder 统一序列化
        return {"total": total, "offset": offset, "limit": limit,
                "columns": valid_cols, "rows": [dict(r) for r in rows]}
    finally:
        try:
            db.close()
        except Exception:
            pass
