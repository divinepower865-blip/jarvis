from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from jarvis.api import create_app
from jarvis.config import Settings

TOKEN = "test-only-token-with-at-least-32-characters"


@pytest.fixture
def settings(tmp_path):
    root = tmp_path / "files"
    root.mkdir()
    return Settings(data_dir=tmp_path / "data", api_token=TOKEN,
                    read_roots=[root], write_roots=[root], retry_base=0,
                    run_timeout=5, approval_ttl=2)


@pytest.fixture
def app(settings):
    return create_app(settings)


@pytest.fixture
def client(app):
    with TestClient(app, headers={"Authorization": "Bearer " + TOKEN}) as client:
        yield client
