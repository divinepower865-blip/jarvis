import argparse
from getpass import getpass
import json
import logging
from pathlib import Path
import secrets
import sqlite3


def configure():
    destination = Path(".env")
    if destination.exists():
        raise SystemExit(".env already exists; edit it locally to preserve your settings.")
    print("Optional provider keys are entered privately and never printed.")
    groq = getpass("Groq API key (Enter for offline mock mode): ").strip()
    fish = getpass("Fish Audio API key (optional): ").strip()
    voice = input("Fish voice model ID (optional): ").strip()
    template = (Path(__file__).parent.parent / ".env.example")
    if template.exists():
        content = template.read_text()
    else:
        content = "JARVIS_API_TOKEN=\nJARVIS_PROVIDER=mock\nGROQ_API_KEY=\nFISH_API_KEY=\nFISH_VOICE_ID=\n"
    values = {"JARVIS_API_TOKEN": secrets.token_urlsafe(32), "JARVIS_PROVIDER": "groq" if groq else "mock",
              "GROQ_API_KEY": groq, "FISH_API_KEY": fish, "FISH_VOICE_ID": voice}
    lines = []
    for line in content.splitlines():
        key = line.split("=", 1)[0]
        lines.append(key + "=" + json.dumps(values[key]) if key in values else line)
    with destination.open("x", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")
    destination.chmod(0o600)
    print("Saved private local .env. Start with: python -m jarvis serve")


def main():
    parser = argparse.ArgumentParser(description="JARVIS personal assistant backend")
    sub = parser.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve")
    serve.add_argument("--port", type=int, default=8000)
    sub.add_parser("configure")
    sub.add_parser("demo")
    sub.add_parser("openapi")
    backup = sub.add_parser("backup")
    backup.add_argument("destination", type=Path)
    args = parser.parse_args()
    if args.command == "configure":
        return configure()
    if args.command == "demo":
        from .demo import main as demo
        return demo()
    if args.command == "openapi":
        from .api import create_app
        from .events import EVENT_ADAPTER
        Path("openapi.json").write_text(json.dumps(create_app().openapi(), indent=2), encoding="utf-8")
        Path("events.schema.json").write_text(json.dumps(EVENT_ADAPTER.json_schema(), indent=2), encoding="utf-8")
        print("Wrote openapi.json and events.schema.json")
        return
    if args.command == "backup":
        from .config import Settings
        source = Settings.from_env().data_dir / "jarvis.sqlite"
        if not source.is_file() or args.destination.exists():
            raise SystemExit("Source must exist and destination must be a new file.")
        args.destination.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(source) as src, sqlite3.connect(args.destination) as dest:
            src.backup(dest)
        args.destination.chmod(0o600)
        print("Backup created.")
        return
    from .api import create_app
    import uvicorn
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    # Avoid logging request paths, query strings, headers or provider exceptions.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    uvicorn.run(create_app(), host="127.0.0.1", port=args.port, access_log=False, log_level="warning")


if __name__ == "__main__":
    main()
