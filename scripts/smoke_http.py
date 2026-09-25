"""Real localhost startup test. Uses only mock mode and temporary credentials."""
import os
import asyncio
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import tempfile
import time

import httpx


def main():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    with tempfile.TemporaryDirectory(prefix="jarvis-http-") as folder:
        token = secrets.token_urlsafe(32)
        env = dict(os.environ, JARVIS_API_TOKEN=token, JARVIS_PROVIDER="mock", JARVIS_DATA_DIR=folder)
        process = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "--server", str(port)],
                                   env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                   creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        try:
            with httpx.Client(base_url=f"http://127.0.0.1:{port}", headers={"Authorization": "Bearer " + token}, trust_env=False) as client:
                for _ in range(100):
                    try:
                        if client.get("/v1/ready").status_code == 200:
                            break
                    except httpx.ConnectError:
                        pass
                    time.sleep(0.05)
                else:
                    raise AssertionError("Service failed to start")
                assert client.get("/v1/tasks", headers={"Authorization": "invalid"}).status_code == 401
                conversation = client.post("/v1/conversations", json={"title": "HTTP smoke"}).json()["id"]
                run = client.post(f"/v1/conversations/{conversation}/runs", json={"message": "/task Socket-tested task", "permitted_tools": ["task_create"]}).json()["id"]
                events = client.get(f"/v1/runs/{run}/events")
                assert "run.completed" in events.text
                assert client.get("/v1/tasks").json()["data"][0]["title"] == "Socket-tested task"
                assert client.get("/openapi.json").json()["info"]["title"] == "JARVIS backend"
                print("PASS: real HTTP startup, authentication, task execution, SSE and OpenAPI")
        finally:
            (Path(folder) / "stop").touch()
            process.wait(timeout=10)


async def serve_test(port):
    import uvicorn
    from jarvis.api import create_app
    server = uvicorn.Server(uvicorn.Config(create_app(), host="127.0.0.1", port=port,
                                          access_log=False, log_level="warning"))
    async def watch_stop():
        while not (Path(os.environ["JARVIS_DATA_DIR"]) / "stop").exists():
            await asyncio.sleep(0.05)
        server.should_exit = True
    watcher = asyncio.create_task(watch_stop())
    try:
        await server.serve()
    finally:
        watcher.cancel()
        await asyncio.gather(watcher, return_exceptions=True)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--server":
        asyncio.run(serve_test(int(sys.argv[2])))
    else:
        main()
