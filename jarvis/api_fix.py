import asyncio
import base64
import binascii
from contextlib import asynccontextmanager
import hmac
import json
import logging
import os
import secrets
from pathlib import Path

from fastapi import FastAPI, Request, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response, StreamingResponse
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import SecretStr

from .config import Settings
from .providers import HTTPProviders, GroqAdapter, MockAdapter
from .runtime import Runtime, TERMINAL, DEFAULT_TOOLS
from .scheduler import Scheduler
from .schemas import (ApprovalDecision, ConversationCreate, ItemCreate, ItemUpdate,
                      ReminderCreate, ScheduledToolCreate, RunInput, SpeechInput, ToolInvocation, TranscriptionInput)
from .security import AppError, Redactor
from .store import Store, uid
from .tools import Registry, Authorization


class ProcessLock:
    """Prevent two API runtimes from sharing the same database and recovering each other."""
    def __init__(self, path):
        self.file = path.open("a+b")
        try:
            self.file.seek(0)
            if not self.file.read(1):
                self.file.write(b"0")
                self.file.flush()
            self.file.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.file.close()
            raise RuntimeError("Another JARVIS service is already using this data directory.") from None

    def close(self):
        self.file.close()


def error_response(code, message, status, request_id=None):
    return JSONResponse({"error": {"code": code, "message": message, "request_id": request_id}}, status_code=status)


class GuardMiddleware:
    def __init__(self, app, settings):
        self.app, self.settings = app, settings

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        request_id = uid()
        scope.setdefault("state", {})["request_id"] = request_id
        headers = dict(scope["headers"])
        path = scope["path"]
        public = path in {"/", "/v1/health", "/docs", "/openapi.json", "/redoc"} or path.startswith("/assets/")
        if not public and scope["method"] != "OPTIONS":
            token = headers.get(b"authorization", b"").decode("latin-1")
            expected = "Bearer " + self.settings.api_token.get_secret_value()
            if not self.settings.api_token.get_secret_value() or not hmac.compare_digest(token, expected):
                return await error_response("unauthorized", "Valid bearer token required.", 401, request_id)(scope, receive, send)
        chunks, length = [], 0
        while True:
            msg = await receive()
            if msg["type"] == "http.disconnect":
                return
            length += len(msg.get("body", b""))
            if length > self.settings.max_body_bytes:
                return await error_response("request_too_large", "Request exceeds the configured limit.", 413, request_id)(scope, receive, send)
            chunks.append(msg.get("body", b""))
            if not msg.get("more_body", False):
                break
        delivered = False

        async def limited_receive():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": b"".join(chunks), "more_body": False}
            return await receive()

        response_started = False

        async def stamped_send(message):
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
                message["headers"] = list(message.get("headers", [])) + [(b"x-request-id", request_id.encode()),
                                                                          (b"cache-control", b"no-store")]
            await send(message)

        try:
            await self.app(scope, limited_receive, stamped_send)
        except Exception:
            # Catch before the outer server-error middleware can log raw
            # exception strings containing provider bodies or credentials.
            logging.getLogger("jarvis").error(json.dumps({"event": "request.failed", "request_id": request_id}))
            if not response_started:
                await error_response("internal_error", "Request failed; use the request ID for diagnostics.",
                                     500, request_id)(scope, limited_receive, stamped_send)


