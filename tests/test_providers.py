import json

import httpx
import pytest

from jarvis.providers import GroqAdapter, HTTPProviders
from jarvis.security import AppError


def sse(chunks, done=True):
    content = "".join("data: " + json.dumps(c) + "\n\n" for c in chunks)
    return content + ("data: [DONE]\n\n" if done else "")


@pytest.mark.asyncio
async def test_groq_stream_tool_arguments_and_usage(settings):
    from pydantic import SecretStr
    settings.groq_key = SecretStr("not-a-real-key")
    seen = []
    def handler(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, text=sse([
            {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "call-1", "function": {"name": "task_create", "arguments": '{"title":'}}]}}]},
            {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": '"Tea"}'}}]}}]},
            {"choices": [], "usage": {"total_tokens": 42}}]))
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = HTTPProviders(settings, client)
        pieces = [p async for p in GroqAdapter(provider).stream([{"role": "user", "content": "task"}], [])]
    turn = pieces[-1]["turn"]
    assert turn.calls[0].arguments == {"title": "Tea"}
    assert turn.usage["total_tokens"] == 42
    assert seen[0]["max_completion_tokens"] == settings.output_tokens


@pytest.mark.asyncio
async def test_provider_bounded_retries_and_no_raw_error(settings):
    from pydantic import SecretStr
    settings.groq_key = SecretStr("not-a-real-key")
    count = [0]
    def handler(request):
        count[0] += 1
        return httpx.Response(429, text="sensitive backend detail")
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = HTTPProviders(settings, client)
        with pytest.raises(AppError) as error:
            _ = [p async for p in GroqAdapter(provider).stream([], [])]
        assert count[0] == settings.retries + 1
        assert "sensitive" not in str(error.value)
        assert provider.states["groq"] == "degraded"


@pytest.mark.asyncio
async def test_provider_timeout_retries(settings):
    from pydantic import SecretStr
    settings.groq_key = SecretStr("fake")
    count = [0]
    def handler(request):
        count[0] += 1
        raise httpx.ReadTimeout("private raw exception")
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(AppError, match="interrupted"):
            _ = [p async for p in GroqAdapter(HTTPProviders(settings, client)).stream([], [])]
    assert count[0] == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("body", ["data: not-json\n\n", sse([{"choices": [{"delta": {"content": "Partial"}}]}], done=False)])
async def test_malformed_and_incomplete_stream(settings, body):
    from pydantic import SecretStr
    settings.groq_key = SecretStr("fake")
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, text=body))) as client:
        with pytest.raises(AppError):
            _ = [p async for p in GroqAdapter(HTTPProviders(settings, client)).stream([], [])]


@pytest.mark.asyncio
async def test_voice_and_search_contracts(settings):
    from pydantic import SecretStr
    settings.fish_key = SecretStr("fake-fish")
    settings.fish_voice_id = "configured-voice"
    settings.groq_key = SecretStr("fake-groq")
    settings.tavily_key = SecretStr("fake-search")
    requests = []
    def handler(request):
        requests.append(request)
        if request.url.host == "api.fish.audio":
            assert request.headers["model"] == settings.fish_model
            assert json.loads(request.content)["reference_id"] == "configured-voice"
            return httpx.Response(200, content=b"ID3audio", headers={"Content-Type": "audio/mpeg"})
        if request.url.path.endswith("transcriptions"):
            assert b"whisper-large-v3-turbo" in request.content
            return httpx.Response(200, json={"text": "Hello"})
        return httpx.Response(200, json={"results": [{"title": "Official", "url": "https://example.org", "content": "Data"}]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        providers = HTTPProviders(settings, client)
        assert await providers.synthesize("Hello", "mp3") == b"ID3audio"
        assert await providers.transcribe(b"RIFFaudio", "wav") == "Hello"
        assert (await providers.search("test"))[0]["title"] == "Official"
    assert len(requests) == 3


@pytest.mark.asyncio
async def test_voice_not_retried(settings):
    from pydantic import SecretStr
    settings.fish_key, settings.fish_voice_id = SecretStr("fake"), "voice"
    count = [0]
    def handler(request):
        count[0] += 1
        return httpx.Response(503)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(AppError):
            await HTTPProviders(settings, client).synthesize("Hello", "mp3")
    assert count[0] == 1
