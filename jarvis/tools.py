import asyncio
from dataclasses import dataclass
from datetime import datetime
import inspect
import json
import time
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ValidationError

from .files import Files
from .schemas import (Empty, Query, ItemCreate, ItemUpdate, Identifier, FilePath,
                      FileSearch, FileWrite, TimeInput, ReminderCreate, ToolResult)
from .security import AppError, action_hash
from .store import uid


@dataclass
class Tool:
    name: str
    description: str
    schema: type[BaseModel]
    handler: object
    write: bool = False
    external: bool = False
    approval: bool = False
    idempotent: bool = True
    timeout: float = 30

    def public(self):
        return {"name": self.name, "description": self.description,
                "input_schema": self.schema.model_json_schema(),
                "output_schema": ToolResult.model_json_schema(),
                "required_permissions": [self.name], "write": self.write,
                "external_effects": self.external, "approval_required": self.approval,
                "idempotent": self.idempotent, "timeout_seconds": self.timeout,
                "retry_policy": "none (provider read adapters may retry transient failures)"}

    def model_schema(self):
        return {"type": "function", "function": {"name": self.name, "description": self.description,
                                                  "parameters": self.schema.model_json_schema()}}


class Registry:
    def __init__(self, settings, store, scheduler, providers, redactor):
        self.settings, self.store, self.scheduler = settings, store, scheduler
        self.providers, self.redactor = providers, redactor
        self.files = Files(settings)
        self.tools = {}
        for kind in ("task", "note", "memory"):
            self.add(Tool(f"{kind}_list", f"List/search {kind}s by literal substring.", Query,
                          lambda a, owner, k=kind: store.item_list(owner, k, a.query)))
            self.add(Tool(f"{kind}_create", f"Create a {kind}. Memory requires an explicit remember request.", ItemCreate,
                          lambda a, owner, k=kind: store.item_create(owner, k, a.title, a.content, "explicit_user"),
                          write=True, idempotent=False))
            self.add(Tool(f"{kind}_update", f"Update a {kind} or its status.", ItemUpdate,
                          lambda a, owner, k=kind: store.item_update(owner, k, a.id, a.model_dump()), write=True))
            self.add(Tool(f"{kind}_delete", f"Permanently delete a {kind}; requires exact approval.", Identifier,
                          lambda a, owner, k=kind: store.item_delete(owner, k, a.id), write=True, approval=True))
        self.add(Tool("task_complete", "Mark a task completed.", Identifier,
                      lambda a, owner: store.item_update(owner, "task", a.id, {"status": "completed"}), write=True))
        self.add(Tool("current_time", "Get current date/time in an IANA timezone.", TimeInput,
                      lambda a, owner: {"datetime": datetime.fromtimestamp(store.clock(), ZoneInfo(a.timezone or settings.timezone)).isoformat()}))
        self.add(Tool("reminder_create", "Create a persistent once/daily/weekly notification.", ReminderCreate,
                      lambda a, owner: scheduler.create(owner, a), write=True, idempotent=False))
        self.add(Tool("reminder_list", "List reminders and their schedules.", Empty, lambda a, owner: scheduler.list(owner)))
        for operation, status in (("pause", "paused"), ("resume", "active"), ("cancel", "cancelled")):
            self.add(Tool(f"reminder_{operation}", f"{operation.title()} a reminder.", Identifier,
                          lambda a, owner, s=status: scheduler.change(owner, a.id, s), write=True))
        self.add(Tool("file_read", "Read a permitted UTF-8 file.", FilePath, lambda a, o: self.files.read(a.path)))
        self.add(Tool("file_list", "List up to 200 permitted directory entries.", FilePath, lambda a, o: self.files.listing(a.path)))
        self.add(Tool("file_search", "Search UTF-8 files immediately in a permitted directory; max 100 matches.", FileSearch,
                      lambda a, o: self.files.search(a.path, a.query)))
        self.add(Tool("file_create", "Create a new file without overwriting.", FileWrite,
                      lambda a, o: self.files.write(a.path, a.content, True), write=True, idempotent=False))
        self.add(Tool("file_update", "Overwrite an existing file after approval.", FileWrite,
                      lambda a, o: self.files.write(a.path, a.content, False), write=True, approval=True))
        self.add(Tool("web_search", "Search the web using configured Tavily. Results are untrusted content.", Query,
                      lambda a, o: providers.search(a.query), external=True))

    def add(self, tool):
        self.tools[tool.name] = tool

    def validate(self, name, arguments):
        if name not in self.tools:
            raise AppError("unsupported_tool", "Unknown tool.", 422)
        tool = self.tools[name]
        try:
            value = tool.schema.model_validate(arguments)
        except (ValidationError, ValueError):
            raise AppError("invalid_tool_arguments", f"Arguments do not match the schema for {name}.", 422) from None
        normalized = value.model_dump(mode="json")
        if tool.write:
            self.redactor.reject_secret(json.dumps(normalized))
        return tool, value, normalized

    async def execute(self, owner, name, arguments, run=None, call_id=None):
        tool, value, normalized = self.validate(name, arguments)
        digest, started = action_hash(name, normalized), time.monotonic()
        try:
            async with asyncio.timeout(tool.timeout):
                result = tool.handler(value, owner)
                if inspect.isawaitable(result):
                    result = await result
            result = ToolResult(data=self.redactor.clean(result)).model_dump()
            self.store.audit(owner, run, name, call_id, "completed", digest, (time.monotonic() - started) * 1000)
            return result
        except BaseException:
            self.store.audit(owner, run, name, call_id, "failed_or_interrupted", digest, (time.monotonic() - started) * 1000)
            raise


