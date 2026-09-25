import asyncio
from datetime import datetime, timedelta, timezone
import json
from zoneinfo import ZoneInfo

from .store import uid
from .security import AppError


def next_wall_time(previous: float, zone: str, recurrence: str, local_time: str, now: float):
    """Skip missed occurrences. Gap: first valid minute; fold: earlier occurrence."""
    tz = ZoneInfo(zone)
    old_date = datetime.fromtimestamp(previous, tz).date()
    today = datetime.fromtimestamp(now, tz).date()
    step = 7 if recurrence == "weekly" else 1
    days = max(1, (today - old_date).days // step)
    date = old_date + timedelta(days=days * step)
    hour, minute, second = map(int, local_time.split(":"))
    while True:
        naive = datetime(date.year, date.month, date.day, hour, minute, second)
        for _ in range(181):
            candidate = naive.replace(tzinfo=tz, fold=0)
            roundtrip = candidate.astimezone(timezone.utc).astimezone(tz)
            if roundtrip.replace(tzinfo=None) == naive:
                break
            naive += timedelta(minutes=1)
        if candidate.timestamp() > now:
            return candidate.timestamp()
        date += timedelta(days=step)


class Scheduler:
    def __init__(self, store):
        self.store = store
        self.registry = None
        self.authorization = None

    def create(self, owner, args):
        identifier, now = uid(), self.store.clock()
        self.store.execute("INSERT INTO jobs(id,owner,title,content,status,next_at,timezone,recurrence,local_time,created) VALUES(?,?,?,?,?,?,?,?,?,?)",
                           (identifier, owner, args.title, args.content, "active", args.at.timestamp(),
                            args.timezone, args.recurrence,
                            args.at.astimezone(ZoneInfo(args.timezone)).strftime("%H:%M:%S"), now))
        return self.get(owner, identifier)

    def create_tool_job(self, owner, args):
        # Immutable operation/arguments and the authenticated request form the
        # standing authorization for this specific schedule. No inferred grants.
        tool, _, normalized = self.registry.validate(args.tool, args.arguments)
        if tool.approval or tool.external or args.tool not in {"task_create", "task_complete", "note_create"}:
            raise AppError("schedule_denied", "Only supported reversible local tools can be scheduled.", 403)
        self.registry.redactor.reject_secret(args.title + args.content)
        with self.store.transaction():
            job = self.create(owner, args)
            self.store.execute("UPDATE jobs SET tool=?,arguments=? WHERE id=?",
                               (args.tool, json.dumps(normalized), job["id"]))
        return self.get(owner, job["id"])

    def get(self, owner, identifier):
        return self.store.one("SELECT * FROM jobs WHERE owner=? AND id=?", (owner, identifier))

    def list(self, owner):
        return self.store.rows("SELECT * FROM jobs WHERE owner=? ORDER BY next_at LIMIT 200", (owner,))

    def change(self, owner, identifier, status):
        job = self.get(owner, identifier)
        if job["status"] in {"completed", "cancelled"}:
            raise AppError("job_terminal", "Completed or cancelled jobs cannot be resumed.", 409)
        self.store.execute("UPDATE jobs SET status=? WHERE owner=? AND id=?", (status, owner, identifier))
        return self.get(owner, identifier)

    def tick(self):
        now = self.store.clock()
        # Claim, notification and schedule advance are a single durable transaction.
        # BEGIN IMMEDIATE serializes competing scheduler processes.
        with self.store.transaction() as db:
            jobs = db.execute("SELECT * FROM jobs WHERE status='active' AND next_at<=? ORDER BY next_at LIMIT 100", (now,)).fetchall()
            for job in jobs:
                inserted = db.execute("INSERT OR IGNORE INTO job_executions VALUES(?,?,?,'completed')",
                                      (job["id"], job["next_at"], now)).rowcount
                if inserted:
                    payload = {"job_id": job["id"], "title": job["title"], "content": job["content"],
                               "scheduled_at": job["next_at"], "late": now - job["next_at"] > 60}
                    event_type = "notification.reminder"
                    if job["tool"]:
                        from .schemas import ToolResult
                        from .security import action_hash
                        try:
                            args = json.loads(job["arguments"])
                            self.authorization.prepare(job["owner"], job["tool"], args, permitted=[job["tool"]])
                            tool, validated, normalized = self.registry.validate(job["tool"], args)
                            result = tool.handler(validated, job["owner"])
                            payload["result"] = ToolResult(data=self.registry.redactor.clean(result)).model_dump()
                            self.store.audit(job["owner"], None, job["tool"], job["id"], "completed", action_hash(job["tool"], normalized))
                            event_type = "notification.job_completed"
                        except AppError as exc:
                            payload["error_code"] = exc.code
                            event_type = "notification.job_failed"
                            db.execute("UPDATE job_executions SET status='failed' WHERE job=? AND scheduled_at=?", (job["id"], job["next_at"]))
                    db.execute("INSERT INTO events(owner,conversation,run,type,timestamp,payload) VALUES(?,NULL,NULL,?,?,?)",
                               (job["owner"], event_type, now, json.dumps(payload)))
                if job["recurrence"] == "once":
                    db.execute("UPDATE jobs SET status='completed' WHERE id=?", (job["id"],))
                else:
                    following = next_wall_time(job["next_at"], job["timezone"], job["recurrence"], job["local_time"], now)
                    db.execute("UPDATE jobs SET next_at=? WHERE id=?", (following, job["id"]))

    async def serve(self):
        while True:
            self.tick()
            await asyncio.sleep(0.5)
