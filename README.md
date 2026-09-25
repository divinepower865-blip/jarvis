# JARVIS personal assistant backend

A runnable Python/FastAPI backend for your own interface. Groq provides language
and optional transcription; Fish Audio provides optional speech. SQLite holds
conversations, tasks, notes, explicitly saved memories, schedules and audit data.
A local chat interface is now included, following your request to add one.

## Chat interface

JARVIS defaults to `llama-3.1-8b-instant` on Groq and short spoken replies
(usually one or two sentences). The completion limit is 512 tokens per model turn.
This reduces generated text and subsequent speech work. Ask for more detail when
needed; very long answers or tool arguments may require raising
`JARVIS_OUTPUT_TOKENS` in `.env`. To restore the previous model and limit, set
`GROQ_MODEL=openai/gpt-oss-20b` and `JARVIS_OUTPUT_TOKENS=2048`, then restart.
The smaller model may be less capable on complex reasoning tasks; live latency
depends on the provider and has not been benchmarked for this change.

### Voice latency settings

Provider HTTP clients persist for the service lifetime. Voice has its own pool
(8 connections), separate from chat/search (16); idle connections stay reusable
for 60 seconds. Both pools close on shutdown. Connection setup is bounded at
5 seconds and pool waits at 1 second. Saturated pools fail without retrying.

Total deadlines include retries, backoff, uploads and response downloads:
`JARVIS_STT_TIMEOUT=20`, `JARVIS_TTS_TIMEOUT=30`,
`JARVIS_SEARCH_TIMEOUT=20`, and `JARVIS_PROVIDER_TIMEOUT=40` (seconds).
These settings, plus `JARVIS_CONNECT_TIMEOUT`, `JARVIS_POOL_TIMEOUT` and
`JARVIS_KEEPALIVE_EXPIRY`, can be overridden in `.env`; restart the service
after changing them. Increase the TTS deadline if long replies time out on your
provider. Voice calls are not automatically retried.

Reply events wake connected clients immediately instead of polling every 250 ms.
Conversation-list refresh runs independently of reply retrieval. Provider duration
logs contain only the service name and elapsed milliseconds, never audio, text or
credentials. The model's complete turn still undergoes redaction before speech;
this is not token-to-audio streaming and live provider latency remains variable.

The interface now follows the supplied dark cyan JARVIS Command Center reference:
left navigation, central wireframe globe, intelligence feed, skill cards, reminders,
session metrics, memory counts, provider status, and a bottom voice bar. The globe
reacts to assistant audio. Open Conversation reveals the prompt input, replies,
voice controls and colour setting. Workspace panels use authenticated local data;
unconfigured integrations are labelled rather than populated with sample activity.
Assistant skill cards prepare prompts for the single assistant; they do not launch
independent agents. Refresh the browser to load the updated interface.

Choose **Hands-free** or **Hold to talk** before starting Voice chat. In hold mode,
hold the on-screen button or Ctrl+Space while this page has focus, then release
to send. The microphone is disabled between turns. Replies still speak automatically
through Fish Audio, with Groq transcription and reasoning. Gemini Live, wake-word
detection, desktop control and phoneme-based lip-sync are not included.

After starting or restarting the service, open http://127.0.0.1:8000/.
Enter your local `JARVIS_API_TOKEN` once to connect; do not enter provider keys.
The token stays in this browser tab session. The interface supports saved
conversations, progress, cancellation, action approvals, optional voice recording,
transcription and automatic Fish Audio speech for new replies. You no longer need to copy IDs.
Replies speak by default; there is no per-message Read aloud button. If browser
audio is blocked, click **Enable sound** once. **Stop** also stops speech.
Speech starts with a short segment and prepares the next segment during playback
to reduce startup delay. Model and voice-provider processing still take time.

Microphone access starts only when you click Record and approve your browser's
permission. The transcript appears in the message box for review before sending.
The tasks/notes/reminders checkbox grants those local tools for the message;
destructive actions still require explicit approval. Speech depends on your
configured credentials and selected Fish Audio voice.

## Start on this computer

### Voice conversations

Click **Voice chat** at the top of the chat and allow microphone access. Speak,
then pause for about a second. Your speech is transcribed and sent automatically;
JARVIS replies aloud and then listens for your next message. The microphone is
paused while JARVIS thinks and speaks to prevent echo. Use **Interrupt** to cut
off a reply and speak again, **Mute** to pause listening, or **End call** to release
the microphone and stop pending work. Approvals still need an on-screen decision.

This is turn-based voice chat, not simultaneous speech or a wake-word service.
Each recording is capped at 25 seconds. Silence is discarded. Transcription goes
to Groq and speech synthesis to Fish Audio through your backend. Microphone access
is never started on page load. If your in-app browser blocks audio or microphone
access, open the same localhost address in Chrome or Edge. No server restart is
needed for this UI update; refresh the chat page.

