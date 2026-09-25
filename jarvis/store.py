"""Short, serialized SQLite transactions. No network or awaits inside transactions."""
from contextlib import contextmanager
from pathlib import Path
import json
import sqlite3
import threading
import time
import uuid

from .security import AppError
from .events import EVENT_ADAPTER, PAYLOADS


def uid():
    return uuid.uuid4().hex


class Store:
    def __init__(self, path: Path, clock=time.time):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.clock = clock
        self.lock = threading.RLock()
        self.db = sqlite3.connect(path, check_same_thread=False, isolation_level=None, timeout=5)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA busy_timeout=5000")
        self.db.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER PRIMARY KEY)")
        version = self.db.execute("SELECT COALESCE(MAX(version),0) FROM schema_version").fetchone()[0]
        for migration in sorted((Path(__file__).parent / "migrations").glob("*.sql")):
            if int(migration.name.split("_")[0]) > version:
                self.db.executescript("BEGIN IMMEDIATE;\n" + migration.read_text() + "\nCOMMIT;")

    @contextmanager
    def transaction(self):
        with self.lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                yield self.db
                self.db.execute("COMMIT")
            except BaseException:
                self.db.execute("ROLLBACK")
                raise

    def execute(self, sql, args=()):
        with self.lock:
            return self.db.execute(sql, args).rowcount

    def rows(self, sql, args=()):
        with self.lock:
            return [dict(r) for r in self.db.execute(sql, args).fetchall()]

    def one(self, sql, args=()):
        rows = self.rows(sql, args)
        if not rows:
            raise AppError("not_found", "Resource not found.", 404)
        return rows[0]

    def event(self, owner, conversation, run, kind, payload):
        PAYLOADS[kind].model_validate(payload)
        with self.lock:
            cur = self.db.execute(
                "INSERT INTO events(owner,conversation,run,type,timestamp,payload) VALUES(?,?,?,?,?,?)",
                (owner, conversation, run, kind, self.clock(), json.dumps(payload)))
            return cur.lastrowid

    def events(self, owner, run=None, after=0, notifications=False):
        condition = "run IS NULL" if notifications else "run=?"
        args = (owner, after) if notifications else (owner, after, run)
        rows = self.rows(f"SELECT * FROM events WHERE owner=? AND id>? AND {condition} ORDER BY id LIMIT 500", args)
        events = [{"event_id": r["id"], "event_type": r["type"],
                 "conversation_id": r["conversation"], "run_id": r["run"],
                 "timestamp": r["timestamp"], "payload": json.loads(r["payload"])} for r in rows]
        for event in events:
            EVENT_ADAPTER.validate_python(event)
        return events

    def audit(self, owner, run, tool, call_id, decision, digest, duration=None):
        self.execute("INSERT INTO audit(owner,run,tool,call_id,decision,digest,duration_ms,created) VALUES(?,?,?,?,?,?,?,?)",
                     (owner, run, tool, call_id, decision, digest, duration, self.clock()))

    def item_create(self, owner, kind, title, content, source):
        now, identifier = self.clock(), uid()
        self.execute("INSERT INTO items VALUES(?,?,?,?,?,?,?,?,?)",
                     (identifier, owner, kind, title, content, "active", source, now, now))
        return self.item_get(owner, kind, identifier)

    def item_get(self, owner, kind, identifier):
        return self.one("SELECT * FROM items WHERE owner=? AND kind=? AND id=?", (owner, kind, identifier))

    def item_list(self, owner, kind, query=""):
        # Literal substring search, not user-controlled SQL wildcards.
        return self.rows("SELECT * FROM items WHERE owner=? AND kind=? AND "
                         "(instr(lower(title),lower(?))>0 OR instr(lower(content),lower(?))>0) "
                         "ORDER BY updated DESC LIMIT 200", (owner, kind, query, query))

    def item_update(self, owner, kind, identifier, fields):
        old = self.item_get(owner, kind, identifier)
        for key in ("title", "content", "status"):
            if fields.get(key) is not None:
                old[key] = fields[key]
        self.execute("UPDATE items SET title=?,content=?,status=?,updated=? WHERE owner=? AND kind=? AND id=?",
                     (old["title"], old["content"], old["status"], self.clock(), owner, kind, identifier))
        return self.item_get(owner, kind, identifier)

    def item_delete(self, owner, kind, identifier):
        self.item_get(owner, kind, identifier)
        self.execute("DELETE FROM items WHERE owner=? AND kind=? AND id=?", (owner, kind, identifier))
        return {"deleted": identifier}

    def close(self):
        self.db.close()
