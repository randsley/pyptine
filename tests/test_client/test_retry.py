"""Retry and timeout policy (pyptine.client.retry), sync and async.

INE answers 404 for valid codes when throttling, resets connections, answers 429/5xx under
load, and its gateway gives 504 after 120 s (verified 2026-11-26/27). Refusals (HTTP 200 +
Sucesso.Falso) are final. The conftest fixture zeroes the waits; tests of the waits patch
the sleep and pass retry_delays explicitly.
"""

from pathlib import Path

import httpx
import pytest
import requests
import responses

from pyptine import INE
from pyptine.client import base as base_module
from pyptine.client.async_base import AsyncINEClient
from pyptine.client.base import INEClient
from pyptine.client.metadata import MetadataClient
from pyptine.utils.exceptions import APIError, INERefusalError, RateLimitError

URL = "https://www.ine.pt/test"
META_URL = "https://www.ine.pt/ine/json_indicador/pindicaMeta.jsp"
FIXTURES = Path(__file__).parents[1] / "fixtures" / "ine"
OK = {"ok": True}


@pytest.fixture
def sleeps(monkeypatch):
    """Record the waits instead of sleeping."""
    recorded: list[float] = []
    monkeypatch.setattr(base_module.time, "sleep", recorded.append)
    return recorded


class TestSyncRetry:
    @responses.activate
    def test_throttling_404_then_success(self):
        responses.add(responses.GET, URL, status=404)
        responses.add(responses.GET, URL, status=404)
        responses.add(responses.GET, URL, json=OK)
        assert INEClient(cache_enabled=False)._make_request("/test") == OK
        assert len(responses.calls) == 3

    @responses.activate
    def test_persistent_503(self):
        responses.add(responses.GET, URL, status=503)
        with pytest.raises(APIError, match="HTTP 503 after 4 attempt") as e:
            INEClient(cache_enabled=False)._make_request("/test")
        assert e.value.status_code == 503
        assert len(responses.calls) == 4

    @responses.activate
    def test_504_message_names_the_gateway_limit(self):
        responses.add(responses.GET, URL, status=504)
        with pytest.raises(APIError, match="120 s"):
            INEClient(cache_enabled=False)._make_request("/test")

    @responses.activate
    def test_persistent_429_is_rate_limit_error(self):
        responses.add(responses.GET, URL, status=429)
        with pytest.raises(RateLimitError):
            INEClient(cache_enabled=False)._make_request("/test")
        assert len(responses.calls) == 4

    @responses.activate
    def test_default_waits(self, sleeps):
        responses.add(responses.GET, URL, status=503)
        c = INEClient(cache_enabled=False, retry_delays=(5, 20, 60))
        with pytest.raises(APIError):
            c._make_request("/test")
        assert sleeps == [5.0, 20.0, 60.0]

    def test_module_defaults(self):
        from pyptine.client import retry

        assert retry.RETRY_DELAYS == (5.0, 20.0, 60.0)
        assert retry.MAX_RETRIES == 3
        assert retry.DEFAULT_TIMEOUT > 120  # longer than INE's gateway limit

    @responses.activate
    def test_last_wait_repeats(self, sleeps):
        responses.add(responses.GET, URL, status=503)
        c = INEClient(cache_enabled=False, max_retries=5, retry_delays=(5, 20, 60))
        with pytest.raises(APIError):
            c._make_request("/test")
        assert sleeps == [5.0, 20.0, 60.0, 60.0, 60.0]
        assert len(responses.calls) == 6

    @responses.activate
    def test_no_retries(self):
        responses.add(responses.GET, URL, status=503)
        with pytest.raises(APIError, match="after 1 attempt"):
            INEClient(cache_enabled=False, max_retries=0)._make_request("/test")
        assert len(responses.calls) == 1

    @responses.activate
    def test_retry_after_honoured_and_capped(self, sleeps):
        responses.add(responses.GET, URL, status=429, headers={"Retry-After": "7"})
        responses.add(responses.GET, URL, status=429, headers={"Retry-After": "1000"})
        responses.add(responses.GET, URL, json=OK)
        c = INEClient(cache_enabled=False, retry_delays=(1,))
        assert c._make_request("/test") == OK
        assert sleeps == [7.0, 120.0]

    @responses.activate
    def test_connection_reset_then_success(self):
        responses.add(responses.GET, URL, body=requests.ConnectionError("connection reset"))
        responses.add(responses.GET, URL, json=OK)
        assert INEClient(cache_enabled=False)._make_request("/test") == OK
        assert len(responses.calls) == 2

    @responses.activate
    def test_read_timeout_not_retried(self):
        responses.add(responses.GET, URL, body=requests.ReadTimeout("slow"))
        with pytest.raises(APIError, match="read timeout; not retried"):
            INEClient(cache_enabled=False)._make_request("/test")
        assert len(responses.calls) == 1

    @responses.activate
    def test_client_error_not_retried(self):
        responses.add(responses.GET, URL, status=400)
        with pytest.raises(APIError) as e:
            INEClient(cache_enabled=False)._make_request("/test")
        assert e.value.status_code == 400
        assert len(responses.calls) == 1

    @responses.activate
    def test_refusal_not_retried(self):
        body = (FIXTURES / "meta_9999999_EN.json").read_bytes()
        responses.add(responses.GET, META_URL, body=body, content_type="application/json")
        with pytest.raises(INERefusalError):
            MetadataClient(cache_enabled=False).get_metadata("9999999")
        assert len(responses.calls) == 1

    @responses.activate
    def test_truncated_body_retried_and_not_cached(self, tmp_path):
        body = (FIXTURES / "meta_0008273_EN.json").read_bytes()
        responses.add(responses.GET, META_URL, body=body[:1000], content_type="application/json")
        responses.add(responses.GET, META_URL, body=body, content_type="application/json")
        m = MetadataClient(cache_enabled=True, cache_dir=tmp_path)
        assert m.get_metadata("0008273").varcd == "0008273"
        assert len(responses.calls) == 2
        # Served from the cache now: the good body, not the truncated one.
        assert m.get_metadata("0008273").varcd == "0008273"
        assert len(responses.calls) == 2
        m.close()

    def test_timeouts_sent(self, monkeypatch):
        seen = {}

        def fake_get(url, params=None, timeout=None, **kwargs):
            seen["timeout"] = timeout
            r = requests.Response()
            r.status_code, r._content, r.url = 200, b'{"ok": true}', url
            return r

        c = INEClient(cache_enabled=False)
        monkeypatch.setattr(c.session, "get", fake_get)
        c._make_request("/test")
        assert seen["timeout"] == (10.0, 130)
        c = INEClient(cache_enabled=False, timeout=5)
        monkeypatch.setattr(c.session, "get", fake_get)
        c._make_request("/test")
        assert seen["timeout"] == (5.0, 5)

    def test_facade_defaults(self):
        ine = INE(cache=False)
        for client in (ine.data_client, ine.metadata_client, ine.catalogue_client):
            assert client.timeout == 130
            assert client.max_retries == 3


