import importlib.util
from pathlib import Path
import httpx
import pytest
from fastapi.testclient import TestClient
from tony_demo.web import create_app
from tony_demo.integrations.monday import read_board,parse_board,API_VERSION


def test_demo_has_no_network_calls_or_mutation_endpoints(monkeypatch):
    def denied(*args,**kwargs):raise AssertionError('Default demo must stay offline')
    monkeypatch.setattr(httpx.AsyncClient,'post',denied)
    with TestClient(create_app()) as c:
        result=c.get('/api/demo')
        assert result.status_code==200 and result.json()['provenance']['synthetic']
        assert c.get('/').status_code==200 and c.get('/static/app.js').status_code==200
        assert c.post('/api/demo',json={}).status_code==405
        assert 'frame-ancestors' in result.headers['content-security-policy']


@pytest.mark.asyncio
async def test_optional_monday_checker_uses_fixed_versioned_read_and_marks_partial():
    def handle(request):
        assert request.url=='https://api.monday.com/v2'
        assert request.headers['API-Version']==API_VERSION
        assert b'mutation' not in request.content
        return httpx.Response(200,json={'data':{'boards':[{'id':'123','name':'Synthetic test board',
            'items_page':{'cursor':'more','items':[{'name':'Synthetic task','updated_at':'2026-04-01T00:00:00Z'}]}}]}})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        result=await read_board('https://example.monday.com/boards/123','synthetic-test-only',client)
    assert result['partial'] and result['read_only'] and len(result['items'])==1
    assert 'synthetic-test-only' not in str(result)


@pytest.mark.parametrize('url',['http://example.monday.com/boards/123','https://monday.com.evil.example/boards/123',
    'https://user:pass@example.monday.com/boards/123','https://example.monday.com/boards/123?token=private'])
def test_monday_board_validation(url):
    with pytest.raises(ValueError):parse_board(url)


@pytest.mark.asyncio
async def test_monday_auth_error_does_not_expose_token_or_upstream_body():
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r:httpx.Response(403,text='private-upstream-body'))) as client:
        with pytest.raises(ValueError) as error:await read_board('https://example.monday.com/boards/123','synthetic-test-only',client)
    assert 'private-upstream-body' not in str(error.value) and 'synthetic-test-only' not in str(error.value)


def test_public_guard_detects_tokens_and_private_file_types():
    path=Path(__file__).parents[1]/'scripts/check_public_bundle.py'
    spec=importlib.util.spec_from_file_location('public_guard',path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    fake='sk-'+('x'*30)
    assert module.check([('accidental.txt',fake.encode())])
    assert module.check([('private.sqlite3',b'data')])
