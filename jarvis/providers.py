"""Fixed official endpoints; no arbitrary URL fetcher or redirect following."""
import asyncio
import json
import ssl
import logging
import time
from contextlib import aclosing
from typing import AsyncIterator, Protocol

import httpx
from pydantic import ValidationError

from .schemas import ModelTurn, ToolCall
from .security import AppError


class ModelAdapter(Protocol):
    async def stream(self, messages: list, tools: list) -> AsyncIterator[dict]: ...


class SpeechToText(Protocol):
    async def transcribe(self, audio: bytes, format: str) -> str: ...


class SpeechSynthesis(Protocol):
    async def synthesize(self, text: str, format: str) -> bytes: ...


class SearchAdapter(Protocol):
    async def search(self, query: str) -> list[dict]: ...


class ProductivityAdapter(Protocol):
    """Extension point only. Register individual actions with the tool registry."""
    async def execute(self, operation: str, arguments: dict) -> dict: ...


class AutomationAdapter(Protocol):
    """Extension point only; no shell/browser control is enabled by this protocol."""
    async def execute(self, operation: str, arguments: dict) -> dict: ...


class HTTPProviders:
    def __init__(self, settings, client=None):
        self.settings = settings
        self._owns_clients = client is None
        if client is None:
            tls = ssl.create_default_context()
            def session(size):
                return httpx.AsyncClient(timeout=self.timeout(settings.provider_timeout),
                    limits=httpx.Limits(max_connections=size, max_keepalive_connections=size,
                                        keepalive_expiry=settings.keepalive_expiry),
                    follow_redirects=False, trust_env=False, verify=tls)
            self.client = session(16)
            # Keep interactive audio out of the chat/search connection queue.
            self.voice_client = session(8)
        else:
            self.client = self.voice_client = client
        self.states = {}

    def timeout(self, budget):
        return httpx.Timeout(budget, connect=min(self.settings.connect_timeout, budget),
                             pool=min(self.settings.pool_timeout, budget), write=min(10, budget))

    async def aclose(self):
        if self._owns_clients:
            await asyncio.gather(self.client.aclose(), self.voice_client.aclose())

    def require(self, key, name):
        value = key.get_secret_value()
        if not value:
            raise AppError("integration_unconfigured", f"Configure {name} on the backend.", 503)
        return {"Authorization": f"Bearer {value}"}

    async def request(self, name, url, *, retry=False, **kwargs):
        budget = {"fish": self.settings.tts_timeout, "groq_stt": self.settings.stt_timeout,
                  "tavily": self.settings.search_timeout}.get(name, self.settings.provider_timeout)
        client = self.voice_client if name in {"fish", "groq_stt"} else self.client
        started = time.monotonic()
        try:
            # Includes pool wait, upload, body download, retries and backoff.
            async with asyncio.timeout(budget):
                return await self._request(client, name, url, retry=retry,
                                           timeout=self.timeout(budget), **kwargs)
        except TimeoutError:
            self.states[name] = "degraded"
            raise AppError("provider_timeout", f"{name} exceeded its request deadline.", 504) from None
        finally:
            logging.getLogger("jarvis").info(json.dumps({"event": "provider.latency", "provider": name,
                "duration_ms": round((time.monotonic() - started) * 1000)}))

    async def _request(self, client, name, url, *, retry=False, **kwargs):
        attempts = self.settings.retries + 1 if retry else 1
        for attempt in range(attempts):
            try:
                response = await client.post(url, **kwargs)
                if response.status_code == 429 or response.status_code >= 500:
                    if attempt + 1 < attempts:
                        await asyncio.sleep(self.settings.retry_base * 2 ** attempt)
                        continue
                if not response.is_success:
                    raise AppError("provider_rejected", f"{name} rejected the request (HTTP {response.status_code}).", 502)
                self.states[name] = "available"
                return response
            except httpx.TransportError as exc:
                if not isinstance(exc, httpx.PoolTimeout) and attempt + 1 < attempts:
                    await asyncio.sleep(self.settings.retry_base * 2 ** attempt)
                    continue
                self.states[name] = "degraded"
                raise AppError("provider_timeout", f"{name} is temporarily unavailable.", 502) from None
            except AppError:
                self.states[name] = "degraded"
                raise

    async def search(self, query):
        response = await self.request("tavily", "https://api.tavily.com/search", retry=True,
                                      headers=self.require(self.settings.tavily_key, "TAVILY_API_KEY"),
                                      json={"query": query, "max_results": 5, "include_raw_content": False})
        try:
            return [{"title": str(r["title"])[:300], "url": str(r["url"])[:2048],
                     "content": str(r.get("content", ""))[:3000]} for r in response.json()["results"][:5]]
        except (ValueError, KeyError, TypeError):
            raise AppError("provider_invalid", "Search returned an invalid response.", 502) from None

    async def synthesize(self, text, format):
        headers = self.require(self.settings.fish_key, "FISH_API_KEY")
        if not self.settings.fish_voice_id:
            raise AppError("integration_unconfigured", "Configure FISH_VOICE_ID for your selected voice.", 503)
        headers["model"] = self.settings.fish_model
        response = await self.request("fish", "https://api.fish.audio/v1/tts", headers=headers,
                                      json={"text": text, "reference_id": self.settings.fish_voice_id,
                                            "format": format})
        if not response.content or len(response.content) > 20 * 1024 * 1024:
            raise AppError("provider_invalid", "Speech response was empty or too large.", 502)
        return response.content

    async def transcribe(self, audio, format):
        response = await self.request("groq_stt", "https://api.groq.com/openai/v1/audio/transcriptions",
                                      headers=self.require(self.settings.groq_key, "GROQ_API_KEY"),
                                      data={"model": self.settings.stt_model, "response_format": "json"},
                                      files={"file": (f"audio.{format}", audio)})
        try:
            text = response.json()["text"]
            if not isinstance(text, str):
                raise ValueError()
            return text
        except (KeyError, TypeError, ValueError):
            raise AppError("provider_invalid", "Transcription returned an invalid response.", 502) from None

    def capabilities(self):
        def state(name, configured):
            return {"status": self.states.get(name, "available") if configured else "unconfigured",
                    "live_verified": name in self.states and self.states[name] == "available"}
        return {"model": {**state("groq", bool(self.settings.groq_key.get_secret_value()) or self.settings.provider == "mock"),
                           "provider": self.settings.provider, "mock": self.settings.provider == "mock"},
                "speech_synthesis": state("fish", bool(self.settings.fish_key.get_secret_value() and self.settings.fish_voice_id)),
                "speech_recognition": state("groq_stt", bool(self.settings.groq_key.get_secret_value())),
                "web_search": state("tavily", bool(self.settings.tavily_key.get_secret_value())),
                "computer_automation": {"status": "unavailable", "reason": "Extension point only"},
                "productivity": {"status": "unavailable", "reason": "Extension point only"}}


