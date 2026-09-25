import asyncio

import httpx
import pytest
from pydantic import SecretStr

from jarvis.providers import HTTPProviders, GroqAdapter
from jarvis.security import AppError


@pytest.mark.asyncio
async def test_voice_deadline_cancels_slow_body_without_blocking_loop(settings):
    settings.tts_timeout = 0.1
    settings.fish_key, settings.fish_voice_id = SecretStr("fake"), "voice"
    cancelled = asyncio.Event()
    ticked = asyncio.Event()
    async def handler(request):
        try:
            await asyncio.sleep(10)
        finally:
            cancelled.set()
    async def tick():
        await asyncio.sleep(0.01)
        ticked.set()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        providers = HTTPProviders(settings, client)
        ticker = asyncio.create_task(tick())
        with pytest.raises(AppError, match="deadline"):
            await providers.synthesize("hello", "mp3")
        await ticker
        assert ticked.is_set() and cancelled.is_set()
        assert providers.states["fish"] == "degraded"


@pytest.mark.asyncio
async def test_total_deadline_includes_retry_backoff(settings):
    settings.search_timeout, settings.retry_base = 0.1, 1
    calls = []
    async def handler(request):
        calls.append(request)
        return httpx.Response(503)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(AppError, match="deadline"):
            await HTTPProviders(settings, client).request("tavily", "https://example.test", retry=True)
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_stream_deadline_closes_trickling_response(settings):
    settings.provider_timeout = 0.1
    settings.groq_key = SecretStr("fake")
    closed = asyncio.Event()
    class SlowStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            while True:
                yield b'data: {"choices": []}\n\n'
                await asyncio.sleep(0.01)
        async def aclose(self):
            closed.set()
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, stream=SlowStream()))) as client:
        with pytest.raises(AppError, match="deadline"):
            _ = [p async for p in GroqAdapter(HTTPProviders(settings, client)).stream([], [])]
    assert closed.is_set()


@pytest.mark.asyncio
async def test_voice_uses_dedicated_persistent_session_and_closes_both(settings):
    providers = HTTPProviders(settings)
    assert providers.voice_client is not providers.client
    original = providers.voice_client
    assert providers.voice_client is original
    await providers.aclose()
    assert providers.client.is_closed and original.is_closed


@pytest.mark.asyncio
async def test_injected_client_stays_owned_by_caller_and_timeout_phases_are_bounded(settings):
    requests = []
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: requests.append(r) or httpx.Response(200))) as client:
        providers = HTTPProviders(settings, client)
        await providers.request("fish", "https://example.test")
        await providers.request("fish", "https://example.test")
        await providers.aclose()
        assert not client.is_closed
    assert len(requests) == 2
    assert requests[0].extensions["timeout"] == {"connect": 5, "read": 30, "write": 10, "pool": 1}


@pytest.mark.asyncio
async def test_pool_exhaustion_is_not_retried(settings):
    calls = []
    def handler(request):
        calls.append(request)
        raise httpx.PoolTimeout("busy")
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(AppError):
            await HTTPProviders(settings, client).request("tavily", "https://example.test", retry=True)
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_cancelled_voice_request_releases_inflight_work(settings):
    entered, cancelled = asyncio.Event(), asyncio.Event()
    async def handler(request):
        entered.set()
        try:
            await asyncio.sleep(10)
        finally:
            cancelled.set()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        providers = HTTPProviders(settings, client)
        task = asyncio.create_task(providers.request("fish", "https://example.test"))
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert cancelled.is_set()


@pytest.mark.asyncio
async def test_groq_releases_retry_response_before_backoff(settings, monkeypatch):
    settings.groq_key = SecretStr("fake")
    closed = asyncio.Event()
    class RetryStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b"unavailable"
        async def aclose(self):
            closed.set()
    calls = 0
    def handler(request):
        nonlocal calls
        calls += 1
        return httpx.Response(503, stream=RetryStream()) if calls == 1 else httpx.Response(200, text="data: [DONE]\n\n")
    async def backoff(delay):
        assert closed.is_set(), "retry must not hold its connection during backoff"
    monkeypatch.setattr("jarvis.providers.asyncio.sleep", backoff)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        pieces = [p async for p in GroqAdapter(HTTPProviders(settings, client)).stream([], [])]
    assert pieces[-1]["type"] == "turn"
