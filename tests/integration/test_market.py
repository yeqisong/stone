"""大盘/市场详情 API 集成测试（本地库，只读端点）。"""
import pytest
from httpx import ASGITransport, AsyncClient
import pytest_asyncio

from app.main import app
from app.auth.auth import create_token

AUTH = {"Authorization": f"Bearer {create_token('admin')}"}


@pytest_asyncio.fixture(scope="module")
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.mark.asyncio
async def test_market_endpoints_require_auth(client):
    """与 stock/cb/risk 一致：整 router 挂 JWT 依赖。"""
    response = await client.get("/api/market/overview")
    assert response.status_code in (401, 403)


@pytest.mark.asyncio
async def test_market_overview(client):
    response = await client.get("/api/market/overview", headers=AUTH)
    assert response.status_code == 200
    body = response.json()
    assert isinstance(body["indices"], list) and len(body["indices"]) == 7
    first = body["indices"][0]
    for key in ("ts_code", "name", "close", "pe_ttm", "pb", "pe_pct", "pb_pct"):
        assert key in first
    # 估值/分位在回补后应非空（分位 0-100）
    if first["pe_ttm"] is not None and first["pe_pct"] is not None:
        assert 0 <= first["pe_pct"] <= 100
    # 资金流/两融快照字段
    if body["moneyflow"]:
        assert {"date", "net_main", "net_total"} <= set(body["moneyflow"].keys())


@pytest.mark.asyncio
async def test_market_valuation(client):
    response = await client.get("/api/market/valuation/000300.SH", headers=AUTH)
    assert response.status_code == 200
    body = response.json()
    assert body["ts_code"] == "000300.SH"
    assert isinstance(body["data"], list)
    if not body["data"]:
        pytest.skip("测试库未回补指数估值历史")
    row = body["data"][-1]
    assert {"date", "pe_ttm", "pb", "turnover_rate"} <= set(row.keys())
    pct = body["pct"]
    assert pct["p30_pe"] is not None and pct["p70_pe"] is not None
    assert pct["p30_pe"] <= pct["p70_pe"]
    if pct["pe_pct"] is not None:
        assert 0 <= pct["pe_pct"] <= 100


@pytest.mark.asyncio
async def test_market_valuation_unknown_code(client):
    response = await client.get("/api/market/valuation/999999.SZ", headers=AUTH)
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_market_valuation_days_validation(client):
    response = await client.get("/api/market/valuation/000300.SH?days=1", headers=AUTH)
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_market_moneyflow(client):
    response = await client.get("/api/market/moneyflow?days=60", headers=AUTH)
    assert response.status_code == 200
    data = response.json()["data"]
    if not data:
        pytest.skip("测试库未回补资金流/宽度/两融历史")
    assert isinstance(data, list) and data
    row = data[-1]
    assert {"date", "net_main", "net_total", "cum_main", "stocks"} <= set(row.keys())
    # 累计线是逐日净流入的 running sum：相邻差 == 当日净流入
    for a, b in zip(data, data[1:]):
        if a["cum_main"] is not None and b["cum_main"] is not None and b["net_main"] is not None:
            assert abs((b["cum_main"] - a["cum_main"]) - b["net_main"]) < 1.0


@pytest.mark.asyncio
async def test_market_breadth(client):
    response = await client.get("/api/market/breadth?days=30", headers=AUTH)
    assert response.status_code == 200
    data = response.json()["data"]
    if not data:
        pytest.skip("测试库未回补资金流/宽度/两融历史")
    assert isinstance(data, list) and data
    row = data[-1]
    assert {"date", "up", "down", "flat", "limit_up", "limit_down"} <= set(row.keys())
    assert row["up"] >= 0 and row["down"] >= 0
    # 家数量级：全市场股票约 5000+
    assert row["up"] + row["down"] + row["flat"] > 1000


@pytest.mark.asyncio
async def test_market_margin(client):
    response = await client.get("/api/market/margin?days=30", headers=AUTH)
    assert response.status_code == 200
    data = response.json()["data"]
    if not data:
        pytest.skip("测试库未回补资金流/宽度/两融历史")
    assert isinstance(data, list) and data
    row = data[-1]
    assert {"date", "fin", "sec", "total"} <= set(row.keys())
    if row["total"] is not None and row["fin"] is not None:
        # 两融合计 ≥ 融资余额
        assert row["total"] >= row["fin"]


@pytest.mark.asyncio
async def test_market_macro(client):
    response = await client.get("/api/market/macro", headers=AUTH)
    assert response.status_code == 200
    series = response.json()["series"]
    assert isinstance(series, dict)
    if not series:
        pytest.skip("测试库未回补宏观指标")
    # 至少有 CPI / M2 / SHIBOR 部分序列
    assert any(k in series for k in ("cpi", "m2", "shibor_on"))
    for code, rows in series.items():
        assert isinstance(rows, list) and rows
        assert {"date", "value"} <= set(rows[0].keys())
        dates = [r["date"] for r in rows]
        assert dates == sorted(dates)