class GroqAdapter:
    def __init__(self, http):
        self.http = http

    async def stream(self, messages, tools):
        started = time.monotonic()
        try:
            async with asyncio.timeout(self.http.settings.provider_timeout):
                async with aclosing(self._stream(messages, tools)) as stream:
                    async for piece in stream:
                        yield piece
        except TimeoutError:
            self.http.states["groq"] = "degraded"
            raise AppError("provider_timeout", "Groq exceeded its request deadline.", 504) from None
        finally:
            logging.getLogger("jarvis").info(json.dumps({"event": "provider.latency", "provider": "groq",
                "duration_ms": round((time.monotonic() - started) * 1000)}))

    async def _stream(self, messages, tools):
        settings = self.http.settings
        headers = self.http.require(settings.groq_key, "GROQ_API_KEY")
        body = {"model": settings.groq_model, "messages": messages,
                "stream": True, "stream_options": {"include_usage": True},
                "max_completion_tokens": settings.output_tokens}
        if tools:
            body.update(tools=tools, tool_choice="auto", parallel_tool_calls=False)
        for attempt in range(settings.retries + 1):
            emitted = False
            text, calls, usage, finished = "", {}, None, False
            try:
                async with self.http.client.stream("POST", "https://api.groq.com/openai/v1/chat/completions",
                                                   headers=headers, json=body,
                                                   timeout=self.http.timeout(settings.provider_timeout)) as response:
                    if response.status_code == 429 or response.status_code >= 500:
                        if attempt < settings.retries:
                            # Release the socket before sleeping, not after backoff.
                            await response.aclose()
                            yield {"type": "retry", "attempt": attempt + 1}
                            await asyncio.sleep(settings.retry_base * 2 ** attempt)
                            continue
                    if not response.is_success:
                        raise AppError("provider_rejected", f"Groq rejected the request (HTTP {response.status_code}).", 502)
                    async for line in response.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        raw = line[5:].strip()
                        if raw == "[DONE]":
                            finished = True
                            break
                        chunk = json.loads(raw)
                        usage = chunk.get("usage") or chunk.get("x_groq", {}).get("usage") or usage
                        for choice in chunk.get("choices", []):
                            delta = choice.get("delta", {})
                            if delta.get("content"):
                                piece = delta["content"]
                                text += piece
                                if len(text) > 100000:
                                    raise ValueError("response too large")
                                emitted = True
                                yield {"type": "delta", "text": piece}
                            for call in delta.get("tool_calls", []):
                                index = call["index"]
                                if index not in calls and len(calls) >= settings.max_tool_calls:
                                    raise ValueError("too many calls")
                                entry = calls.setdefault(index, {"id": "", "name": "", "arguments": ""})
                                entry["id"] += call.get("id", "")
                                fn = call.get("function", {})
                                entry["name"] += fn.get("name", "")
                                entry["arguments"] += fn.get("arguments", "")
                                if len(entry["arguments"]) > settings.context_chars:
                                    raise ValueError("arguments too large")
                    if not finished:
                        raise AppError("provider_incomplete", "Groq stream ended before completion.", 502)
                result = ModelTurn(content=text, calls=[ToolCall(id=c["id"], name=c["name"],
                                   arguments=json.loads(c["arguments"])) for c in calls.values()], usage=usage)
                self.http.states["groq"] = "available"
                yield {"type": "turn", "turn": result}
                return
            except httpx.TransportError as exc:
                if not isinstance(exc, httpx.PoolTimeout) and not emitted and attempt < settings.retries:
                    yield {"type": "retry", "attempt": attempt + 1}
                    await asyncio.sleep(settings.retry_base * 2 ** attempt)
                    continue
                self.http.states["groq"] = "degraded"
                raise AppError("provider_timeout", "Groq stream interrupted; partial output is not a completed answer.", 502) from None
            except (ValueError, KeyError, TypeError, ValidationError):
                self.http.states["groq"] = "degraded"
                raise AppError("provider_invalid", "Groq returned malformed model output.", 502) from None
            except AppError:
                self.http.states["groq"] = "degraded"
                raise


