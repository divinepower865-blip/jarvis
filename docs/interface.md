# Interface integration guide

Base URL: `http://127.0.0.1:8000`. Send the backend's separate local API token in
`Authorization: Bearer ...` on every data request. Never send provider credentials
to a frontend. For a browser on another origin, set `JARVIS_ALLOWED_ORIGINS` to an
explicit JSON array, for example `["http://localhost:3000"]`, and restart.

The generated `openapi.json` is included with this delivery. Runtime documentation
is at `/docs`; machine-readable schemas are at `/openapi.json`. Tool input/output
schemas and policy metadata are returned by `/v1/capabilities`.
`events.schema.json` defines the discriminated event envelope and typed payloads;
the backend validates event payloads and replayed envelopes against those models.

## Minimal conversation flow

Create a conversation:

```http
POST /v1/conversations
Authorization: Bearer <local token>
Content-Type: application/json

{"title":"My assistant"}
```

Response (`201`):

```json
{"id":"<conversation-id>","owner":"local","title":"My assistant","created":1790000000.0}
```

Start an offline demo run:

```http
POST /v1/conversations/<conversation-id>/runs
Authorization: Bearer <local token>
Content-Type: application/json

{"message":"/task Buy tea","permitted_tools":["task_create"]}
```

For Groq use natural language such as `Create a task called Buy tea` with the same
capability grant. `202` returns a run with `id`, `conversation`, `status`, `result`,
`error`, timestamps and request ID. Poll `/v1/runs/<id>` or fetch its event stream.
Default capabilities are `task_list`, `note_list`, `memory_list`, `reminder_list`,
and `current_time`. Explicitly grant only additional tools needed for the request.
For example, researching a topic needs `web_search`; creating a note needs
`note_create`; file reading needs `file_read` and backend read-root configuration.

## SSE

```http
GET /v1/runs/<run-id>/events
Authorization: Bearer <local token>
Last-Event-ID: 17
Accept: text/event-stream
```

Example event:

```text
id: 18
event: tool.completed
data: {"event_id":18,"event_type":"tool.completed","conversation_id":"c1","run_id":"r1","timestamp":1790000000.0,"payload":{"tool":"task_create","call_id":"call1","result":{"ok":true,"data":{"id":"t1","title":"Buy tea"},"error":null}}}

```

The envelope always has integer `event_id`, string `event_type`, nullable
`conversation_id` and `run_id`, a Unix UTC seconds `timestamp`, and an event-specific
payload. IDs are global database sequence numbers, so gaps are normal.

| Event | Payload fields |
| --- | --- |
| `run.started` | `provider: string`, `mock: boolean` |
| `response.delta` | `text: string` |
| `tool.started` | `tool: string`, `call_id: string` |
| `tool.completed` | `tool`, `call_id`, `result: ToolResult` |
| `tool.failed` | `tool`, `call_id`, `ok: false`, `error: {code,message}` |
| `approval.required` | `approval_id`, `tool`, `arguments`, `digest`, `expires_at`, optional `target`/`previous` preview |
| `provider.retry` | `attempt: integer` |
| `provider.usage` | `usage: object` exactly as reported by the provider |
| `run.completed` | `status`, `result: string`, `error_code: null` |
| `run.failed` | `status`, `result: null`, `error_code: string` |
| `run.cancelled` | `status`, `result: null`, `error_code: string` |

`ToolResult` is `{ok: boolean, data: object | object[] | null, error: {code,message}
| null}`. It is output-validated before publication. A run can complete with a
failed tool result if the assistant explains the failure; use both run status and
tool events when displaying action outcomes.

The stream replays every retained event after `Last-Event-ID` or `?after=ID` (the
higher cursor wins), then follows the run until a terminal state. Duplicate events
after reconnect should be deduplicated by ID. SSE disconnect does not cancel a run;
POST `/v1/runs/<id>/cancel` explicitly. Cancellation never undoes completed actions.
Deleting a conversation deletes its runs/events, so reconnect afterward returns
404. Approval and provider progress arrive as they happen; response text is
redacted after each completed model turn and then delivered as chunks, rather
than raw live tokens. Do not render deltas as trusted HTML.

Browser `EventSource` does not support bearer headers directly. Use streaming
`fetch` as demonstrated in `examples/client.mjs`, or a same-origin authenticated
proxy under your control. Do not put the token in a URL/query string.

## Exact action approvals

Direct destructive requests and model-requested destructive tools return a
pending preview instead of performing the operation. For example:

```http
DELETE /v1/notes/<note-id>
Authorization: Bearer <local token>
```

```json
{
  "status":"waiting_approval",
  "approval_id":"<id>",
  "expires_at":1790000120.0,
  "tool":"note_delete",
  "arguments":{"id":"<note-id>"},
  "digest":"<64-character digest>",
  "target":{"id":"<note-id>","title":"Old note","content":"Content to delete"}
}
```

Display the tool, target and content, then send the digest from that exact preview:

```http
POST /v1/approvals/<approval-id>/decision
Authorization: Bearer <local token>
Content-Type: application/json

{"approve":true,"digest":"<64-character digest>"}
```

Use `approve:false` to reject. A direct action executes once within the decision
request. A run-bound approval resumes its waiting run. Decisions cannot change
arguments; request a new action to change them. Reused, mismatched and expired
approvals return 409. Run timeout includes time spent awaiting approval. GET
`/v1/approvals` supports recovering pending actions after an interface reload.

## Resource routes

