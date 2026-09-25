# Capability and verification matrix

| Capability | Implementation | Default configuration | Verification |
| --- | --- | --- | --- |
| HTTP auth, health, readiness, OpenAPI | Working | Available | Automated and local HTTP smoke test |
| Conversation history and SSE replay | Working | Available | Automated |
| Groq conversation/tool adapter | Implemented real API | Mock selected; Groq key needed for live | HTTP contract mocks; live unverified |
| Deterministic mock conversation | Working | Available | Offline demo |
| Tasks/notes CRUD and search | Working | Available | Persistence and approval tests |
| Explicit memories, export and statuses | Working | Available | Restart/create/delete tests |
| Lexical memory relevance | Working | Available | No embeddings; no automatic extraction |
| Conversation summaries | Deferred | Unavailable | Recent bounded history used instead |
| File tools | Working | Disabled until roots configured | Traversal, overwrite, junction and hard-link tests |
| Native symlink creation test | Implemented | OS-specific | Skipped here: account cannot create symlinks |
| Exact expiring approvals | Working | Required for deletes/overwrites | Tampering, reuse, rejection, expiry, cancellation tests |
| Reminders, DST, downtime catch-up | Working | Available while service runs | Controllable-clock and concurrent scheduler tests |
| Scheduled local task/note actions | Working | Available while service runs | Atomic effect/notification duplicate suppression test |
| Arbitrary scheduled agent/external jobs | Deferred | Unavailable | Only explicit supported local actions allowed |
| Durable notifications | Working | Polling endpoint | Offline demo and restart tests |
| Fish Audio speech synthesis | Implemented real API | Needs key and voice ID | HTTP contract mocks; live unverified |
| Groq speech recognition | Implemented real API | Needs Groq key | HTTP contract mocks; live unverified |
| Tavily search | Implemented real API | Needs separate Tavily key | HTTP contract mocks; live unverified |
| Turn-based voice chat | Implemented in chat UI | Explicit click and microphone permission | Automated voice flow tests; live device/audio unverified |
| Wake word / simultaneous speech | Deferred | Disabled | No background listening outside an active call |
| Browser/computer/productivity connectors | Protocol extension points | Unavailable | No placeholder success responses |
| Shell execution | Intentionally absent | Unavailable | No shell tool |
| Multiple accounts / remote hosting | Deferred | Local single user | Owner boundary tested; one API token |
| Local chat interface | Implemented at `/` | Workspace token required | Browser-tested chat, history, approvals and cancellation |

`available` in `/v1/capabilities` means configured and ready to attempt calls, not
that live account access has been verified. `live_verified` becomes true only
after a successful request in that service process. Provider failures mark the
integration degraded until a later success. Unconfigured integrations explain
which setting is needed when invoked.

Logs are structured and deliberately omit prompts, keys, arguments and raw
exceptions. Audit and metrics APIs expose local execution metadata. The `data`
directory is not encrypted by this app; use OS user permissions and encrypted
disk/backup storage if needed. Redaction recognizes configured secrets and common
credential patterns, not every possible arbitrary sensitive string.
