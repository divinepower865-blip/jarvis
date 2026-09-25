# Local operation

## Settings and files

Start from `.env.example` or `python -m jarvis configure`. Provider credentials
belong only in the local `.env` or OS environment. The local API token is separate
from Groq/Fish keys. If no token is configured, startup creates `data/api-token`.
The service never returns this token through an endpoint.

On Windows the files inherit your user-directory ACL; `chmod` is not a substitute
for a Windows ACL. Keep this folder private to your account. On POSIX newly written
credential files are mode 0600. Do not expose the service to your LAN or the
Internet without adding an appropriate TLS/authentication deployment layer.

Example explicit file directories in `.env`:

```dotenv
JARVIS_READ_ROOTS=["C:/Users/your-user/Documents/JarvisFiles"]
JARVIS_WRITE_ROOTS=["C:/Users/your-user/Documents/JarvisFiles"]
JARVIS_ALLOWED_ORIGINS=["http://localhost:3000"]
```

Create the directories first. Paths sent to tools must be absolute. Reads/writes
are limited to UTF-8 text and 256 KiB. Directory listing is non-recursive, capped
at 200 entries; search scans those immediate files with at most 100 matches.
Credential/internal paths, traversal, symlinks, junctions and hard links are
blocked. File updates require a content preview and approval. File creation is
exclusive and fails instead of overwriting an existing file.

Per-run tool scope is supplied by the authenticated interface. Optional
`JARVIS_STANDING_PERMISSIONS` is a JSON list of exact tool names. No wildcards are
accepted. Granting a destructive tool there deliberately bypasses its approval
for all targets reachable by that tool; prefer per-run grants and exact previews.

## Foreground and background service

```powershell
.\.venv\Scripts\python.exe -m jarvis serve
```

Ctrl+C requests graceful shutdown, cancelling outstanding runs and closing
provider connections and the database. Run **one** process per data directory.
Do not use Uvicorn multi-worker mode or hot reload with this deployment. A process
lock prevents accidental concurrent runtimes. SQLite WAL and transactions protect
storage; the scheduler's job/occurrence primary key suppresses duplicate local
notifications even when tested from separate scheduler connections.

For Windows background operation, use Task Scheduler's **Create Task**:

1. Trigger: at logon for your Windows account.
2. Program: the absolute path to `.venv\Scripts\pythonw.exe` in this folder.
3. Arguments: `-m jarvis serve`.
4. Start in: the absolute path to this JARVIS folder.
5. Configure restart on failure and prevent overlapping task instances.

`pythonw.exe` runs without a console window. Use foreground mode when diagnosing
startup errors. This delivery does not register a background task automatically.
The service must be running for reminders and jobs to execute; a chat message
alone cannot keep it alive.

For Linux, an example systemd user unit (replace both absolute paths):

```ini
[Unit]
Description=JARVIS personal assistant

[Service]
WorkingDirectory=/absolute/path/jarvis
ExecStart=/absolute/path/jarvis/.venv/bin/python -m jarvis serve
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
```

## Scheduler policy

Schedules are stored with a UTC instant plus IANA timezone and local wall-clock
time. Daily/weekly schedules preserve local time across daylight-saving changes.
For a nonexistent spring-forward time, execution advances to the first valid
minute. For an ambiguous autumn time, the earlier occurrence is used. One-time
reminders use the exact timestamp supplied.

After downtime, a due reminder/job executes once for the earliest pending
occurrence; additional missed recurrences are coalesced, then the next future
occurrence is scheduled. `late` marks delivery over one minute late. Paused jobs
do not execute; resuming applies the same catch-up rule. Cancelled/completed jobs
cannot be resumed. Create a new job instead.

Only selected reversible database actions can be scheduled. Their operation,
execution record, schedule advance and notification are a single transaction.
They revalidate schemas and authorization each time. No external exactly-once
delivery guarantee is made; external schedules are not implemented. Success and
failure produce durable notifications, while unchanged idle state stays quiet.
Have your interface persist its last `/v1/notifications` cursor and poll for new
events. A notification remains available after service restart.

## Backup and restore

Create a consistent snapshot even while the service is running:

```powershell
.\.venv\Scripts\python.exe -m jarvis backup .\backups\jarvis-backup.sqlite
```

The destination must not exist. Backups contain private conversations and memory;
protect them like the live database. Back up `.env` separately through your own
secure credential storage, not source control or a public archive.

To restore, stop JARVIS first. Preserve the current entire `data` folder as a
separate recovery copy, create a fresh `data` folder, and copy the chosen backup
to `data/jarvis.sqlite`. Do not copy old WAL/SHM sidecars into the restored folder.
Keep your `.env` settings; if the token was only in the old `data/api-token`, copy
that file securely or accept a newly generated token and update your interface.
Restart the service. Pending runs from the backup become `service_restarted`
failures and are not replayed. Due schedules follow the catch-up policy above.

## Diagnostics and limits

`/v1/ready` checks database access and scheduler liveness. Missing optional
integrations do not make unrelated features unready. `/v1/capabilities` reports
configuration and provider failure state without exposing keys. `/v1/audit`
records tool decisions and durations; `/v1/metrics` aggregates outcomes.
JSON logs include request, conversation, run and tool-call IDs, plus run latency.
Raw exceptions, prompts, API keys and tool arguments are omitted. Provider usage
and retry attempts appear as durable run events. No cost estimates are invented.

Adjust `JARVIS_MAX_TOOL_CALLS`, `JARVIS_RUN_TIMEOUT`, `JARVIS_CONTEXT_CHARS`,
`JARVIS_OUTPUT_TOKENS`, and `JARVIS_APPROVAL_TTL` in the environment within their
validated ranges. Request bodies are limited to 2 MiB and voice uploads to 1 MiB
decoded. The development mode is the foreground service plus mock adapter and
readable JSON logs; it uses the same redaction and authorization boundaries.

This release has no data-retention vacuum/compaction job, arbitrary service
connectors, conversation summarization, remote multi-user account administration,
or encrypted-at-rest application database. These are documented extensions rather
than hidden placeholders.
