"""交割单导入与持仓重建。

职责边界：
- 解析券商导出的成交明细（TSV/CSV/Excel，GBK/UTF-8），列名做同义词映射；
- 幂等入库（成交编号 + 行哈希双去重），重复导入不会产生脏数据；
- 按交割单重建持仓（数量以券商「股份余额」为唯一事实源，成本用移动加权）；
- 与 portfolio 表做差异预览，用户确认后才同步。

复盘指标（收益/回撤/行为画像）在 app/trade_review.py，不放在本模块。
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date as _date, datetime
from typing import Any, Optional

from loguru import logger
from sqlalchemy import text

# ── 表头同义词（覆盖常见券商导出；比较前统一去空格/括号/大小写）──

HEADER_ALIASES = {
    "date": ["成交日期", "交易日期", "发生日期", "成交日", "日期"],
    "time": ["成交时间", "交易时间", "委托时间", "时间"],
    "code": ["证券代码", "股票代码", "证券编码", "代码", "证券编号"],
    "name": ["证券名称", "股票名称", "证券简称", "名称", "股票简称"],
    "side": ["买卖标志", "买卖方向", "买卖类别", "操作", "交易方向", "买卖"],
    "qty": ["成交数量", "成交股数", "成交量", "数量", "股数"],
    "price": ["成交价格", "成交均价", "成交价", "价格", "均价"],
    "amount": ["成交金额", "成交额", "金额"],
    "balance": ["股份余额", "股票余额", "证券余额", "持仓数量", "股份数量", "余额数量"],
    "clean": ["清算金额", "发生金额", "净金额", "结算金额", "清算额"],
    "commission": ["手续费", "佣金", "交易手续费"],
    "stamp": ["印花税"],
    "transfer": ["过户费"],
    "extra": ["附加费", "其他费用", "规费", "其它费"],
    "cash": ["资金本次余额", "资金余额", "现金余额", "可用资金", "资金帐户余额"],
    "order_no": ["成交编号", "成交序号", "成交单号", "合同编号"],
    "account": ["资金帐号", "资金账号", "股东帐号", "股东账号", "股东代码", "客户代码"],
    "exchange": ["交易所名称", "交易市场", "交易所"],
    "biz": ["业务名称", "业务类型", "摘要", "操作类型", "业务标志"],
}

MONEY_KEYS = ("amount", "clean", "commission", "stamp", "transfer", "extra", "cash")
NUM_KEYS = ("qty", "price", "balance") + MONEY_KEYS


class TradeParseError(ValueError):
    """交割单无法解析（面向用户的中文错误）。"""


def _norm_key(s: Any) -> str:
    s = str(s or "").replace("\ufeff", "")
    s = re.sub(r"[\s\u3000（）()【】\[\]]+", "", s)
    return s.lower()


_ALIAS_LOOKUP: dict[str, str] = {}
for _canon, _names in HEADER_ALIASES.items():
    for _n in _names:
        _ALIAS_LOOKUP[_norm_key(_n)] = _canon


def _norm_text(s: Any) -> str:
    return str(s or "").replace("\ufeff", "").strip()


def _to_float(v: Any) -> Optional[float]:
    """券商数字：'1,234.50' / '--' / '' / Decimal / int / 全角负号。"""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).replace("\ufeff", "").replace(",", "").replace("，", "").strip()
    s = s.replace("−", "-").replace("－", "-")
    if s in ("", "-", "--", "—", "N/A", "n/a"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _to_date(v: Any) -> Optional[_date]:
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, _date):
        return v
    # Excel 序列日期（xls 未套日期格式时）
    if isinstance(v, (int, float)) and 20000 < float(v) < 80000:
        try:
            import xlrd
            return xlrd.xldate_as_datetime(float(v), 0).date()
        except Exception:
            return None
    s = str(v).replace("\ufeff", "").strip()
    if not s:
        return None
    s = s.split(" ")[0].split("T")[0]
    s = s.replace("/", "-").replace(".", "-")
    for fmt in ("%Y-%m-%d", "%Y%m%d", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    digits = re.sub(r"\D", "", s)
    if len(digits) == 8:
        try:
            return datetime.strptime(digits, "%Y%m%d").date()
        except ValueError:
            return None
    return None


def _to_time(v: Any) -> str:
    if v is None or v == "":
        return ""
    if isinstance(v, datetime):
        return v.strftime("%H:%M:%S")
    if isinstance(v, (int, float)) and 0 <= float(v) < 1:
        secs = round(float(v) * 86400)
        return f"{secs // 3600:02d}:{(secs % 3600) // 60:02d}:{secs % 60:02d}"
    try:
        from datetime import time as _time
        if isinstance(v, _time):
            return v.strftime("%H:%M:%S")
    except Exception:
        pass
    s = str(v).strip()
    digits = re.sub(r"\D", "", s)
    if len(digits) >= 6:
        return f"{digits[0:2]}:{digits[2:4]}:{digits[4:6]}"
    if len(digits) == 4:
        return f"{digits[0:2]}:{digits[2:4]}:00"
    return s[-8:] if len(s) > 8 else s


def _normalize_code(v: Any) -> str:
    s = re.sub(r"\D", "", str(v or ""))
    if not s:
        return ""
    return s[-6:].zfill(6) if len(s) <= 6 else s


def _normalize_side(raw: Any, biz: Any = "") -> tuple[str, str]:
    """返回 (side, subtype)。side ∈ buy/sell/other。"""
    s = _norm_text(raw)
    b = _norm_text(biz)
    joined = f"{s} {b}"
    if any(k in joined for k in ("买入", "买进", "申购中签", "配股缴款")):
        return "buy", ""
    if any(k in joined for k in ("卖出", "赎回")):
        return "sell", ""
    if any(k in joined for k in ("送股", "红股", "转股")):
        return "other", "bonus"
    if "税" in joined:
        return "other", "tax"
    if any(k in joined for k in ("红利", "股息", "派息", "利息")):
        return "other", "dividend"
    return "other", "other"


def _fee_of(row: dict) -> float:
    """交易费用合计。兼容解析阶段的短键与入库后的 *_tax/*_fee 键。"""
    return sum(float(row.get(k) or 0) for k in (
        "commission", "stamp_tax", "stamp", "transfer_fee", "transfer",
        "extra_fee", "extra",
    ))


def _usable_order_no(v: Any) -> str:
    """券商对红利/送股行常写 '0'，这类值不能进唯一键（同日多行会互相冲突）。"""
    s = _norm_text(v)
    if not s or s in ("0", "0.0", "000000"):
        return ""
    if not re.search(r"[1-9A-Za-z]", s):
        return ""
    return s[:40]


def _row_hash(row: dict) -> str:
    """内容指纹。红利/送股行不纳入时间：这类行的时间常被券商写成 0，
    归一化后若改变会导致重复导入被误判为新记录。"""
    parts = [
        str(row.get("account_no") or ""),
        str(row.get("trade_date") or ""),
        str(row.get("stock_code") or ""),
        str(row.get("side") or ""),
        f"{float(row.get('quantity') or 0):.4f}",
        f"{float(row.get('price') or 0):.4f}",
        f"{float(row.get('amount') or 0):.2f}",
        str(row.get("business_name") or ""),
        f"{float(row.get('clean_amount') or 0):.2f}",
    ]
    if row.get("side") != "other":
        parts.insert(2, str(row.get("trade_time") or ""))
    return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()


# ── 文件解码 / 表格读取 ──

def _decode_text(data: bytes) -> tuple[str, str]:
    if data.startswith(b"\xef\xbb\xbf"):
        return data.decode("utf-8-sig"), "utf-8-sig"
    for enc in ("utf-8", "gb18030"):
        try:
            return data.decode(enc), enc
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace"), "utf-8-replace"


def _read_table(data: bytes) -> tuple[list[list[Any]], str]:
    """返回 (二维表, 格式描述)。支持 xlsx / xls / 文本。"""
    if data[:4] == b"PK\x03\x04":
        try:
            import openpyxl
            wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
            ws = wb[wb.sheetnames[0]]
            rows = [list(r) for r in ws.iter_rows(values_only=True)]
            wb.close()
            return rows, "xlsx"
        except Exception as e:
            raise TradeParseError(f"Excel(xlsx) 读取失败：{str(e)[:120]}")
    if data[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":
        try:
            import xlrd
            wb = xlrd.open_workbook(file_contents=data)
            sh = wb.sheet_by_index(0)
            rows = [sh.row_values(i) for i in range(sh.nrows)]
            return rows, "xls"
        except Exception as e:
            raise TradeParseError(f"Excel(xls) 读取失败：{str(e)[:120]}（可另存为 CSV 再导入）")
    text, enc = _decode_text(data)
    if not text.strip():
        raise TradeParseError("文件内容为空")
    # 分隔符：表头里出现最多的那个
    head = text.splitlines()[0]
    counts = {d: head.count(d) for d in ("\t", ",", ";", "|")}
    delim = max(counts, key=counts.get) if max(counts.values()) > 0 else "\t"
    rows = list(csv.reader(io.StringIO(text), delimiter=delim))
    del_name = {"\t": "tab", ",": "comma", ";": "semicolon", "|": "pipe"}.get(delim, delim)
    return rows, f"text/{enc}/{del_name}"


def _map_headers(row: list[Any]) -> dict[str, int]:
    out: dict[str, int] = {}
    for i, cell in enumerate(row):
        canon = _ALIAS_LOOKUP.get(_norm_key(cell))
        if canon and canon not in out:
            out[canon] = i
    return out


def _find_header(rows: list[list[Any]], max_scan: int = 40) -> tuple[int, dict[str, int]]:
    for i, row in enumerate(rows[:max_scan]):
        m = _map_headers(row)
        if "date" in m and ("code" in m or "qty" in m):
            return i, m
    # 给出最像的一行帮助报错
    best = max(rows[:max_scan], key=lambda r: len(_map_headers(r)), default=[])
    raise TradeParseError(
        "未识别到交割单表头：至少需要「成交日期/证券代码/成交数量」等列。"
        f"首行识别到 {len(_map_headers(best))} 个已知列"
    )


def parse_slip_bytes(data: bytes) -> dict:
    """解析交割单字节流，返回 {rows, meta}。不落库。"""
    if not data:
        raise TradeParseError("文件为空")
    table, fmt = _read_table(data)
    if not table:
        raise TradeParseError("文件没有可解析的行")
    header_idx, col = _find_header(table)

    rows: list[dict] = []
    invalid: list[dict] = []
    skipped = 0
    for line_no, raw_row in enumerate(table[header_idx + 1:], start=header_idx + 2):
        if not raw_row or not any(_norm_text(c) for c in raw_row):
            skipped += 1
            continue
        # 中途重复表头
        _mid_header = _map_headers(raw_row)
        if len(_mid_header) >= 3 and "date" in _mid_header:
            skipped += 1
            continue

        def cell(key: str):
            i = col.get(key)
            return raw_row[i] if i is not None and i < len(raw_row) else None

        d = _to_date(cell("date"))
        code = _normalize_code(cell("code"))
        if d is None or not code:
            # 合计/小计/说明行：静默跳过；其他记录到 invalid
            raw_txt = " ".join(_norm_text(c) for c in raw_row[:4])
            if any(k in raw_txt for k in ("合计", "小计", "总计", "说明", "备注")):
                skipped += 1
                continue
            invalid.append({"line": line_no, "reason": "日期或证券代码无法识别", "raw": raw_txt[:120]})
            continue

        side, subtype = _normalize_side(cell("side"), cell("biz"))
        qty = _to_float(cell("qty")) or 0.0
        price = _to_float(cell("price")) or 0.0
        amount = _to_float(cell("amount"))
        if amount is None:
            amount = qty * price
        balance = _to_float(cell("balance"))
        clean = _to_float(cell("clean")) or 0.0
        row = {
            "account_no": _norm_text(cell("account"))[:32],
            "trade_date": d,
            "trade_time": _to_time(cell("time")),
            "stock_code": code,
            "stock_name": _norm_text(cell("name"))[:32] or code,
            "side": side,
            "subtype": subtype,
            "business_name": _norm_text(cell("biz"))[:40],
            "quantity": abs(qty),
            "price": abs(price),
            "amount": abs(amount),
            "balance_qty": balance,
            "clean_amount": clean,
            "commission": abs(_to_float(cell("commission")) or 0.0),
            "stamp_tax": abs(_to_float(cell("stamp")) or 0.0),
            "transfer_fee": abs(_to_float(cell("transfer")) or 0.0),
            "extra_fee": abs(_to_float(cell("extra")) or 0.0),
            "cash_balance": _to_float(cell("cash")),
            "order_no": _usable_order_no(cell("order_no")),
            "exchange_name": _norm_text(cell("exchange"))[:20],
        }
        if side in ("buy", "sell") and row["quantity"] <= 0:
            invalid.append({"line": line_no, "reason": f"{side} 数量为 0", "raw": f"{code} {row['stock_name']}"})
            continue
        # 红利/送股行的成交时间常写成 0；统一排到当日最后，避免现金余额错位
        if side == "other" and row["trade_time"] in ("", "0", "0.0", "00:00:00"):
            row["trade_time"] = "23:59:59"
        row["row_hash"] = _row_hash(row)
        rows.append(row)

    if not rows:
        raise TradeParseError(
            f"未解析到有效成交记录（跳过 {skipped} 行，无效 {len(invalid)} 行）。"
            "请确认文件是券商导出的「交割单/成交明细」。"
        )
    rows.sort(key=lambda r: (r["trade_date"], r["trade_time"] or "00:00:00"))
    dates = [r["trade_date"] for r in rows]
    meta = {
        "format": fmt,
        "header_line": header_idx + 1,
        "columns": {k: str(v) for k, v in col.items()},
        "missing_columns": [k for k in ("qty", "price", "balance", "cash") if k not in col],
        "rows_total": len(rows),
        "rows_invalid": len(invalid),
        "rows_skipped": skipped,
        "invalid_samples": invalid[:20],
        "date_min": min(dates).isoformat(),
        "date_max": max(dates).isoformat(),
        "accounts": sorted({r["account_no"] for r in rows if r["account_no"]}),
    }
    return {"rows": rows, "meta": meta}


# ── 入库 ──

_INSERT_SQL = text("""
INSERT INTO trade_slip (
    user_id, account_no, trade_date, trade_time, stock_code, stock_name, side, business_name,
    quantity, price, amount, balance_qty, clean_amount, commission, stamp_tax,
    transfer_fee, extra_fee, cash_balance, order_no, exchange_name,
    source_file, import_batch_id, row_hash, raw
) VALUES (
    :user_id, :account_no, :trade_date, :trade_time, :stock_code, :stock_name, :side, :business_name,
    :quantity, :price, :amount, :balance_qty, :clean_amount, :commission, :stamp_tax,
    :transfer_fee, :extra_fee, :cash_balance, :order_no, :exchange_name,
    :source_file, :import_batch_id, :row_hash, CAST(:raw AS jsonb)
) ON CONFLICT DO NOTHING
""")


def import_slip(db, filename: str, data: bytes, user_id: int) -> dict:
    """解析并幂等导入到指定用户。返回批次摘要（positions 预览由调用方另行取）。"""
    if not user_id:
        raise ValueError("import_slip 需要 user_id")
    parsed = parse_slip_bytes(data)
    rows, meta = parsed["rows"], parsed["meta"]
    batch = db.execute(text("""
        INSERT INTO trade_import_batch
            (user_id, filename, file_size, file_format, rows_total, date_min, date_max, detail)
        VALUES (:uid, :fn, :sz, :fmt, :total, :dmin, :dmax, CAST(:detail AS jsonb))
        RETURNING id
    """), {
        "uid": user_id, "fn": filename[:200], "sz": len(data), "fmt": meta["format"][:20],
        "total": len(rows), "dmin": meta["date_min"], "dmax": meta["date_max"],
        "detail": json.dumps({"parse": meta}, ensure_ascii=False),
    }).fetchone()
    batch_id = batch[0]

    inserted = 0
    dup = 0
    side_counts: dict[str, int] = defaultdict(int)
    for r in rows:
        side_counts[r["side"]] += 1
        raw_json = json.dumps({
            k: (v.isoformat() if isinstance(v, _date) else v)
            for k, v in r.items() if k != "subtype"
        }, ensure_ascii=False)
        res = db.execute(_INSERT_SQL, {
            **{k: r.get(k) for k in (
                "account_no", "trade_date", "trade_time", "stock_code", "stock_name",
                "side", "business_name", "quantity", "price", "amount", "balance_qty",
                "clean_amount", "commission", "stamp_tax", "transfer_fee", "extra_fee",
                "cash_balance", "order_no", "exchange_name", "row_hash")},
            "user_id": user_id,
            "source_file": filename[:200],
            "import_batch_id": batch_id,
            "raw": raw_json,
        })
        if res.rowcount:
            inserted += 1
        else:
            dup += 1

    db.execute(text("""
        UPDATE trade_import_batch
        SET rows_inserted=:ins, rows_duplicate=:dup, rows_invalid=:inv,
            side_counts=CAST(:sc AS jsonb)
        WHERE id=:id
    """), {
        "ins": inserted, "dup": dup, "inv": meta["rows_invalid"],
        "sc": json.dumps(side_counts, ensure_ascii=False), "id": batch_id,
    })
    db.commit()
    logger.info(f"[trade-slip] 导入 {filename}: 新增 {inserted} / 重复 {dup} / 无效 {meta['rows_invalid']}")
    return {
        "batch_id": batch_id,
        "filename": filename,
        "format": meta["format"],
        "rows_total": len(rows),
        "rows_inserted": inserted,
        "rows_duplicate": dup,
        "rows_invalid": meta["rows_invalid"],
        "rows_skipped": meta["rows_skipped"],
        "date_min": meta["date_min"],
        "date_max": meta["date_max"],
        "side_counts": dict(side_counts),
        "accounts": meta["accounts"],
        "missing_columns": meta["missing_columns"],
        "invalid_samples": meta["invalid_samples"],
    }


# ── 读取 + 重放 ──

def load_slip_rows(db, start: Optional[_date] = None, end: Optional[_date] = None,
                   user_id: int | None = None) -> list[dict]:
    if not user_id:
        raise ValueError("load_slip_rows 需要 user_id")
    sql = "SELECT * FROM trade_slip"
    cond, params = ["user_id = :uid"], {"uid": user_id}
    if start:
        cond.append("trade_date >= :start")
        params["start"] = start
    if end:
        cond.append("trade_date <= :end")
        params["end"] = end
    if cond:
        sql += " WHERE " + " AND ".join(cond)
    sql += " ORDER BY trade_date, trade_time, id"
    out = []
    for r in db.execute(text(sql), params).mappings():
        d = dict(r)
        for k in ("quantity", "price", "amount", "balance_qty", "clean_amount",
                  "commission", "stamp_tax", "transfer_fee", "extra_fee", "cash_balance"):
            d[k] = float(d[k]) if d[k] is not None else None
        d["subtype"] = _normalize_side(d.get("side"), d.get("business_name"))[1]
        out.append(d)
    return out


def _close_before(db, codes: list[str], day: _date) -> dict[str, float]:
    """取每只股票在 day 之前最近一个收盘价（建仓成本兜底/区间起点估值）。"""
    if not codes:
        return {}
    rows = db.execute(text("""
        SELECT DISTINCT ON (stock_code) stock_code, close
        FROM daily_quote
        WHERE stock_code = ANY(CAST(:codes AS varchar[]))
          AND trade_date < :day AND close > 0
        ORDER BY stock_code, trade_date DESC
    """), {"codes": codes, "day": day}).fetchall()
    return {r[0]: float(r[1]) for r in rows}


def _last_close(db, codes: list[str]) -> dict[str, float]:
    if not codes:
        return {}
    rows = db.execute(text("""
        SELECT DISTINCT ON (stock_code) stock_code, close, trade_date
        FROM daily_quote
        WHERE stock_code = ANY(CAST(:codes AS varchar[])) AND close > 0
        ORDER BY stock_code, trade_date DESC
    """), {"codes": codes}).fetchall()
    return {r[0]: (float(r[1]), r[2]) for r in rows}


def _pre_balance(row: dict) -> float:
    """该行之前的股份余额（由成交后余额反推）。"""
    bal = row.get("balance_qty")
    if bal is None:
        return 0.0
    q = float(row.get("quantity") or 0)
    if row["side"] == "buy":
        return bal - q
    if row["side"] == "sell":
        return bal + q
    return bal


def _apply_row(state: dict, row: dict) -> None:
    """把一行成交应用到持仓状态（原地）。

    state: {qty, avg, realized, estimated, seed_qty}
    - estimated 只表示「当前剩余股数里是否还有期初未覆盖的估算股」；
    - 期初估算股按先进先出消耗，清仓/耗尽后必须复位为 False，
      否则空仓后重新买入的精确成本会被永久误标为估算值。
    """
    code = row["stock_code"]
    st = state.setdefault(code, {"qty": 0.0, "avg": 0.0, "realized": 0.0,
                                 "estimated": False, "seed_qty": 0.0})
    prev_qty = float(st["qty"])
    q = float(row.get("quantity") or 0)
    px = float(row.get("price") or 0)
    amt = float(row.get("amount") or 0)
    fees = _fee_of(row)
    side = row["side"]

    if side == "buy":
        if q <= 0:
            return
        if prev_qty <= 0.5:
            # 空仓后重新建仓：这批股票全部由交割单覆盖，成本可精确计算
            st["seed_qty"] = 0.0
            st["estimated"] = False
        new_qty = st["qty"] + q
        st["avg"] = (st["avg"] * st["qty"] + (amt if amt > 0 else px * q) + fees) / new_qty
        st["qty"] = new_qty
    elif side == "sell":
        if q <= 0:
            return
        st["realized"] += (px - st["avg"]) * q - fees
        st["qty"] = max(0.0, st["qty"] - q)
        # 估算股按先进先出消耗；耗尽后剩余股数全部可验证
        st["seed_qty"] = max(0.0, float(st.get("seed_qty") or 0) - q)
        if st["seed_qty"] <= 0.5:
            st["estimated"] = False
    elif row.get("subtype") == "bonus" and q > 0:
        new_qty = st["qty"] + q
        st["avg"] = (st["avg"] * st["qty"]) / new_qty if new_qty > 0 else 0.0
        st["qty"] = new_qty
    # 红利/股息税只影响现金，不影响股数与成本。
    # 注意：股息税行的「成交数量/股份余额」是计税股数，不是持仓，绝不能采信。

    # 券商股份余额是权威值：仅买卖/送股行可用（历史缺行/送转时以它为准）
    if side != "other":
        bal = row.get("balance_qty")
        if bal is not None and abs(float(bal) - st["qty"]) > 0.5:
            st["qty"] = float(bal)
            if st["qty"] <= 0.5:
                st["seed_qty"] = 0.0
                st["estimated"] = False


def replay_positions(db, rows: list[dict], until: Optional[_date] = None,
                     portfolio_costs: Optional[dict[str, float]] = None) -> tuple[dict, Optional[float]]:
    """从交割单重放持仓。

    返回 (state, cash)。state[code] = {qty, avg, realized, estimated, first_date, last_date}
    - 期初已有持仓（交割单首行之前的余额）成本优先用 portfolio 现成本，否则用首行前收盘价；
    - 数量始终以「股份余额」为最高优先级。
    """
    if not rows:
        return {}, None
    portfolio_costs = portfolio_costs or {}
    codes = sorted({r["stock_code"] for r in rows})
    seed = _close_before(db, codes, rows[0]["trade_date"])

    state: dict[str, dict] = {}
    # 从每只股票的首个买卖行反推期初持仓（红利/股息税行不能用于反推）
    first_by_code: dict[str, dict] = {}
    for r in rows:
        if r["side"] in ("buy", "sell"):
            first_by_code.setdefault(r["stock_code"], r)
    for code, r0 in first_by_code.items():
        pre = _pre_balance(r0)
        if pre > 0.5:
            est = code not in portfolio_costs
            state[code] = {
                "qty": pre,
                "avg": float(portfolio_costs.get(code) or seed.get(code) or r0.get("price") or 0),
                "realized": 0.0,
                "estimated": est,
                "seed_qty": pre,
                "first_date": r0["trade_date"],
                "last_date": r0["trade_date"],
            }

    # 首笔成交前现金 = 该笔成交后余额 - 清算金额（买入清算为负、卖出为正）
    first_cash_row = next((r for r in rows if r.get("cash_balance") is not None), None)
    cash = (float(first_cash_row["cash_balance"]) - float(first_cash_row.get("clean_amount") or 0)
            if first_cash_row else None)

    for r in rows:
        if until and r["trade_date"] >= until:
            break
        _apply_row(state, r)
        st = state[r["stock_code"]]
        st["last_date"] = r["trade_date"]
        if r.get("cash_balance") is not None:
            cash = float(r["cash_balance"])
    return state, cash


# ── 持仓预览 / 同步 ──

def _portfolio_map(db, user_id: int) -> dict[str, dict]:
    rows = db.execute(text("""
        SELECT id, stock_code, stock_name, quantity, cost_price, exchange
        FROM portfolio WHERE user_id=:uid AND is_active=true ORDER BY stock_code
    """), {"uid": user_id}).fetchall()
    return {r[1]: {"id": r[0], "name": r[2], "qty": int(r[3] or 0),
                   "cost": float(r[4] or 0), "exchange": r[5]} for r in rows}


def _exchange_of(code: str) -> str:
    if code.startswith(("0", "3", "1", "2")):
        return "SZSE"
    if code.startswith(("6", "5", "9")):
        return "SSE"
    return "BSE"


def build_positions(db, user_id: int) -> dict:
    """按指定用户的全部交割单重建当前持仓 + 与 portfolio 的差异。"""
    rows = load_slip_rows(db, user_id=user_id)
    if not rows:
        return {"positions": [], "diff": [], "summary": {"positions": 0}, "warnings": ["尚无交割单数据"]}
    pmap = _portfolio_map(db, user_id)
    # 只有「portfolio 数量 == 交割单反推的期初数量」时才信任手工维护的成本；
    # 数量对不上说明这笔持仓在别处被改过，成本宁可用期初收盘价估算。
    pre_qty: dict[str, float] = {}
    for r in rows:
        if r["side"] in ("buy", "sell"):
            pre_qty.setdefault(r["stock_code"], _pre_balance(r))
    trusted_cost = {c: v["cost"] for c, v in pmap.items()
                    if abs(v["qty"] - pre_qty.get(c, 0.0)) <= 0.5}
    state, cash = replay_positions(db, rows, portfolio_costs=trusted_cost)
    last = _last_close(db, list(state))
    positions = []
    warnings = []
    for code, st in sorted(state.items()):
        qty = round(st["qty"], 2)
        if qty <= 0.5:
            continue
        lp, ld = last.get(code, (None, None))
        cost = round(float(st["avg"]), 4)
        positions.append({
            "stock_code": code,
            "stock_name": next((r["stock_name"] for r in reversed(rows) if r["stock_code"] == code), code),
            "quantity": int(qty) if abs(qty - round(qty)) < 1e-6 else qty,
            "cost_price": cost,
            "cost_estimated": bool(st["estimated"]),
            "realized_pnl": round(float(st["realized"]), 2),
            "last_trade_date": st["last_date"].isoformat() if st.get("last_date") else None,
            "last_price": lp,
            "last_price_date": ld.isoformat() if ld else None,
            "market_value": round(lp * qty, 2) if lp else None,
            "exchange": _exchange_of(code),
        })
        if st["estimated"]:
            warnings.append(f"{code} 期初持仓成本为估算值（交割单未覆盖首次买入）")

    slip_codes = {p["stock_code"] for p in positions}
    closed_codes = {c for c, st in state.items() if st["qty"] <= 0.5}
    diff = []
    for p in positions:
        cur = pmap.get(p["stock_code"])
        if not cur:
            status = "new"
        elif abs(cur["qty"] - p["quantity"]) > 0.5 or abs(cur["cost"] - p["cost_price"]) > 0.005:
            status = "changed"
        else:
            status = "same"
        diff.append({
            **p,
            "status": status,
            "cur_qty": cur["qty"] if cur else 0,
            "cur_cost": cur["cost"] if cur else 0,
            "delta_qty": p["quantity"] - (cur["qty"] if cur else 0),
        })
    for code, cur in pmap.items():
        if code in slip_codes:
            continue
        if code in closed_codes:
            diff.append({
                "stock_code": code, "stock_name": cur["name"], "quantity": 0, "cost_price": 0,
                "status": "closed", "cur_qty": cur["qty"], "cur_cost": cur["cost"],
                "delta_qty": -cur["qty"], "cost_estimated": False,
            })
        else:
            diff.append({
                "stock_code": code, "stock_name": cur["name"], "quantity": cur["qty"],
                "cost_price": cur["cost"], "status": "untouched", "cur_qty": cur["qty"],
                "cur_cost": cur["cost"], "delta_qty": 0, "cost_estimated": False,
            })
            warnings.append(f"{code} {cur['name']} 未出现在交割单中，同步时保持原样")

    accounts = sorted({r["account_no"] for r in rows if r.get("account_no")})
    if len(accounts) > 1:
        warnings.append(f"交割单包含 {len(accounts)} 个资金账号，持仓按合并口径重建")
    return {
        "positions": positions,
        "diff": diff,
        "summary": {
            "positions": len(positions),
            "new": sum(1 for d in diff if d["status"] == "new"),
            "changed": sum(1 for d in diff if d["status"] == "changed"),
            "closed": sum(1 for d in diff if d["status"] == "closed"),
            "same": sum(1 for d in diff if d["status"] == "same"),
            "cash": round(cash, 2) if cash is not None else None,
            "market_value": round(sum(p["market_value"] or 0 for p in positions), 2),
            "accounts": accounts,
        },
        "warnings": warnings,
    }


def apply_positions(db, user_id: int, preview: Optional[dict] = None) -> dict:
    """把指定用户的交割单重建结果写入其 portfolio（增量更新 + 清仓）。"""
    if not user_id:
        raise ValueError("apply_positions 需要 user_id")
    preview = preview or build_positions(db, user_id)
    applied = {"inserted": 0, "updated": 0, "closed": 0, "unchanged": 0}
    for d in preview["diff"]:
        code = d["stock_code"]
        if d["status"] == "untouched":
            continue
        if d["status"] == "closed":
            cur = db.execute(text(
                "SELECT id, quantity, cost_price FROM portfolio "
                "WHERE user_id=:uid AND stock_code=:c AND is_active=true"
            ), {"uid": user_id, "c": code}).fetchone()
            if not cur:
                continue
            db.execute(text(
                "UPDATE portfolio SET is_active=false, quantity=0, updated_at=CURRENT_TIMESTAMP "
                "WHERE id=:id AND user_id=:uid"
            ), {"id": cur[0], "uid": user_id})
            db.execute(text("""
                INSERT INTO portfolio_history
                    (user_id, stock_code, stock_name, action, quantity_before, quantity_after, cost_before, cost_after)
                VALUES (:uid, :c, :n, 'slip_out', :qb, 0, :cb, :cb)
            """), {"uid": user_id, "c": code, "n": d["stock_name"], "qb": cur[1],
                   "cb": float(cur[2] or 0)})
            applied["closed"] += 1
            continue
        if d["status"] == "same":
            applied["unchanged"] += 1
            continue

        cur = db.execute(text(
            "SELECT id, quantity, cost_price FROM portfolio "
            "WHERE user_id=:uid AND stock_code=:c AND is_active=true"
        ), {"uid": user_id, "c": code}).fetchone()
        qty = int(round(d["quantity"]))
        cost = round(float(d["cost_price"]), 3)
        if cur:
            db.execute(text("""
                UPDATE portfolio SET quantity=:q, cost_price=:p, stock_name=:n,
                       updated_at=CURRENT_TIMESTAMP WHERE id=:id AND user_id=:uid
            """), {"q": qty, "p": cost, "n": d["stock_name"], "id": cur[0], "uid": user_id})
            db.execute(text("""
                INSERT INTO portfolio_history
                    (user_id, stock_code, stock_name, action, quantity_before, quantity_after, cost_before, cost_after)
                VALUES (:uid, :c, :n, 'slip_sync', :qb, :qa, :cb, :ca)
            """), {"uid": user_id, "c": code, "n": d["stock_name"], "qb": cur[1], "qa": qty,
                   "cb": float(cur[2] or 0), "ca": cost})
            applied["updated"] += 1
        else:
            db.execute(text("""
                INSERT INTO portfolio (user_id, stock_code, stock_name, exchange, quantity, cost_price, notes)
                VALUES (:uid, :c, :n, :e, :q, :p, :notes)
            """), {"uid": user_id, "c": code, "n": d["stock_name"],
                   "e": d.get("exchange") or _exchange_of(code),
                   "q": qty, "p": cost, "notes": "交割单同步"})
            applied["inserted"] += 1
    db.execute(text(
        "UPDATE trade_import_batch SET positions_synced=true, synced_at=CURRENT_TIMESTAMP "
        "WHERE user_id=:uid AND positions_synced=false"
    ), {"uid": user_id})
    db.commit()
    logger.info(f"[trade-slip] 持仓同步 {applied}")
    return applied


# ── 查询 ──

def list_trades(db, user_id: int, start: Optional[_date] = None, end: Optional[_date] = None,
                code: str = "", side: str = "", keyword: str = "",
                page: int = 1, page_size: int = 100) -> dict:
    if not user_id:
        raise ValueError("list_trades 需要 user_id")
    cond, params = ["user_id = :uid"], {"uid": user_id}
    if start:
        cond.append("trade_date >= :start"); params["start"] = start
    if end:
        cond.append("trade_date <= :end"); params["end"] = end
    if code:
        cond.append("stock_code = :code"); params["code"] = code
    if side in ("buy", "sell", "other"):
        cond.append("side = :side"); params["side"] = side
    if keyword:
        cond.append("(stock_code LIKE :kw OR stock_name LIKE :kw)")
        params["kw"] = f"%{keyword}%"
    where = (" WHERE " + " AND ".join(cond)) if cond else ""
    total = db.execute(text(f"SELECT COUNT(*) FROM trade_slip{where}"), params).scalar() or 0
    summary = db.execute(text(f"""
        SELECT COUNT(*),
               COALESCE(SUM(quantity) FILTER (WHERE side='buy'), 0),
               COALESCE(SUM(quantity) FILTER (WHERE side='sell'), 0),
               COALESCE(SUM(amount) FILTER (WHERE side='buy'), 0),
               COALESCE(SUM(amount) FILTER (WHERE side='sell'), 0),
               COALESCE(SUM(commission+stamp_tax+transfer_fee+extra_fee), 0),
               MIN(trade_date), MAX(trade_date)
        FROM trade_slip{where}
    """), params).fetchone()
    offset = max(0, (page - 1) * page_size)
    rows = db.execute(text(f"""
        SELECT id, trade_date, trade_time, stock_code, stock_name, side, business_name,
               quantity, price, amount, balance_qty, clean_amount,
               commission, stamp_tax, transfer_fee, extra_fee, cash_balance,
               order_no, account_no, source_file, import_batch_id
        FROM trade_slip{where}
        ORDER BY trade_date DESC, trade_time DESC, id DESC
        LIMIT :lim OFFSET :off
    """), {**params, "lim": page_size, "off": offset}).mappings().fetchall()
    items = []
    for r in rows:
        d = dict(r)
        for k in ("quantity", "price", "amount", "balance_qty", "clean_amount",
                  "commission", "stamp_tax", "transfer_fee", "extra_fee", "cash_balance"):
            d[k] = float(d[k]) if d[k] is not None else None
        d["trade_date"] = d["trade_date"].isoformat()
        items.append(d)
    return {
        "items": items, "total": total, "page": page, "page_size": page_size,
        "summary": {
            "count": total,
            "buy_qty": float(summary[1] or 0), "sell_qty": float(summary[2] or 0),
            "buy_amount": float(summary[3] or 0), "sell_amount": float(summary[4] or 0),
            "fee_total": float(summary[5] or 0),
            "date_min": summary[6].isoformat() if summary[6] else None,
            "date_max": summary[7].isoformat() if summary[7] else None,
        },
    }


def list_batches(db, user_id: int, limit: int = 50) -> list[dict]:
    if not user_id:
        raise ValueError("list_batches 需要 user_id")
    rows = db.execute(text("""
        SELECT id, filename, file_size, file_format, rows_total, rows_inserted,
               rows_duplicate, rows_invalid, date_min, date_max, side_counts,
               positions_synced, synced_at, created_at
        FROM trade_import_batch WHERE user_id=:uid ORDER BY id DESC LIMIT :lim
    """), {"uid": user_id, "lim": limit}).mappings().fetchall()
    out = []
    for r in rows:
        d = dict(r)
        for k in ("date_min", "date_max"):
            d[k] = d[k].isoformat() if d[k] else None
        for k in ("synced_at", "created_at"):
            d[k] = d[k].isoformat() if d[k] else None
        out.append(d)
    return out


def delete_batch(db, batch_id: int, user_id: int) -> dict:
    if not user_id:
        raise ValueError("delete_batch 需要 user_id")
    n = db.execute(text(
        "DELETE FROM trade_slip WHERE user_id=:uid AND import_batch_id=:id"
    ), {"uid": user_id, "id": batch_id}).rowcount or 0
    db.execute(text(
        "DELETE FROM trade_import_batch WHERE user_id=:uid AND id=:id"
    ), {"uid": user_id, "id": batch_id})
    db.commit()
    logger.info(f"[trade-slip] 删除批次 {batch_id} (user={user_id}): {n} 行")
    return {"deleted_rows": n}
