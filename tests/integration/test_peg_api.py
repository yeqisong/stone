"""PEG API 集成测试（本地库，只读端点）。"""
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
async def test_peg_requires_auth(client):
    assert (await client.get("/api/market/peg")).status_code in (401, 403)
    assert (await client.get("/api/stock/000001/peg_history")).status_code in (401, 403)


@pytest.mark.asyncio
async def test_peg_invalid_index(client):
    resp = await client.get("/api/market/peg?index_code=999999.SH", headers=AUTH)
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_peg_cross_section_shape(client):
    resp = await client.get("/api/market/peg?index_code=000300.SH", headers=AUTH)
    assert resp.status_code == 200
    body = resp.json()
    assert body['index']['code'] == '000300.SH'
    assert body['index']['name'] == '沪深300'
    assert {'members', 'with_pe', 'with_consensus', 'with_peg'} <= set(body['coverage'])
    assert isinstance(body['industries'], list)
    assert isinstance(body['items'], list)
    if body['items']:
        it = body['items'][0]
        for key in ('code', 'name', 'industry_l1', 'pe_ttm', 'g', 'peg',
                    'peg_pct', 'org_count', 'flags', 'growth_source', 'peg_basis'):
            assert key in it
        assert body['default_code'] == body['items'][0]['code']
        assert it['peg'] > 0
        # 默认选中：完整口径且 PEG≤1 时优先；否则取全表 PEG 最低
        full = [x for x in body['items'] if x['peg_basis'] == 'full']
        if full and full[0]['peg'] <= 1.0:
            expected = full[0]['code']
        else:
            expected = min(body['items'], key=lambda x: x['peg'])['code']
        assert body['default_code'] == expected
        if full:
            assert body['items'][0]['peg_basis'] == 'full'


@pytest.mark.asyncio
async def test_peg_industry_filter(client):
    resp = await client.get("/api/market/peg?index_code=000300.SH", headers=AUTH)
    body = resp.json()
    if not body['industries']:
        pytest.skip('测试库无指数成分快照')
    ind = body['industries'][0]['name']
    filtered = await client.get(
        f"/api/market/peg?index_code=000300.SH&industry_l1={ind}", headers=AUTH)
    assert filtered.status_code == 200
    fb = filtered.json()
    assert all(it['industry_l1'] == ind for it in fb['items'])
    # 行业列表始终基于未过滤全集
    assert fb['industries'] == body['industries']


@pytest.mark.asyncio
async def test_peg_history_shape(client):
    resp = await client.get("/api/stock/000001/peg_history?years=3", headers=AUTH)
    assert resp.status_code == 200
    body = resp.json()
    assert body['code'] == '000001'
    assert isinstance(body['data'], list)
    assert body['meta']['years'] == 3
    # 一致预期是抽样口径（每 20 个交易日抽样），机构数要如实带出来供前端披露
    meta = body['meta']
    assert {'org_median', 'org_min', 'consensus_points'} <= set(meta)
    if meta['consensus_points']:
        assert meta['org_median'] is not None and meta['org_min'] is not None
        assert 0 < meta['org_min'] <= meta['org_median']
    else:
        assert meta['org_median'] is None and meta['org_min'] is None
    if body['data']:
        row = body['data'][-1]
        for key in ('date', 'pe_ttm', 'g', 'peg', 'band20', 'band50', 'band80',
                    'peg_pct'):
            assert key in row
    else:
        assert body.get('reason')


@pytest.mark.asyncio
async def test_peg_history_validation(client):
    assert (await client.get("/api/stock/abc/peg_history", headers=AUTH)).status_code == 400
    assert (await client.get("/api/stock/000001/peg_history?lam=2",
                             headers=AUTH)).status_code == 422
