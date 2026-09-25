"""Credential-free acceptance demonstration using a real ASGI app and SQLite."""
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import time

from fastapi.testclient import TestClient

from .api import create_app
from .config import Settings


def wait_run(client, identifier, wanted=None):
    for _ in range(200):
        row = client.get(f"/v1/runs/{identifier}").json()
        if row["status"] in (wanted or {"completed", "failed", "cancelled"}):
            return row
        time.sleep(0.01)
    raise AssertionError("Run did not reach expected state")


def main():
    print("JARVIS OFFLINE MOCK DEMONSTRATION — no live providers are used.")
    with TemporaryDirectory(prefix="jarvis-demo-") as folder:
        settings = Settings(data_dir=Path(folder), api_token="offline-demo-token-32-characters!")
        headers = {"Authorization": "Bearer " + settings.api_token.get_secret_value()}
        with TestClient(create_app(settings), headers=headers) as client:
            assert client.get("/v1/ready").status_code == 200
            print("PASS 1: service starts without provider credentials")
            conv = client.post("/v1/conversations", json={"title": "Offline demo"}).json()["id"]
            run = client.post(f"/v1/conversations/{conv}/runs", json={"message": "/task Build JARVIS", "permitted_tools": ["task_create"]}).json()
            assert wait_run(client, run["id"])["status"] == "completed"
            assert client.get("/v1/tasks").json()["data"][0]["title"] == "Build JARVIS"
            print("PASS 2: mock-backed conversation executes a local tool")
            memory = client.post("/v1/memories", json={"title": "Tone", "content": "Prefer concise answers"}).json()["data"]
            assert client.get("/v1/memories?query=concise").json()["data"][0]["id"] == memory["id"]
            print("PASS 4: explicitly saved preference is retrievable")
            note = client.post("/v1/notes", json={"title": "Delete me"}).json()["data"]
            pending = client.delete(f"/v1/notes/{note['id']}").json()
            assert pending["status"] == "waiting_approval"
            assert client.get("/v1/notes").json()["data"]
            accepted = client.post(f"/v1/approvals/{pending['approval_id']}/decision",
                                   json={"approve": True, "digest": pending["digest"]})
            assert accepted.json()["ok"]
            assert not client.get("/v1/notes").json()["data"]
            print("PASS 5: destructive action waits for exact approval")
            client.post("/v1/reminders", json={"title": "Demo reminder", "at": datetime.now(timezone.utc).isoformat()})
            for _ in range(100):
                if client.get("/v1/notifications").json():
                    break
                time.sleep(0.02)
            assert client.get("/v1/notifications").json()[0]["event_type"] == "notification.reminder"
            print("PASS 6: reminder emits a durable notification")
            assert client.get("/v1/capabilities").json()["integrations"]["web_search"]["status"] == "unconfigured"
            print("PASS 7: missing integration is explicitly unconfigured")
            run = client.post(f"/v1/conversations/{conv}/runs", json={"message": "/slow"}).json()
            assert client.post(f"/v1/runs/{run['id']}/cancel").json()["status"] == "cancelled"
            print("PASS 8: cancellation reaches a terminal state")
        with TestClient(create_app(settings), headers=headers) as client:
            assert client.get("/v1/tasks").json()["data"][0]["title"] == "Build JARVIS"
            assert client.get("/v1/notifications").json()
            assert client.get("/v1/memories").json()["data"]
            print("PASS 3: task, memory and notification survive service restart")
    print("8/8 offline acceptance checks passed. Live integrations remain unverified.")
