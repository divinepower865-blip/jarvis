import asyncio
import json
from pathlib import Path
import time

import pytest
from fastapi.testclient import TestClient

from jarvis.api import create_app
from jarvis.demo import wait_run
from jarvis.schemas import ToolCall, ModelTurn
from jarvis.security import AppError, Redactor
from conftest import TOKEN


def start(client, message, tools=()):
    conv = client.post("/v1/conversations", json={}).json()["id"]
    response = client.post(f"/v1/conversations/{conv}/runs", json={"message": message, "permitted_tools": list(tools)})
    assert response.status_code == 202, response.text
    return response.json()


def test_auth_limits_config(client):
    assert client.get("/v1/health", headers={"Authorization": "bad"}).status_code == 200
    assert client.get("/v1/tasks", headers={"Authorization": "bad"}).status_code == 401
    assert TOKEN not in client.get("/v1/config").text
    assert client.post("/v1/conversations", content=b"x" * (2 * 1024 * 1024 + 1)).status_code == 413
    schema = client.get("/openapi.json").json()
    assert schema["security"] == [{"BearerAuth": []}]


def test_mock_conversation_stream(client):
    run = start(client, "/task Buy tea", ["task_create"])
    assert wait_run(client, run["id"])["status"] == "completed"
    assert client.get("/v1/tasks").json()["data"][0]["title"] == "Buy tea"
    response = client.get(f"/v1/runs/{run['id']}/events")
    events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
    assert events[0]["event_type"] == "run.started"
    assert events[-1]["event_type"] == "run.completed"
    assert any(e["event_type"] == "tool.completed" for e in events)
    last = events[-2]["event_id"]
    replay = client.get(f"/v1/runs/{run['id']}/events", headers={"Last-Event-ID": str(last)})
    assert "run.completed" in replay.text and "tool.started" not in replay.text
    assert client.get(f"/v1/runs/{run['id']}/events", headers={"Last-Event-ID": "bad"}).status_code == 422


def test_persistence_and_memory(settings):
    for iteration in range(2):
        with TestClient(create_app(settings), headers={"Authorization": "Bearer " + TOKEN}) as client:
            if iteration == 0:
                assert client.post("/v1/tasks", json={"title": "Persist"}).json()["ok"]
                assert client.post("/v1/notes", json={"title": "Note", "content": "Durable"}).json()["ok"]
                run = start(client, "/remember I prefer short replies", ["memory_create"])
                assert wait_run(client, run["id"])["status"] == "completed"
            else:
                assert client.get("/v1/tasks").json()["data"][0]["title"] == "Persist"
                assert client.get("/v1/notes?query=Durable").json()["data"]
                memory = client.get("/v1/memories").json()["data"][0]
                assert memory["content"] == "I prefer short replies"
                changed = client.put(f"/v1/memories/{memory['id']}", json={"id": memory["id"], "status": "outdated"})
                assert changed.json()["data"]["status"] == "outdated"
                assert client.get("/v1/memory-export").json()["memories"]
                pending = client.delete(f"/v1/memories/{memory['id']}").json()
                client.post(f"/v1/approvals/{pending['approval_id']}/decision", json={"approve": True, "digest": pending["digest"]})
                assert client.get("/v1/memories").json()["data"] == []


def test_schema_and_secret_rejection(client):
    assert client.post("/v1/tools/task_create/invoke", json={"arguments": {"unexpected": True}}).status_code == 422
    assert client.post("/v1/tools/no_such_tool/invoke", json={"arguments": {}}).status_code == 422
    secret = "gsk_" + "sensitive" * 6
    response = client.post("/v1/memories", json={"title": "Key", "content": secret})
    assert response.status_code == 422 and secret not in response.text
    invalid = client.post("/v1/memories", json={"title": {"secret": secret}})
    assert secret not in invalid.text
    assert Redactor([TOKEN]).text(TOKEN) == "[REDACTED]"


def test_scope_and_explicit_memory(client):
    run = start(client, "/task Unauthorized")
    wait_run(client, run["id"])
    assert client.get("/v1/tasks").json()["data"] == []
    assert "permission_denied" in client.get(f"/v1/runs/{run['id']}/events").text
    run = start(client, '/tool memory_create {"title":"Inferred", "content":"save me"}', ["memory_create"])
    wait_run(client, run["id"])
    assert client.get("/v1/memories").json()["data"] == []


def test_approval_reject_tamper_reuse(client, app):
    note = client.post("/v1/notes", json={"title": "Keep"}).json()["data"]
    pending = client.delete(f"/v1/notes/{note['id']}").json()
    url = f"/v1/approvals/{pending['approval_id']}/decision"
    assert client.post(url, json={"approve": True, "digest": "0" * 64}).status_code == 409
    assert client.post(url, json={"approve": False, "digest": pending["digest"]}).status_code == 200
    assert client.get("/v1/notes").json()["data"]
    assert client.post(url, json={"approve": True, "digest": pending["digest"]}).status_code == 409
    new = client.delete(f"/v1/notes/{note['id']}").json()
    auth = app.state.authorization
    auth.decide("local", new["approval_id"], True, new["digest"])
    with pytest.raises(AppError, match="reused, changed"):
        auth.consume("local", new["approval_id"], "note_delete", {"id": "other"})
    auth.consume("local", new["approval_id"], "note_delete", {"id": note["id"]})
    with pytest.raises(AppError):
        auth.consume("local", new["approval_id"], "note_delete", {"id": note["id"]})