def create_app(settings=None, *, model=None, clock=None, http_client=None):
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app):
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        lock = ProcessLock(settings.data_dir / "service.lock")
        if not settings.api_token.get_secret_value():
            token_file = settings.data_dir / "api-token"
            if not token_file.exists():
                with token_file.open("x", encoding="utf-8") as handle:
                    handle.write(secrets.token_urlsafe(32))
                token_file.chmod(0o600)
            settings.api_token = SecretStr(token_file.read_text().strip())
        if len(settings.api_token.get_secret_value()) < 24:
            lock.close()
            raise RuntimeError("JARVIS_API_TOKEN must contain at least 24 characters.")
        store = Store(settings.data_dir / "jarvis.sqlite", **({"clock": clock} if clock else {}))
        redactor = Redactor(settings.secrets())
        providers = HTTPProviders(settings, http_client)
        scheduler = Scheduler(store)
        registry = Registry(settings, store, scheduler, providers, redactor)
        authorization = Authorization(registry)
        scheduler.registry, scheduler.authorization = registry, authorization
        adapter = model or (GroqAdapter(providers) if settings.provider == "groq" else MockAdapter())
        runtime = Runtime(settings, store, adapter, registry, authorization, redactor)
        app.state.store, app.state.runtime = store, runtime
        app.state.registry, app.state.authorization = registry, authorization
        app.state.providers, app.state.redactor, app.state.scheduler = providers, redactor, scheduler
        runtime.recover()
        scheduler_task = asyncio.create_task(scheduler.serve())
        app.state.scheduler_task = scheduler_task
        try:
            yield
        finally:
            scheduler_task.cancel()
            await asyncio.gather(scheduler_task, return_exceptions=True)
            await runtime.shutdown()
            await providers.aclose()
            store.close()
            lock.close()

    app = FastAPI(title="JARVIS backend", version="0.1.0", lifespan=lifespan,
                  description="Single-user authenticated backend. Bearer token maps to owner 'local'. "
                              "Mock mode is a deterministic offline demonstration.")
    app.add_middleware(GuardMiddleware, settings=settings)
    app.add_middleware(CORSMiddleware, allow_origins=settings.allowed_origins,
                       allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
                       allow_headers=["Authorization", "Content-Type", "Last-Event-ID"],
                       expose_headers=["X-Request-ID"])

    static_dir = Path(__file__).parent / "static"
    app.mount("/assets", StaticFiles(directory=static_dir), name="assets")

    @app.get("/", include_in_schema=False)
    async def chat_page():
        return FileResponse(static_dir / "index.html", headers={
            "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; media-src 'self' blob:; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'",
            "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer"})

    @app.exception_handler(AppError)
    async def app_error(request, exc):
        return error_response(exc.code, exc.message, exc.status, getattr(request.state, "request_id", None))

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        # Do not echo input fields: they may contain a secret.
        return error_response("validation_error", "Request does not match the endpoint schema.", 422, request.state.request_id)

    @app.exception_handler(HTTPException)
    async def http_error(request, exc):
        return error_response("http_error", "Request could not be handled.", exc.status_code, request.state.request_id)

    @app.exception_handler(Exception)
    async def unexpected(request, exc):
        return error_response("internal_error", "Request failed. Check the request ID in local diagnostics.", 500,
                              getattr(request.state, "request_id", None))

    @app.get("/v1/health", tags=["status"])
    async def health():
        return {"status": "ok", "version": "0.1.0"}

    @app.get("/v1/ready", tags=["status"])
    async def ready():
        app.state.store.one("SELECT 1 AS alive")
        if app.state.scheduler_task.done():
            raise AppError("scheduler_unavailable", "Scheduler stopped; restart the service.", 503)
        return {"status": "ready", "integrations": app.state.providers.capabilities()}

    @app.get("/v1/capabilities", tags=["status"])
    async def capabilities():
        return {"integrations": app.state.providers.capabilities(),
                "tools": [t.public() for t in app.state.registry.tools.values()]}

    @app.get("/v1/config", tags=["status"])
    async def configuration():
        return {"provider": settings.provider, "model": settings.groq_model,
                "timezone": settings.timezone, "default_tools": sorted(DEFAULT_TOOLS),
                "max_tool_calls": settings.max_tool_calls, "run_timeout": settings.run_timeout,
                "context_chars": settings.context_chars, "output_tokens": settings.output_tokens,
                "automatic_memory": False, "file_reads_configured": bool(settings.read_roots or settings.write_roots),
                "file_writes_configured": bool(settings.write_roots)}

    @app.post("/v1/conversations", status_code=201, tags=["conversations"])
    async def create_conversation(body: ConversationCreate):
        identifier = uid()
        app.state.store.execute("INSERT INTO conversations VALUES(?,?,?,?)",
                                (identifier, "local", app.state.redactor.text(body.title), app.state.store.clock()))
        return app.state.store.one("SELECT * FROM conversations WHERE id=?", (identifier,))

    @app.get("/v1/conversations", tags=["conversations"])
    async def conversations():
        return app.state.store.rows("SELECT * FROM conversations WHERE owner='local' ORDER BY created DESC LIMIT 200")

    @app.get("/v1/conversations/{identifier}", tags=["conversations"])
    async def conversation(identifier: str):
        row = app.state.store.one("SELECT * FROM conversations WHERE owner='local' AND id=?", (identifier,))
        row["messages"] = app.state.store.rows("SELECT * FROM messages WHERE conversation=? ORDER BY id", (identifier,))
        return row

    @app.delete("/v1/conversations/{identifier}", tags=["conversations"])
    async def delete_conversation(identifier: str):
        # This explicit authenticated DELETE is exact authorization to delete the
        # conversation; the model has no such tool.
        await conversation(identifier)
        for run in app.state.store.rows("SELECT * FROM runs WHERE conversation=?", (identifier,)):
            await app.state.runtime.cancel("local", run["id"])
        app.state.store.execute("DELETE FROM conversations WHERE owner='local' AND id=?", (identifier,))
        return {"deleted": identifier}

    @app.post("/v1/conversations/{identifier}/runs", status_code=202, tags=["runs"])
    async def start_run(identifier: str, body: RunInput, request: Request):
        return await app.state.runtime.start("local", identifier, body, request.state.request_id)

    @app.get("/v1/runs/{identifier}", tags=["runs"])
    async def get_run(identifier: str):
        return app.state.runtime.get("local", identifier)

    @app.post("/v1/runs/{identifier}/cancel", tags=["runs"])
    async def cancel_run(identifier: str):
        return await app.state.runtime.cancel("local", identifier)

    @app.get("/v1/runs/{identifier}/events", tags=["runs"], response_class=StreamingResponse,
             responses={200: {"content": {"text/event-stream": {"schema": {"type": "string"}}}}})
    async def run_events(identifier: str, request: Request, after: int = 0):
        app.state.runtime.get("local", identifier)
        try:
            cursor = max(after, int(request.headers.get("Last-Event-ID", "0")))
        except ValueError:
            raise AppError("invalid_cursor", "Last-Event-ID must be an integer.", 422) from None

        async def events():
            nonlocal cursor
            while True:
                changed = app.state.runtime.changed
                terminal = app.state.runtime.get("local", identifier)["status"] in TERMINAL
                rows = app.state.store.events("local", identifier, cursor)
                for event in rows:
                    cursor = event["event_id"]
                    yield f"id: {cursor}\nevent: {event['event_type']}\ndata: {json.dumps(event)}\n\n"
                if len(rows) == 500:
                    continue
                if terminal:
                    break
                if await request.is_disconnected():
                    break
                try:
                    await asyncio.wait_for(changed.wait(), timeout=15)
                except TimeoutError:
                    yield ": heartbeat\n\n"
        return StreamingResponse(events(), media_type="text/event-stream", headers={"X-Accel-Buffering": "no"})

    @app.get("/v1/approvals", tags=["approvals"])
    async def approvals():
        app.state.store.execute("UPDATE approvals SET status='expired' WHERE status IN ('pending','approved') AND expires<=?",
                                (app.state.store.clock(),))
        rows = app.state.store.rows("SELECT * FROM approvals WHERE owner='local' ORDER BY created DESC LIMIT 200")
        for row in rows:
            row["arguments"] = json.loads(row["arguments"])
        return rows

    @app.post("/v1/approvals/{identifier}/decision", tags=["approvals"])
    async def decide(identifier: str, body: ApprovalDecision):
        row = app.state.authorization.decide("local", identifier, body.approve, body.digest)
        if row["run"] or not body.approve:
            return {"status": "approved" if body.approve else "rejected"}
        args = json.loads(row["arguments"])
        app.state.authorization.consume("local", identifier, row["tool"], args)
        return await app.state.registry.execute("local", row["tool"], args)

    @app.post("/v1/tools/{name}/invoke", tags=["tools"])
    async def invoke(name: str, body: ToolInvocation):
        return await app.state.authorization.invoke("local", name, body.arguments)

    def item_routes(kind, plural):
        async def listing(query: str = ""):
            return await app.state.authorization.invoke("local", f"{kind}_list", {"query": query})

        async def create(body: ItemCreate):
            return await app.state.authorization.invoke("local", f"{kind}_create", body.model_dump())

        async def update(identifier: str, body: ItemUpdate):
            if body.id != identifier:
                raise AppError("identifier_mismatch", "Body id must match the route id.", 422)
            return await app.state.authorization.invoke("local", f"{kind}_update", body.model_dump())

        async def delete(identifier: str):
            return await app.state.authorization.invoke("local", f"{kind}_delete", {"id": identifier})

        app.add_api_route(f"/v1/{plural}", listing, methods=["GET"], name=f"list_{plural}", tags=[plural])
        app.add_api_route(f"/v1/{plural}", create, methods=["POST"], name=f"create_{kind}", tags=[plural])
        app.add_api_route(f"/v1/{plural}/{{identifier}}", update, methods=["PUT"], name=f"update_{kind}", tags=[plural])
        app.add_api_route(f"/v1/{plural}/{{identifier}}", delete, methods=["DELETE"], name=f"delete_{kind}", tags=[plural])

    for kind, plural in (("task", "tasks"), ("note", "notes"), ("memory", "memories")):
        item_routes(kind, plural)

    @app.get("/v1/memory-export", tags=["memories"])
    async def export_memory():
        rows = app.state.store.rows("SELECT * FROM items WHERE owner='local' AND kind='memory' ORDER BY created")
        return JSONResponse({"version": 1, "memories": rows}, headers={"Content-Disposition": 'attachment; filename="memories.json"'})

    @app.post("/v1/reminders", tags=["reminders"])
    async def create_reminder(body: ReminderCreate):
        return await app.state.authorization.invoke("local", "reminder_create", body.model_dump(mode="json"))

    @app.get("/v1/reminders", tags=["reminders"])
    async def reminders():
        return app.state.scheduler.list("local")

    @app.post("/v1/reminders/{identifier}/{operation}", tags=["reminders"])
    async def change_reminder(identifier: str, operation: str):
        if operation not in {"pause", "resume", "cancel"}:
            raise AppError("invalid_operation", "Use pause, resume or cancel.", 422)
        return await app.state.authorization.invoke("local", f"reminder_{operation}", {"id": identifier})

    @app.get("/v1/notifications", tags=["reminders"])
    async def notifications(after: int = 0):
        return app.state.store.events("local", after=after, notifications=True)

    @app.get("/v1/job-executions", tags=["reminders"])
    async def executions():
        return app.state.store.rows("SELECT e.* FROM job_executions e JOIN jobs j ON e.job=j.id WHERE j.owner='local' ORDER BY e.executed_at DESC LIMIT 200")

    @app.post("/v1/jobs", tags=["jobs"])
    async def create_job(body: ScheduledToolCreate):
        return app.state.scheduler.create_tool_job("local", body)

    @app.get("/v1/jobs", tags=["jobs"])
    async def jobs():
        return app.state.scheduler.list("local")

    @app.post("/v1/jobs/{identifier}/{operation}", tags=["jobs"])
    async def change_job(identifier: str, operation: str):
        return await change_reminder(identifier, operation)

    async def interruptible(request, coroutine):
        task = asyncio.create_task(coroutine)
        try:
            while not task.done():
                if await request.is_disconnected():
                    raise AppError("client_disconnected", "Voice request cancelled.", 499)
                await asyncio.wait([task], timeout=0.1)
            return await task
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    @app.post("/v1/voice/speech", tags=["voice"], response_class=Response)
    async def speech(body: SpeechInput, request: Request):
        app.state.redactor.reject_secret(body.text)
        audio = await interruptible(request, app.state.providers.synthesize(body.text, body.format))
        return Response(audio, media_type={"mp3": "audio/mpeg", "wav": "audio/wav", "opus": "audio/ogg"}[body.format])

    @app.post("/v1/voice/transcribe", tags=["voice"])
    async def transcribe(body: TranscriptionInput, request: Request):
        try:
            audio = base64.b64decode(body.audio_base64, validate=True)
        except (ValueError, binascii.Error):
            raise AppError("audio_invalid", "Audio must be valid base64.", 422) from None
        signatures = {"wav": audio.startswith(b"RIFF") and audio[8:12] == b"WAVE",
                      "mp3": audio.startswith(b"ID3") or (len(audio) > 2 and audio[0] == 255 and audio[1] & 224 == 224),
                      "flac": audio.startswith(b"fLaC"), "ogg": audio.startswith(b"OggS"),
                      "webm": audio.startswith(b"\x1a\x45\xdf\xa3")}
        if len(audio) > 1024 * 1024 or len(audio) < 12 or not signatures[body.format]:
            raise AppError("audio_invalid", "Audio format mismatch, empty audio, or audio exceeds 1 MiB.", 422)
        text = await interruptible(request, app.state.providers.transcribe(audio, body.format))
        return {"text": app.state.redactor.text(text)}

    @app.get("/v1/audit", tags=["diagnostics"])
    async def audit():
        return app.state.store.rows("SELECT * FROM audit WHERE owner='local' ORDER BY id DESC LIMIT 200")

    @app.get("/v1/metrics", tags=["diagnostics"])
    async def metrics():
        return {"runs": app.state.store.rows("SELECT status,COUNT(*) AS count FROM runs WHERE owner='local' GROUP BY status"),
                "tools": app.state.store.rows("SELECT tool,decision,COUNT(*) AS count,AVG(duration_ms) AS mean_duration_ms FROM audit WHERE owner='local' GROUP BY tool,decision")}

    original_openapi = app.openapi

    def openapi():
        schema = original_openapi()
        schema.setdefault("components", {})["securitySchemes"] = {"BearerAuth": {"type": "http", "scheme": "bearer"}}
        schema["security"] = [{"BearerAuth": []}]
        schema["paths"]["/v1/health"]["get"]["security"] = []
        return schema

    app.openapi = openapi
    return app