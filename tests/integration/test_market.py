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
    assert isinstance(body["data"], list) and body["data"]
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