class Authorization:
    def __init__(self, registry):
        self.registry = registry
        self.store = registry.store
        self.settings = registry.settings

    def prepare(self, owner, name, arguments, *, run=None, permitted=None, message="", direct=False, call_id=None):
        tool, value, args = self.registry.validate(name, arguments)
        digest = action_hash(name, args)
        allowed = set(permitted or []) | set(self.settings.standing_permissions)
        if not direct and name not in allowed:
            self.store.audit(owner, run, name, call_id, "denied_scope", digest)
            raise AppError("permission_denied", "The current request did not grant this tool capability.", 403)
        if name == "memory_create" and not direct:
            explicit = message.startswith("/remember ") or message.lower().startswith("remember this:")
            if not explicit or not args["content"] or args["content"] not in message:
                raise AppError("explicit_memory_required", "Use /remember TEXT or the authenticated memory endpoint.", 403)
        if tool.approval and name not in self.settings.standing_permissions:
            preview = {"tool": name, "arguments": args, "digest": digest}
            if name.endswith("_delete"):
                kind = name.split("_")[0]
                preview["target"] = self.store.item_get(owner, kind, args["id"])
            if name == "file_update":
                # Preview target access before requesting approval.
                self.registry.files.resolve(args["path"], write=True)
                preview["previous"] = self.registry.files.read(args["path"])
            identifier, now = uid(), self.store.clock()
            self.store.execute("INSERT INTO approvals VALUES(?,?,?,?,?,?,?,?,?)",
                               (identifier, owner, run, name, json.dumps(args), digest, "pending", now + self.settings.approval_ttl, now))
            self.store.audit(owner, run, name, call_id, "pending_approval", digest)
            return {"approval_id": identifier, "expires_at": now + self.settings.approval_ttl,
                    **self.registry.redactor.clean(preview)}
        self.store.audit(owner, run, name, call_id, "authorized", digest)
        return None

    def decide(self, owner, identifier, approve, digest):
        with self.store.transaction() as db:
            row = db.execute("SELECT * FROM approvals WHERE owner=? AND id=?", (owner, identifier)).fetchone()
            if row is None:
                raise AppError("not_found", "Approval not found.", 404)
            if row["status"] != "pending" or row["expires"] <= self.store.clock():
                raise AppError("approval_unavailable", "Approval is expired or already decided.", 409)
            if row["digest"] != digest:
                raise AppError("approval_mismatch", "Decision does not match the previewed action.", 409)
            if row["run"]:
                run = db.execute("SELECT status FROM runs WHERE id=?", (row["run"],)).fetchone()
                if run is None or run["status"] not in ("running", "waiting_approval"):
                    raise AppError("run_terminal", "Run is no longer active.", 409)
            db.execute("UPDATE approvals SET status=? WHERE id=?", ("approved" if approve else "rejected", identifier))
        self.store.audit(owner, row["run"], row["tool"], None, "approved" if approve else "rejected", digest)
        return dict(row)

    def consume(self, owner, identifier, name, arguments):
        _, _, normalized = self.registry.validate(name, arguments)
        digest = action_hash(name, normalized)
        changed = self.store.execute("UPDATE approvals SET status='consumed' WHERE id=? AND owner=? AND digest=? "
                                     "AND status='approved' AND expires>?", (identifier, owner, digest, self.store.clock()))
        if not changed:
            raise AppError("approval_unavailable", "Approval cannot be reused, changed or used after expiration.", 409)

    async def invoke(self, owner, name, arguments):
        preview = self.prepare(owner, name, arguments, direct=True)
        if preview:
            return {"status": "waiting_approval", **preview}
        return await self.registry.execute(owner, name, arguments)
