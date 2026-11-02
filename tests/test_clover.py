import pytest
pytest.importorskip('msal',reason='Operational Windows dependencies required.')
import httpx
import pytest

from app.clover import Clover, CloverError


@pytest.mark.asyncio
async def test_clover_checks_orders_with_bearer_token(vault):
    def respond(request):
        assert request.method == 'GET'
        assert request.url.host == 'api.clover.com'
        assert request.url.path == '/v3/merchants/ABC12345/orders'
        assert request.headers['Authorization'] == 'Bearer local-secret'
        assert request.url.params['limit'] == '1'
        return httpx.Response(200, json={'elements': []})
    provider = Clover(vault, httpx.AsyncClient(transport=httpx.MockTransport(respond)))
    assert await provider.verify('ABC12345', 'local-secret')
    assert not provider.status()['configured']
    await provider.http.aclose()


@pytest.mark.asyncio
async def test_clover_rejects_missing_order_permission_without_echoing_token(vault):
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(403)))
    provider = Clover(vault, client)
    with pytest.raises(CloverError, match='Read Orders') as error:
        await provider.verify('ABC12345', 'local-secret')
    assert 'local-secret' not in str(error.value)
    await client.aclose()