Voice flow tests (Node.js): `node --test tests/voice-chat.test.mjs tests/speech-output.test.mjs`.

Open PowerShell in this folder:

```powershell
# The development environment has already been installed in .venv.
.\.venv\Scripts\python.exe -m jarvis demo
.\.venv\Scripts\python.exe -m jarvis configure
.\.venv\Scripts\python.exe -m jarvis serve
```

`configure` asks privately for provider keys, generates a separate local API token,
and writes a git-ignored `.env`. Enter skips optional values. It never overwrites
an existing `.env`; edit that file locally to change an existing configuration.
Provider keys from the conversation have **not** been embedded in these files.
Fish Audio's voice model ID remains configurable at your request.

The service binds to `127.0.0.1:8000`. [Swagger API documentation](http://127.0.0.1:8000/docs)
is available while the service runs. Click **Authorize** and enter the local
`JARVIS_API_TOKEN`, not a provider key.

To start completely offline, skip `configure` and run `serve`. The default is the
deterministic mock adapter. A random local API token is created in `data/api-token`.
Read that file locally to connect your interface; it is never logged or returned
by the API. Health and documentation are public, while application data requires
the token. All requests use `Authorization: Bearer <local API token>`.

## Install on another computer

Requires Python 3.11+:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.lock
.\.venv\Scripts\python.exe -m pip install -e . --no-deps
.\.venv\Scripts\python.exe -m jarvis configure
.\.venv\Scripts\python.exe -m jarvis serve
```

On macOS/Linux use `python3` and `.venv/bin/python`. The lock file records the
versions tested here, including test dependencies. `pip install -e '.[dev]'` uses
the supported dependency ranges when intentionally updating dependencies.

## Provider configuration

| Setting | Purpose |
| --- | --- |
| `JARVIS_PROVIDER=groq` | Enable real language-model calls; `mock` is the offline demo |
| `GROQ_API_KEY` | Backend-only Groq credential |
| `GROQ_MODEL` | Configurable chat model; default `openai/gpt-oss-20b` |
| `GROQ_STT_MODEL` | Transcription model; default `whisper-large-v3-turbo` |
| `FISH_API_KEY` | Backend-only Fish Audio credential |
| `FISH_VOICE_ID` | Voice/model reference ID selected in your Fish Audio account |
| `FISH_MODEL` | Configurable synthesis model; default `s2.1-pro-free` |
| `TAVILY_API_KEY` | Optional web search credential |
| `JARVIS_API_TOKEN` | Separate token authenticating your own interface |

Missing provider configuration disables that integration, not the whole service.
Groq mode without a key returns a clear failed run. The application does not
silently substitute mock replies for a failed live integration. Model availability
and account access are checked by the provider when you make a request.

## What works

- Authenticated, versioned HTTP API and durable SSE run events.
- Groq streaming adapter, bounded tool loop, cancellation, timeouts and usage events.
- Tasks and notes with search/update/delete; exact approval for deletion.
- Explicit memories with editing, export, source/timestamps and outdated status.
- Permitted UTF-8 files with traversal, symlink, junction and hard-link restrictions.
- Once/daily/weekly reminders and selected scheduled local actions.
- Durable notifications, scheduler duplicate suppression and restart recovery.
- Fish speech synthesis and Groq transcription adapters, independently optional.
- Tavily search adapter, independently optional.
- Automated tests and a credential-free acceptance demo.

See [capability matrix](docs/capabilities.md) for implementation and verification
status, [interface guide](docs/interface.md) for requests and SSE examples,
[architecture](docs/architecture.md), and [operations](docs/operations.md).

## Verification

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m jarvis demo
.\.venv\Scripts\python.exe -m jarvis openapi
```

The demo uses temporary local data and proves all eight requested offline
acceptance scenarios. Provider protocol tests use HTTP mocks. They do not verify
your live accounts, billing, model access, selected voice, or live output quality.

## Important behavior

The interface explicitly grants additional per-run tool capabilities through
`permitted_tools`; model output and retrieved content cannot grant them. The
default set contains local list/search tools and the clock. File access also
requires configured absolute roots. Destructive tool actions require approval of
an exact argument digest unless an exact standing tool permission is configured.

Run progress streams immediately. Response text is buffered until each model turn
finishes, redacted, then emitted as `response.delta` chunks. This prevents secrets
split across provider chunks from leaking. This release does not offer live
token-by-token text delivery or continuous voice capture.

Jobs run only while this service runs. Keep one API process per data directory;
the process lock enforces this. Supported scheduled actions are `task_create`,
`task_complete`, and `note_create`. External connectors, browser/desktop control,
arbitrary shell execution and automatic memory extraction are extension points,
not implemented capabilities.