@pytest.mark.asyncio
async def test_market_sentiment_history(client):
    """情绪分日度序列（恐慌指数趋势，2000 起历史回补）。"""
    response = await client.get("/api/market/sentiment-history?days=100", headers=AUTH)
    assert response.status_code == 200
    data = response.json()["data"]
    if not data:
        pytest.skip("测试库未回补市场情绪历史")
    assert isinstance(data, list) and data
    row = data[-1]
    assert {"date", "score", "level", "up_ratio", "limit_up", "limit_down"} <= set(row.keys())
    assert 0 <= row["score"] <= 100
    assert row["level"] in ("panic", "weak", "neutral", "strong")
    dates = [r["date"] for r in data]
    assert dates == sorted(dates)


@pytest.mark.asyncio
async def test_market_ratio_percentile(client):
    """比值中枢：分子/分母/窗口元信息 + 比值与滚动分位序列。"""
    response = await client.get(
        "/api/market/ratio-percentile?num=399006&den=000922&window=1260", headers=AUTH)
    assert response.status_code == 200
    body = response.json()
    assert body["num"] == {"code": "399006", "name": "创业板指"}
    assert body["den"] == {"code": "000922", "name": "中证红利"}
    assert body["window"] == 1260 and body["window_label"] == "5年"
    data = body["data"]
    if not data:
        pytest.skip("测试库未回补指数行情")
    assert len(data) > 100
    latest = body["latest"]
    assert {"date", "ratio", "pct", "mid", "p20", "p80"} <= set(latest.keys())
    assert latest["ratio"] > 0
    assert 0 <= latest["pct"] <= 100
    dates = [r["date"] for r in data]
    assert dates == sorted(dates)


@pytest.mark.asyncio
async def test_market_ratio_percentile_validation(client):
    """非法分子/分母/窗口 → 400。"""
    for url in (
        "/api/market/ratio-percentile?num=999999&den=000922",
        "/api/market/ratio-percentile?num=399006&den=000001",
        "/api/market/ratio-percentile?num=399006&den=000922&window=1000",
        "/api/market/ratio-percentile?num=399006&den=399006",
    ):
        response = await client.get(url, headers=AUTH)
        assert response.status_code == 400, url


@pytest.mark.asyncio
async def test_market_realtime_requires_auth(client):
    """实时大盘端点与 market/* 同权限（research.market.view 前缀兜底）。"""
    response = await client.get("/api/market/realtime")
    assert response.status_code in (401, 403)
    response = await client.get("/api/market/realtime/zt-history")
    assert response.status_code in (401, 403)


@pytest.mark.asyncio
async def test_market_realtime_shape(client):
    """实时大盘聚合：响应 shape 稳定（收盘后/无缓存跑也过，内容不固定）。"""
    response = await client.get("/api/market/realtime", headers=AUTH)
    assert response.status_code == 200
    body = response.json()
    assert {"ok", "date", "session", "session_label", "asof", "stale"} <= set(body.keys())
    assert body["session"] in ("pre", "open", "lunch", "post", "closed")
    assert isinstance(body["indices_trend"], list)
    assert isinstance(body["senti_track"], list)
    assert isinstance(body["boards"], list)
    assert {"series", "latest"} <= set(body["fflow"].keys())


@pytest.mark.asyncio
async def test_market_zt_history(client):
    """连板高度历史：无数据返回空 days（非 5xx）。"""
    response = await client.get("/api/market/realtime/zt-history?days=30", headers=AUTH)
    assert response.status_code == 200
    body = response.json()
    assert {"days", "count"} <= set(body.keys())
    if body["count"]:
        row = body["days"][-1]
        assert {"date", "limit_up", "broken", "broken_rate", "max_lbc"} <= set(row.keys())


@pytest.mark.asyncio
async def test_archive_zt_daily_idempotent(client):
    """日终梯队落库：写入一次 + 二次幂等跳过 + 清理（离线，预置缓存）。"""
    from datetime import datetime
    from app.db.connection import get_sync_db
    from sqlalchemy import text
    from app import market_realtime as mrt

    db = get_sync_db()
    try:
        day = datetime(2026, 11, 27, 15, 10)
        db.execute(text("DELETE FROM zt_pool_daily WHERE trade_date=:d"), {"d": day.date()})
        db.commit()
        mrt._cache_put("zt", {
            "date": "20261127",
            "pool": [{"code": "300001", "name": "A", "lbc": 3, "fund": 1e8},
                     {"code": "600000", "name": "B", "lbc": 1, "fund": 2e8}],
            "broken_pool": [{"code": "000001", "name": "X"}],
        })
        mrt._cache_put("breadth", {"limit_down": 2})
        orig = mrt._is_trade_day
        mrt._is_trade_day = lambda db_, d: True
        try:
            first = mrt.archive_zt_daily(db, now=day)
            second = mrt.archive_zt_daily(db, now=day)
        finally:
            mrt._is_trade_day = orig
        assert first is True and second is False
        row = db.execute(text(
            "SELECT limit_up, limit_down, broken, broken_rate, max_lbc "
            "FROM zt_pool_daily WHERE trade_date=:d"), {"d": day.date()}).fetchone()
        assert row is not None
        assert row[0] == 2 and row[1] == 2 and row[2] == 1
        assert abs(float(row[3]) - 1 / 3) < 1e-3
        assert row[4] == 3
        db.execute(text("DELETE FROM zt_pool_daily WHERE trade_date=:d"), {"d": day.date()})
        db.commit()
    finally:
        db.close()
