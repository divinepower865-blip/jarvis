CREATE TABLE IF NOT EXISTS schema_version (version INTEGER PRIMARY KEY);
CREATE TABLE conversations (
 id TEXT PRIMARY KEY, owner TEXT NOT NULL, title TEXT NOT NULL,
 created REAL NOT NULL
);
CREATE TABLE messages (
 id INTEGER PRIMARY KEY AUTOINCREMENT, conversation TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
 role TEXT NOT NULL, content TEXT NOT NULL, created REAL NOT NULL
);
CREATE TABLE runs (
 id TEXT PRIMARY KEY, owner TEXT NOT NULL, conversation TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
 status TEXT NOT NULL, result TEXT, error TEXT, created REAL NOT NULL, updated REAL NOT NULL,
 request_id TEXT NOT NULL, explicit_memory INTEGER NOT NULL DEFAULT 0
);
CREATE UNIQUE INDEX active_conversation ON runs(conversation)
 WHERE status IN ('queued','running','waiting_approval');
CREATE TABLE events (
 id INTEGER PRIMARY KEY AUTOINCREMENT, owner TEXT NOT NULL,
 conversation TEXT, run TEXT REFERENCES runs(id) ON DELETE CASCADE,
 type TEXT NOT NULL, timestamp REAL NOT NULL, payload TEXT NOT NULL
);
CREATE INDEX run_events ON events(run,id);
CREATE TABLE items (
 id TEXT PRIMARY KEY, owner TEXT NOT NULL, kind TEXT NOT NULL,
 title TEXT NOT NULL, content TEXT NOT NULL, status TEXT NOT NULL,
 source TEXT NOT NULL, created REAL NOT NULL, updated REAL NOT NULL
);
CREATE INDEX owner_items ON items(owner,kind);
CREATE TABLE approvals (
 id TEXT PRIMARY KEY, owner TEXT NOT NULL, run TEXT REFERENCES runs(id) ON DELETE CASCADE,
 tool TEXT NOT NULL, arguments TEXT NOT NULL, digest TEXT NOT NULL,
 status TEXT NOT NULL, expires REAL NOT NULL, created REAL NOT NULL
);
CREATE TABLE jobs (
 id TEXT PRIMARY KEY, owner TEXT NOT NULL, title TEXT NOT NULL, content TEXT NOT NULL,
 status TEXT NOT NULL, next_at REAL NOT NULL, timezone TEXT NOT NULL,
 recurrence TEXT NOT NULL, local_time TEXT, created REAL NOT NULL
);
CREATE TABLE job_executions (
 job TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE, scheduled_at REAL NOT NULL,
 executed_at REAL NOT NULL, status TEXT NOT NULL, PRIMARY KEY(job,scheduled_at)
);
CREATE TABLE audit (
 id INTEGER PRIMARY KEY AUTOINCREMENT, owner TEXT NOT NULL, run TEXT,
 tool TEXT NOT NULL, call_id TEXT, decision TEXT NOT NULL, digest TEXT,
 duration_ms REAL, created REAL NOT NULL
);
INSERT INTO schema_version VALUES (1);