| Routes | Contract |
| --- | --- |
| GET `/v1/health`, `/v1/ready` | Liveness; authenticated database/scheduler readiness |
| GET `/v1/capabilities`, `/v1/config` | Integration/tool status and non-secret config |
| POST/GET `/v1/conversations` | Create/list conversations |
| GET/DELETE `/v1/conversations/{id}` | History/delete exact conversation |
| POST `/v1/conversations/{id}/runs` | Start a bounded run |
| GET `/v1/runs/{id}` | Status, final result and error code |
| POST `/v1/runs/{id}/cancel` | Cancel and return terminal/current status |
| GET `/v1/runs/{id}/events` | Durable SSE stream |
| GET `/v1/approvals` | Recent approvals, statuses and normalized arguments |
| POST `/v1/approvals/{id}/decision` | Approve/reject exact digest |
| GET/POST `/v1/tasks`, `/v1/notes`, `/v1/memories` | List/search (`?query=`)/create |
| PUT/DELETE `/v1/tasks/{id}`, `/v1/notes/{id}`, `/v1/memories/{id}` | Update/delete with exact approval for delete |
| GET `/v1/memory-export` | Complete JSON memory export |
| GET/POST `/v1/reminders` | List/create notifications |
| GET/POST `/v1/jobs` | List/create supported scheduled local tools |
| POST `/v1/reminders/{id}/{operation}`, `/v1/jobs/{id}/{operation}` | `pause`, `resume`, `cancel` |
| GET `/v1/notifications?after=ID` | Up to 500 durable notification events after cursor |
| GET `/v1/job-executions` | Recent durable scheduler outcomes |
| POST `/v1/tools/{name}/invoke` | Explicit direct action `{arguments:{...}}` |
| POST `/v1/voice/speech`, `/v1/voice/transcribe` | Optional audio adapters |
| GET `/v1/audit`, `/v1/metrics` | Audit metadata and execution aggregates |

Resource create input: `{title: string, content?: string}`. PUT input includes the
same `id` as the URL and any of `title`, `content`, `status`. Statuses are `active`,
`completed`, `outdated`, `superseded`; use `completed` for tasks and the latter two
for stale memories. GET/POST/PUT item endpoints use `ToolResult`; reminders/jobs
listing and conversation listing return raw arrays. Lists are bounded to 200
recent records; memory export is complete. Pagination beyond these bounded lists
is a future extension. Individual conversation history currently returns all
stored messages; avoid very large conversations.

Memory creation through the dedicated endpoint is an explicit user save. Through
chat, use `/remember <exact text>` or `remember this: <exact text>` and grant
`memory_create`. In mock mode only `/remember` is interpreted. The application
does not infer consent to save from retrieved documents or prior model responses.

## Scheduling

```json
{
  "title":"Review project",
  "content":"Check today's tasks",
  "at":"2026-10-01T09:00:00+02:00",
  "timezone":"Europe/Brussels",
  "recurrence":"daily"
}
```

POST that object to `/v1/reminders`. `at` requires an explicit UTC offset.
Recurring schedules preserve the wall-clock time in the supplied IANA timezone.
For local scheduled work, POST `/v1/jobs` with the same schedule plus:

```json
{"tool":"task_create","arguments":{"title":"Daily review"}}
```

Supported scheduled tools: `task_create`, `task_complete`, `note_create`. Schedule
arguments are immutable; cancel and recreate to change them. No external service,
model, shell, file or destructive operation is accepted as a scheduled tool.
Notifications use the same envelope with null conversation/run IDs. Payloads
include `job_id`, `title`, `content`, `scheduled_at`, and `late`; local job completion
adds `result`, and failure adds `error_code`. Event types are
`notification.reminder`, `notification.job_completed`, `notification.job_failed`.

## Voice

POST `/v1/voice/speech`:

```json
{"text":"Good evening. Your tasks are ready.","format":"mp3"}
```

Returns audio bytes with the corresponding audio MIME type. Allowed outputs are
mp3, wav, opus. Text is limited to 4,000 characters. Requires Fish key and voice ID.
The interface owns playback; interrupt playback locally and abort the fetch to
cancel an in-flight request. Already-generated provider work may still be billed.
This endpoint buffers audio before responding; streaming audio is deferred.

POST `/v1/voice/transcribe`:

```json
{"audio_base64":"<base64 encoded audio>","format":"wav"}
```

Returns `{"text":"recognized words"}`. Supported inputs: wav, mp3, flac, ogg, webm.
The backend validates base64, leading format signatures and a 1 MiB decoded limit;
the provider performs full decoding. Requires Groq key. Send the returned transcript
to the normal run endpoint with the desired capabilities. No audio is stored by
JARVIS. The selected providers receive audio/text when these endpoints are used.

## Stable errors

```json
{"error":{"code":"permission_denied","message":"The current request did not grant this tool capability.","request_id":"<id>"}}
```

Common codes: `unauthorized` (401), `permission_denied`/`path_denied` (403),
`not_found` (404), `conversation_busy`/`approval_mismatch`/`approval_unavailable`
(409), `request_too_large` (413), `validation_error`/`invalid_tool_arguments` (422),
`busy` (429), `provider_rejected`/`provider_timeout`/`provider_invalid` (502),
`integration_unconfigured` (503). Run-level terminal codes additionally include
`run_timeout`, `tool_limit`, `context_limit`, `cancelled`, `service_restarted`.
Raw provider bodies and stack traces are never included in API errors.
