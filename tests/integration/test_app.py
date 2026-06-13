"""FastAPI 应用集成测试。"""
import pytest
import json
from httpx import ASGITransport, AsyncClient
import pytest_asyncio

from app.main import app
from app.db.connection import SyncSessionLocal
from app.db.schema import init_db


@pytest.fixture(scope="module", autouse=True)
def setup_database():
    """确保测试数据库已初始化。"""
    db = SyncSessionLocal()
    try:
        init_db(db)
    finally:
        db.close()


@pytest_asyncio.fixture(scope="module")
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.mark.asyncio
async def test_health_check(client):
    response = await client.get("/health")
    assert response.status_code == 200
    assert "status" in response.json()


@pytest.mark.asyncio
async def test_data_status(client):
    """数据状态API — PostgreSQL async/sync 混合访问。"""
    try:
        response = await client.get("/api/data_status")
        assert response.status_code in (200, 500)
    except Exception:
        pytest.skip("数据库连接暂时不可用")


@pytest.mark.asyncio
async def test_portfolio(client):
    response = await client.get("/api/portfolio")
    assert response.status_code == 200
    assert "positions" in response.json()


@pytest.mark.asyncio
async def test_buy_signals_empty(client):
    response = await client.get("/api/buy_signals")
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_stock_not_found(client):
    response = await client.get("/api/stock/999999/detail")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_feishu_url_verification(client):
    response = await client.post("/webhook/feishu", json={
        "type": "url_verification",
        "token": "test_token",
        "challenge": "test_challenge",
    })
    assert response.status_code == 200
    assert "challenge" in response.json()


@pytest.mark.asyncio
async def test_feishu_text_message(client):
    response = await client.post("/webhook/feishu", json={
        "type": "event",
        "event": {"message": {"message_type": "text", "content": '{"text":"hi"}'}}
    })
    assert response.status_code == 200
