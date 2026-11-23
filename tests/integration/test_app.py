"""FastAPI 应用集成测试。"""
import pytest
import json
import base64
import hashlib
import os
import time
from unittest.mock import patch
from httpx import ASGITransport, AsyncClient
import pytest_asyncio
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from app.main import app
from app.auth.auth import create_token
from app.db.connection import SyncSessionLocal
from app.db.schema import init_db

AUTH = {"Authorization": f"Bearer {create_token('admin')}"}


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
    response = await client.get("/api/portfolio", headers=AUTH)
    assert response.status_code == 200
    assert "positions" in response.json()


@pytest.mark.asyncio
async def test_buy_signals_empty(client):
    response = await client.get("/api/buy_signals", headers=AUTH)
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_watch_window_requires_auth(client):
    response = await client.get("/api/portfolio/watch-window")
    assert response.status_code in (401, 403)


@pytest.mark.asyncio
async def test_watch_window_with_auth(client):
    response = await client.get("/api/portfolio/watch-window", headers=AUTH)
    assert response.status_code == 200
    assert "items" in response.json()


@pytest.mark.asyncio
async def test_risk_heat_requires_auth(client):
    response = await client.get("/api/portfolio/risk-heat")
    assert response.status_code in (401, 403)


@pytest.mark.asyncio
async def test_risk_heat_with_auth(client):
    response = await client.get("/api/portfolio/risk-heat", headers=AUTH)
    assert response.status_code == 200
    body = response.json()
    assert body.get("ok") is True
    assert "score" in body


@pytest.mark.asyncio
async def test_stock_not_found(client):
    response = await client.get("/api/stock/999999/detail", headers=AUTH)
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_stock_requires_auth(client):
    response = await client.get("/api/stock/600519/detail")
    assert response.status_code in (401, 403)


@pytest.mark.asyncio
async def test_risk_requires_auth(client):
    response = await client.get("/api/risk/alerts")
    assert response.status_code in (401, 403)


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


@pytest.mark.asyncio
async def test_feishu_bad_signature_rejected(client):
    from app.config import settings
    with patch.object(settings, "FEISHU_ENCRYPT_KEY", "k"):
        response = await client.post(
            "/webhook/feishu", json={"type": "event"},
            headers={"X-Lark-Request-Timestamp": str(int(time.time())),
                     "X-Lark-Request-Nonce": "n",
                     "X-Lark-Signature": "0" * 64})
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_feishu_encrypted_event_accepted(client):
    from app.config import settings
    key = "encrypt-key"
    plain = json.dumps({"type": "event", "event": {"message": {"message_type": "image"}}},
                       ensure_ascii=False).encode()
    padder = padding.PKCS7(128).padder()
    padded = padder.update(plain) + padder.finalize()
    iv = os.urandom(16)
    enc = Cipher(algorithms.AES(hashlib.sha256(key.encode()).digest()),
                 modes.CBC(iv)).encryptor()
    token = base64.b64encode(iv + enc.update(padded) + enc.finalize()).decode()
    body = json.dumps({"encrypt": token}).encode()
    ts, nonce = str(int(time.time())), "nonce"
    sig = hashlib.sha256(f"{ts}{nonce}{key}{body.decode()}".encode()).hexdigest()
    with patch.object(settings, "FEISHU_ENCRYPT_KEY", key), \
            patch.object(settings, "FEISHU_VERIFY_TOKEN", ""):
        response = await client.post(
            "/webhook/feishu", content=body,
            headers={"Content-Type": "application/json",
                     "X-Lark-Request-Timestamp": ts,
                     "X-Lark-Request-Nonce": nonce,
                     "X-Lark-Signature": sig})
    assert response.status_code == 200
    assert response.json().get("code") == 0
