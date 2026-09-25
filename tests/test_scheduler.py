from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor

import pytest

from jarvis.scheduler import Scheduler, next_wall_time
from jarvis.schemas import ReminderCreate
from jarvis.security import AppError
from jarvis.store import Store


def test_persistent_reminder_duplicate_suppression(tmp_path):
    clock = [1000.0]
    path = tmp_path / "scheduler.sqlite"
    first = Store(path, clock=lambda: clock[0])
    scheduler = Scheduler(first)
    job = scheduler.create("local", ReminderCreate(title="One", at=datetime.fromtimestamp(1001, timezone.utc)))
    scheduler.tick()
    assert first.events("local", notifications=True) == []
    second = Store(path, clock=lambda: clock[0])
    clock[0] = 1002
    with ThreadPoolExecutor(2) as pool:
        list(pool.map(lambda s: s.tick(), [scheduler, Scheduler(second)]))
    assert len(first.events("local", notifications=True)) == 1
    assert len(first.rows("SELECT * FROM job_executions")) == 1
    scheduler.tick()
    first.close()
    second.close()
    restarted = Store(path, clock=lambda: clock[0])
    assert Scheduler(restarted).get("local", job["id"])["status"] == "completed"
    assert len(restarted.events("local", notifications=True)) == 1
    restarted.close()


def test_dst_gap_fold_and_missed_execution():
    # Brussels jumps from 02:00 to 03:00 on March 29, 2026.
    previous = datetime.fromisoformat("2026-03-28T02:30:00+01:00").timestamp()
    gap = next_wall_time(previous, "Europe/Brussels", "daily", "02:30:00", previous)
    assert datetime.fromtimestamp(gap, timezone.utc).isoformat() == "2026-03-29T01:00:00+00:00"
    previous = datetime.fromisoformat("2026-10-24T02:30:00+02:00").timestamp()
    fold = next_wall_time(previous, "Europe/Brussels", "daily", "02:30:00", previous)
    assert datetime.fromtimestamp(fold, timezone.utc).isoformat() == "2026-10-25T00:30:00+00:00"
    now = datetime.fromisoformat("2026-11-20T12:00:00+01:00").timestamp()
    following = next_wall_time(previous, "Europe/Brussels", "weekly", "02:30:00", now)
    assert following > now


def test_reminder_pause_resume_and_catchup(tmp_path):
    clock = [1000.0]
    store = Store(tmp_path / "jobs.sqlite", clock=lambda: clock[0])
    scheduler = Scheduler(store)
    job = scheduler.create("local", ReminderCreate(title="Daily", at=datetime.fromtimestamp(1000, timezone.utc), recurrence="daily", timezone="UTC"))
    scheduler.change("local", job["id"], "paused")
    clock[0] += 10 * 86400
    scheduler.tick()
    assert not store.events("local", notifications=True)
    scheduler.change("local", job["id"], "active")
    scheduler.tick()
    assert len(store.events("local", notifications=True)) == 1
    assert scheduler.get("local", job["id"])["next_at"] > clock[0]
    scheduler.change("local", job["id"], "cancelled")
    with pytest.raises(AppError):
        scheduler.change("local", job["id"], "active")
    store.close()


def test_approval_expiry_with_clock(settings):
    from jarvis.api import create_app
    from fastapi.testclient import TestClient
    from conftest import TOKEN
    now = [1000.0]
    with TestClient(create_app(settings, clock=lambda: now[0]), headers={"Authorization": "Bearer " + TOKEN}) as client:
        note = client.post("/v1/notes", json={"title": "Expiry"}).json()["data"]
        pending = client.delete(f"/v1/notes/{note['id']}").json()
        now[0] += 10
        response = client.post(f"/v1/approvals/{pending['approval_id']}/decision", json={"approve": True, "digest": pending["digest"]})
        assert response.status_code == 409
        assert client.get("/v1/notes").json()["data"]


def test_scheduled_tool_exactly_one_local_effect(settings):
    from jarvis.api import create_app
    from fastapi.testclient import TestClient
    from conftest import TOKEN
    now = [1000.0]
    app = create_app(settings, clock=lambda: now[0])
    with TestClient(app, headers={"Authorization": "Bearer " + TOKEN}) as client:
        response = client.post("/v1/jobs", json={"title": "Scheduled task", "at": "1970-01-01T00:16:41+00:00",
                                               "tool": "task_create", "arguments": {"title": "Scheduled once"}})
        assert response.status_code == 200, response.text
        now[0] = 1002.0
        app.state.scheduler.tick()
        app.state.scheduler.tick()
        assert len(client.get("/v1/tasks").json()["data"]) == 1
        assert client.get("/v1/notifications").json()[0]["event_type"] == "notification.job_completed"
