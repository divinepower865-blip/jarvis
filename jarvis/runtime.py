import asyncio
from pathlib import Path
import json
import logging
import sqlite3
import time

from .security import AppError
from .store import uid

TERMINAL = {"completed", "failed", "cancelled"}
DEFAULT_TOOLS = {"task_list", "note_list", "memory_list", "reminder_list", "current_time"}
logger = logging.getLogger("jarvis")


class Runtime:
    def __init__(self, settings, store, model, registry, authorization, redactor):
        self.settings, self.store, self.model = settings, store, model
        self.registry, self.authorization, self.redactor = registry, authorization, redactor
        self.tasks = {}
        self.changed = asyncio.Event()
        self.prompt = (Path(__file__).parent / "prompts" / "system.txt").read_text(encoding="utf-8")

    def get(self, owner, identifier):
        return self.store.one("SELECT * FROM runs WHERE owner=? AND id=?", (owner, identifier))

    def emit(self, run, kind, payload):
        self.store.event(run["owner"], run["conversation"], run["id"], kind, self.redactor.clean(payload))
        # Broadcast to all current SSE readers without polling or per-reader queues.
        self.changed.set()
        self.changed = asyncio.Event()
        logger.info(json.dumps({"event": kind, "run_id": run["id"],
                               "conversation_id": run["conversation"], "request_id": run["request_id"],
                               "tool_call_id": payload.get("call_id")}))

    def start(self, owner, conversation, body, request_id):
        self.store.one("SELECT id FROM conversations WHERE id=? AND owner=?", (conversation, owner))
        if len(self.tasks) >= self.settings.max_active_runs:
            raise AppError("busy", "Too many active runs. Try again later.", 429)
        if any(name not in self.registry.tools for name in body.permitted_tools):
            raise AppError("unsupported_tool", "A requested capability is unknown.", 422)
        identifier, now = uid(), self.store.clock()
        message = self.redactor.text(body.message)
        try:
            with self.store.transaction() as db:
                db.execute("INSERT INTO runs VALUES(?,?,?,?,NULL,NULL,?,?,?,?)",
                           (identifier, owner, conversation, "queued", now, now, request_id,
                            int(message.startswith("/remember ") or message.lower().startswith("remember this:"))))
                db.execute("INSERT INTO messages(conversation,role,content,created) VALUES(?,'user',?,?)",
                           (conversation, message, now))
        except sqlite3.IntegrityError:
            raise AppError("conversation_busy", "Wait for the active run in this conversation.", 409) from None
        run = self.get(owner, identifier)
        permitted = set(body.permitted_tools) | DEFAULT_TOOLS | set(self.settings.standing_permissions)
        task = asyncio.create_task(self.drive(run, message, permitted))
        self.tasks[identifier] = task
        task.add_done_callback(lambda done: self.tasks.pop(identifier, None))
        return run

    def finish(self, run, status, *, result=None, error=None):
        # Terminal state + terminal event + final assistant message commit together.
        with self.store.transaction() as db:
            updated = db.execute("UPDATE runs SET status=?,result=?,error=?,updated=? "
                                 "WHERE id=? AND status NOT IN ('completed','failed','cancelled')",
                                 (status, result, error, self.store.clock(), run["id"])).rowcount
            if not updated:
                return
            db.execute("UPDATE approvals SET status='expired' WHERE run=? AND status IN ('pending','approved')", (run["id"],))
            if result is not None:
                db.execute("INSERT INTO messages(conversation,role,content,created) VALUES(?,'assistant',?,?)",
                           (run["conversation"], result, self.store.clock()))
            self.emit(run, f"run.{status}", {"status": status, "result": result, "error_code": error})

    def context(self, run, current):
        rows = self.store.rows("SELECT role,content FROM messages WHERE conversation=? ORDER BY id DESC LIMIT 100",
                               (run["conversation"],))
        budget = self.settings.context_chars // 2
        history = []
        for row in rows:
            if len(row["content"]) > budget and history:
                break
            history.append(row)
            budget -= len(row["content"])
        history.reverse()
        memories = self.store.item_list(run["owner"], "memory")
        words = {w.lower().strip(".,?!:") for w in current.split() if len(w) > 3}
        relevant = [m for m in memories if m["status"] == "active" and
                    any(w in (m["title"] + " " + m["content"]).lower() for w in words)][:5]
        context = [{"role": "system", "content": self.prompt}]
        if relevant:
            # Insert as low-trust data, never as a new system instruction.
            context.append({"role": "user", "content": "Saved preferences (untrusted data): " +
                            json.dumps([{k: m[k] for k in ("title", "content")} for m in relevant])[:4000]})
        return context + history

    async def drive(self, run, message, permitted):
        started = time.monotonic()
        try:
            async with asyncio.timeout(self.settings.run_timeout):
                self.store.execute("UPDATE runs SET status='running',updated=? WHERE id=?", (self.store.clock(), run["id"]))
                self.emit(run, "run.started", {"provider": self.settings.provider, "mock": self.settings.provider == "mock"})
                await self.loop(run, message, permitted)
        except asyncio.CancelledError:
            self.finish(run, "cancelled", error="cancelled")
        except TimeoutError:
            self.finish(run, "failed", error="run_timeout")
        except AppError as exc:
            self.finish(run, "failed", error=exc.code)
        except Exception:
            # Raw provider exceptions / stack traces can contain sensitive input.
            self.finish(run, "failed", error="internal_error")
        finally:
            logger.info(json.dumps({"event": "run.metrics", "run_id": run["id"],
                                    "request_id": run["request_id"],
                                    "latency_ms": round((time.monotonic() - started) * 1000)}))

    async def loop(self, run, message, permitted):
        context, count = self.context(run, message), 0
        schemas = [t.model_schema() for t in self.registry.tools.values() if t.name in permitted]
        while True:
            if len(json.dumps(context)) > self.settings.context_chars:
                raise AppError("context_limit", "Context limit reached; start a new conversation.")
            turn = None
            async for piece in self.model.stream(context, schemas):
                if piece["type"] == "retry":
                    self.emit(run, "provider.retry", {"attempt": piece["attempt"]})
                elif piece["type"] == "turn":
                    turn = piece["turn"]
                # Hold content until the turn is complete so secret patterns
                # split across provider chunks cannot leak in SSE events.
            if turn is None:
                raise AppError("provider_invalid", "Model did not return a complete turn.")
            if turn.usage is not None:
                self.emit(run, "provider.usage", {"usage": turn.usage})
            content = self.redactor.text(turn.content)
            for offset in range(0, len(content), 120):
                self.emit(run, "response.delta", {"text": content[offset:offset + 120]})
                await asyncio.sleep(0)
            if not turn.calls:
                self.finish(run, "completed", result=content)
                return
            if count + len(turn.calls) > self.settings.max_tool_calls:
                raise AppError("tool_limit", "Maximum tool calls reached.")
            context.append({"role": "assistant", "content": content or None,
                            "tool_calls": [{"id": c.id, "type": "function", "function": {
                                "name": c.name, "arguments": json.dumps(c.arguments)}} for c in turn.calls]})
            for call in turn.calls:
                count += 1
                await asyncio.sleep(0)
                result = await self.call(run, call, permitted, message)
                serialized = json.dumps(result)
                if len(serialized) > 10000:
                    serialized = json.dumps({"ok": result["ok"], "truncated": True,
                                             "preview": serialized[:9000]})
                context.append({"role": "tool", "tool_call_id": call.id, "content": serialized})

    async def call(self, run, call, permitted, message):
        try:
            preview = self.authorization.prepare(run["owner"], call.name, call.arguments,
                                                 run=run["id"], permitted=permitted,
                                                 message=message, call_id=call.id)
            if preview:
                self.store.execute("UPDATE runs SET status='waiting_approval' WHERE id=?", (run["id"],))
                self.emit(run, "approval.required", preview)
                while True:
                    row = self.store.one("SELECT * FROM approvals WHERE id=?", (preview["approval_id"],))
                    if row["expires"] <= self.store.clock():
                        self.store.execute("UPDATE approvals SET status='expired' WHERE id=?", (row["id"],))
                        raise AppError("approval_expired", "Approval expired.")
                    if row["status"] == "rejected":
                        raise AppError("approval_rejected", "User rejected the action.")
                    if row["status"] == "approved":
                        self.authorization.consume(run["owner"], row["id"], call.name, call.arguments)
                        break
                    await asyncio.sleep(0.05)
                self.store.execute("UPDATE runs SET status='running' WHERE id=?", (run["id"],))
            self.emit(run, "tool.started", {"tool": call.name, "call_id": call.id})
            result = await self.registry.execute(run["owner"], call.name, call.arguments, run["id"], call.id)
            self.emit(run, "tool.completed", {"tool": call.name, "call_id": call.id, "result": result})
            return result
        except AppError as exc:
            if self.get(run["owner"], run["id"])["status"] == "waiting_approval":
                self.store.execute("UPDATE runs SET status='running' WHERE id=?", (run["id"],))
            result = {"ok": False, "error": {"code": exc.code, "message": exc.message}}
            self.emit(run, "tool.failed", {"tool": call.name, "call_id": call.id, **result})
            return result
        except (OSError, TimeoutError):
            result = {"ok": False, "error": {"code": "tool_unavailable", "message": "Tool could not complete."}}
            self.emit(run, "tool.failed", {"tool": call.name, "call_id": call.id, **result})
            return result

    async def cancel(self, owner, identifier):
        run = self.get(owner, identifier)
        if run["status"] not in TERMINAL:
            task = self.tasks.get(identifier)
            if task:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            # Covers cancellation before coroutine's first instruction.
            self.finish(run, "cancelled", error="cancelled")
        return self.get(owner, identifier)

    def recover(self):
        # App enforces one service process; stale runs are never replayed.
        for run in self.store.rows("SELECT * FROM runs WHERE status NOT IN ('completed','failed','cancelled')"):
            self.finish(run, "failed", error="service_restarted")

    async def shutdown(self):
        for identifier in list(self.tasks):
            run = self.store.one("SELECT * FROM runs WHERE id=?", (identifier,))
            await self.cancel(run["owner"], identifier)