def mock_async(handler) -> AsyncINEClient:
    c = AsyncINEClient()
    c.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return c


def sequence(*steps):
    """A handler answering with each step in turn (an int status, a dict body, or an error)."""
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        step = steps[min(len(calls), len(steps) - 1)]
        calls.append(request)
        if isinstance(step, Exception):
            raise step
        if isinstance(step, int):
            return httpx.Response(step)
        return httpx.Response(200, json=step)

    return handler, calls


class TestAsyncRetry:
    async def test_throttling_404_then_success(self):
        handler, calls = sequence(404, OK)
        c = mock_async(handler)
        assert await c._make_request("/test") == OK
        assert len(calls) == 2
        await c.client.aclose()

    async def test_persistent_503(self):
        handler, calls = sequence(503)
        c = mock_async(handler)
        with pytest.raises(APIError, match="HTTP 503 after 4 attempt"):
            await c._make_request("/test")
        assert len(calls) == 4
        await c.client.aclose()

    async def test_connect_error_then_success(self):
        handler, calls = sequence(httpx.ConnectError("reset"), OK)
        c = mock_async(handler)
        assert await c._make_request("/test") == OK
        assert len(calls) == 2
        await c.client.aclose()

    async def test_read_timeout_not_retried(self):
        handler, calls = sequence(httpx.ReadTimeout("slow"))
        c = mock_async(handler)
        with pytest.raises(APIError, match="read timeout; not retried"):
            await c._make_request("/test")
        assert len(calls) == 1
        await c.client.aclose()

    async def test_malformed_then_success(self):
        calls = []

        def handler(request):
            calls.append(request)
            return httpx.Response(200, content=b'[{"Dad' if len(calls) == 1 else b'{"ok": true}')

        c = mock_async(handler)
        assert await c._make_request("/test") == OK
        assert len(calls) == 2
        await c.client.aclose()

    async def test_waits(self, monkeypatch):
        from pyptine.client import async_base

        waited: list[float] = []

        async def fake_sleep(seconds):
            waited.append(seconds)

        monkeypatch.setattr(async_base.asyncio, "sleep", fake_sleep)
        handler, _ = sequence(503)
        c = mock_async(handler)
        c.retry_delays = (5, 20, 60)
        with pytest.raises(APIError):
            await c._make_request("/test")
        assert waited == [5.0, 20.0, 60.0]
        await c.client.aclose()

    async def test_timeouts_configured(self):
        async with AsyncINEClient() as c:
            assert c.client.timeout.read == 130
            assert c.client.timeout.connect == 10.0