def test_run_approval_wait_and_cancel(client):
    note = client.post("/v1/notes", json={"title": "Pending"}).json()["data"]
    run = start(client, '/tool note_delete ' + json.dumps({"id": note["id"]}), ["note_delete"])
    wait_run(client, run["id"], {"waiting_approval"})
    pending = client.get("/v1/approvals").json()[0]
    assert client.get("/v1/notes").json()["data"]
    assert client.post(f"/v1/runs/{run['id']}/cancel").json()["status"] == "cancelled"
    assert client.post(f"/v1/approvals/{pending['id']}/decision", json={"approve": True, "digest": pending["digest"]}).status_code == 409
    assert client.get("/v1/notes").json()["data"]


def test_run_approval_resume(client):
    note = client.post("/v1/notes", json={"title": "Delete"}).json()["data"]
    run = start(client, '/tool note_delete ' + json.dumps({"id": note["id"]}), ["note_delete"])
    wait_run(client, run["id"], {"waiting_approval"})
    pending = client.get("/v1/approvals").json()[0]
    client.post(f"/v1/approvals/{pending['id']}/decision", json={"approve": True, "digest": pending["digest"]})
    assert wait_run(client, run["id"])["status"] == "completed"
    assert not client.get("/v1/notes").json()["data"]


def test_cancellation_and_timeout(client, settings):
    run = start(client, "/slow")
    assert client.post(f"/v1/runs/{run['id']}/cancel").json()["status"] == "cancelled"
    settings.run_timeout = 0.05
    run = start(client, "/slow")
    assert wait_run(client, run["id"])["error"] == "run_timeout"


def test_file_boundaries(client, settings, tmp_path):
    root = settings.write_roots[0]
    def invoke(name, args):
        return client.post(f"/v1/tools/{name}/invoke", json={"arguments": args})
    assert invoke("file_read", {"path": str(tmp_path / "outside.txt")}).status_code == 403
    assert invoke("file_read", {"path": str(root / ".." / "outside.txt")}).status_code == 403
    assert invoke("file_read", {"path": str(root / ".env")}).status_code == 403
    path = root / "allowed.txt"
    assert invoke("file_create", {"path": str(path), "content": "hello"}).json()["ok"]
    assert invoke("file_create", {"path": str(path), "content": "overwrite"}).status_code == 409
    assert invoke("file_read", {"path": str(path)}).json()["data"]["content"] == "hello"
    pending = invoke("file_update", {"path": str(path), "content": "updated"}).json()
    assert path.read_text() == "hello"
    assert pending["previous"]["content"] == "hello"
    client.post(f"/v1/approvals/{pending['approval_id']}/decision", json={"approve": True, "digest": pending["digest"]})
    assert path.read_text() == "updated"
    assert invoke("file_search", {"path": str(root), "query": "updated"}).json()["data"]


def test_symlink_restriction(client, settings, tmp_path):
    target = tmp_path / "private.txt"
    target.write_text("private")
    link = settings.read_roots[0] / "link.txt"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("OS account cannot create symlinks")
    response = client.post("/v1/tools/file_read/invoke", json={"arguments": {"path": str(link)}})
    assert response.status_code == 403


def test_missing_integrations_voice_validation(client):
    assert client.get("/v1/capabilities").json()["integrations"]["speech_synthesis"]["status"] == "unconfigured"
    assert client.post("/v1/voice/speech", json={"text": "Hello"}).status_code == 503
    assert client.post("/v1/voice/transcribe", json={"audio_base64": "bad!", "format": "wav"}).status_code == 422


def test_no_secret_in_model_output_or_events(settings):
    secret = "gsk_" + "example" * 8
    class LeakyModel:
        async def stream(self, messages, tools):
            yield {"type": "delta", "text": secret[:7]}
            yield {"type": "delta", "text": secret[7:]}
            yield {"type": "turn", "turn": ModelTurn(content=secret)}
    with TestClient(create_app(settings, model=LeakyModel()), headers={"Authorization": "Bearer " + TOKEN}) as client:
        run = start(client, "hello")
        result = wait_run(client, run["id"])
        assert result["result"] == "[REDACTED]"
        assert secret not in client.get(f"/v1/runs/{run['id']}/events").text


def test_conversation_deletion_and_boundary(client, app):
    run = start(client, "/time")
    wait_run(client, run["id"])
    with pytest.raises(AppError):
        app.state.runtime.get("other-user", run["id"])
    assert client.delete(f"/v1/conversations/{run['conversation']}").status_code == 200
    assert client.get(f"/v1/runs/{run['id']}").status_code == 404


def test_stale_run_recovery(settings):
    from jarvis.store import Store
    store = Store(settings.data_dir / "jarvis.sqlite")
    store.execute("INSERT INTO conversations VALUES('c','local','Recovery',0)")
    store.execute("INSERT INTO runs VALUES('r','local','c','running',NULL,NULL,0,0,'request',0)")
    store.close()
    with TestClient(create_app(settings), headers={"Authorization": "Bearer " + TOKEN}) as client:
        assert client.get("/v1/runs/r").json()["error"] == "service_restarted"
        assert "run.failed" in client.get("/v1/runs/r/events").text
