# Verification report

Verified locally on Windows with Python 3.12.14, using the versions recorded in
`requirements.lock`.

| Check | Result |
| --- | --- |
| Automated suite (`python -m pytest -q`) | 36 passed, 1 skipped |
| Offline acceptance demo (`python -m jarvis demo`) | 8 of 8 passed |
| Real localhost HTTP smoke (`python scripts/smoke_http.py`) | Passed |
| Dependency consistency (`python -m pip check`) | No broken requirements |
| OpenAPI and event schema generation | Generated successfully |

The skipped test requires native symlink creation, which this Windows account
cannot perform. Separate actual Windows junction and hard-link tests passed,
along with traversal, credential-path, file-create and approved-overwrite tests.

The installed Starlette version emits a deprecation warning about its httpx-based
test client. This does not affect the passing ASGI tests or the independently
verified real HTTP service. The dependency lock preserves the tested environment.

Coverage includes offline tool execution, task/note persistence, explicit memory
save/delete/export, approval enforcement/rejection/reuse/tampering/expiry, bounded
provider retries and malformed responses, cancellation and terminal states,
restart recovery, scheduler concurrency/duplicate suppression, DST handling,
authentication, request size limits, secret redaction across model chunks, owner
isolation, process locking, and denial of capabilities requested by untrusted
retrieved instructions.

The real HTTP check starts a temporary mock-backed service, verifies authentication,
creates a task through a run, consumes SSE over an actual socket, fetches OpenAPI,
and shuts the service down. It leaves no persistent service running.

Groq, Fish Audio and Tavily requests were verified against mocked HTTP transports
and official API contracts. No live provider account or voice-quality verification
is claimed. Credentials were not embedded in source/configuration deliverables.
Configure them privately with `python -m jarvis configure`. The Fish voice ID is
intentionally left configurable, following your answer.

## Live Groq follow-up (2026-09-24)

The configured key authenticated successfully against Groq. The old model was
absent from the account model list. Switched to `openai/gpt-oss-20b` and verified
a real streaming completion: "JARVIS is ready." HTTPS now uses the operating
system certificate trust store, with certificate verification still enabled.
Fish Audio and Tavily remain unverified. Restart the running service to load
the updated model and transport configuration.

## Chat interface follow-up

Added a same-origin local chat interface without exposing provider credentials.
Browser checks verified sign-in, a mock-backed task request, history on reload,
and approval display/rejection. Voice controls are implemented; actual microphone
and Fish playback were not exercised. Additional HTTP tests check public static
assets, private API data, security headers, and blocked configuration paths.

## Voice-chat follow-up

Eight Node.js tests pass for silence suppression, utterance/pause detection,
short-noise rejection, late transcription/reply cancellation, provider failure
cleanup, mute/interrupt behavior, and the playback-to-listening transition.
JavaScript syntax checks pass. The updated Voice chat button was verified on the
running chat page. Physical microphone capture and audible playback require user
device testing and have not been represented as verified here.