class MockAdapter:
    """Deterministic commands only. Deliberately does not pretend to be an LLM."""
    async def stream(self, messages, tools):
        latest = messages[-1]
        if latest["role"] == "tool":
            content = "[MOCK] Tool result: " + latest["content"]
            yield {"type": "delta", "text": content}
            yield {"type": "turn", "turn": ModelTurn(content=content)}
            return
        prompt = latest.get("content", "")
        if prompt == "/slow":
            await asyncio.sleep(60)
        name, args = None, {}
        if prompt.startswith("/tool "):
            _, name, raw = prompt.split(" ", 2)
            try:
                args = json.loads(raw)
            except ValueError:
                raise AppError("mock_command_invalid", "Use /tool NAME followed by JSON arguments.", 422) from None
        elif prompt.startswith("/task "):
            name, args = "task_create", {"title": prompt[6:]}
        elif prompt.startswith("/note "):
            name, args = "note_create", {"title": "Note", "content": prompt[6:]}
        elif prompt.startswith("/remember "):
            name, args = "memory_create", {"title": "Preference", "content": prompt[10:]}
        elif prompt == "/tasks":
            name = "task_list"
        elif prompt == "/memories":
            name = "memory_list"
        elif prompt == "/time":
            name = "current_time"
        if name:
            yield {"type": "turn", "turn": ModelTurn(calls=[ToolCall(id="mock-call", name=name, arguments=args)])}
        else:
            content = "[MOCK] Offline demo ready. Commands: /task TITLE, /note TEXT, /remember TEXT, /tasks, /memories, /time, /tool NAME JSON, /slow."
            yield {"type": "delta", "text": content}
            yield {"type": "turn", "turn": ModelTurn(content=content)}
