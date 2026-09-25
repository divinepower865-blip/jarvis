"""Restricted UTF-8 file tools. Roots must not be writable by untrusted users."""
from pathlib import Path
import os

from .security import AppError


class Files:
    def __init__(self, settings):
        self.settings = settings

    def resolve(self, raw, write=False):
        roots = self.settings.write_roots if write else self.settings.read_roots + self.settings.write_roots
        path = Path(raw)
        if not path.is_absolute():
            raise AppError("path_denied", "An absolute path inside an allowed directory is required.", 403)
        if ".." in path.parts or (os.name == "nt" and ":" in str(path)[2:]):
            raise AppError("path_denied", "Traversal and alternate data streams are prohibited.", 403)
        # Reject symlinks and Windows junctions at every existing component, even
        # those pointing inside the root. Resolve and compare path components too.
        for part in (path, *path.parents):
            if part.is_symlink() or (hasattr(part, "is_junction") and part.is_junction()):
                raise AppError("path_denied", "Symlinks and junctions are prohibited.", 403)
        resolved = path.resolve()
        if not any(resolved.is_relative_to(root.resolve()) for root in roots):
            raise AppError("path_denied", "Path is outside the configured directories.", 403)
        blocked = {".env", ".git", ".ssh", ".aws", ".codex", ".venv"}
        if any(p.lower() in blocked or p.lower().startswith(".env") for p in resolved.parts) or resolved.suffix.lower() in {".pem", ".key", ".sqlite", ".db"}:
            raise AppError("path_denied", "Credential and internal storage paths are prohibited.", 403)
        if resolved.is_relative_to(self.settings.data_dir.resolve()):
            raise AppError("path_denied", "Internal application storage is prohibited.", 403)
        return resolved

    def read(self, raw):
        path = self.resolve(raw)
        if not path.is_file():
            raise AppError("not_found", "File not found.", 404)
        if path.stat().st_nlink > 1:
            raise AppError("path_denied", "Hard-linked files cannot be read.", 403)
        if path.stat().st_size > self.settings.max_file_bytes:
            raise AppError("file_too_large", "File exceeds the configured size limit.", 413)
        try:
            content = path.read_bytes()
            if len(content) > self.settings.max_file_bytes:
                raise AppError("file_too_large", "File exceeds the configured size limit.", 413)
            return {"path": str(path), "content": content.decode("utf-8")}
        except UnicodeError:
            raise AppError("file_format", "Only UTF-8 text files are supported.", 422) from None

    def listing(self, raw):
        path = self.resolve(raw)
        if not path.is_dir():
            raise AppError("not_found", "Directory not found.", 404)
        result = []
        for child in path.iterdir():
            if len(result) >= 200:
                break
            try:
                checked = self.resolve(str(child))
                result.append({"path": str(checked), "directory": checked.is_dir()})
            except AppError:
                continue
        return result

    def search(self, raw, query):
        # Deliberately bounded to immediate directory entries, documented in API.
        matches = []
        for entry in self.listing(raw):
            if entry["directory"]:
                continue
            try:
                text = self.read(entry["path"])["content"]
            except AppError:
                continue
            for n, line in enumerate(text.splitlines(), 1):
                if query.lower() in line.lower():
                    matches.append({"path": entry["path"], "line": n, "text": line[:500]})
                    if len(matches) >= 100:
                        return matches
        return matches

    def write(self, raw, content, create):
        path = self.resolve(raw, write=True)
        encoded = content.encode("utf-8")
        if len(encoded) > self.settings.max_file_bytes:
            raise AppError("file_too_large", "Content exceeds the configured size limit.", 413)
        if not path.parent.is_dir():
            raise AppError("not_found", "Parent directory must already exist.", 404)
        if not create and not path.is_file():
            raise AppError("not_found", "File not found.", 404)
        try:
            # Exclusive create cannot overwrite; updates are approval-gated.
            with path.open("xb" if create else "r+b") as handle:
                if os.fstat(handle.fileno()).st_nlink > 1:
                    raise AppError("path_denied", "Hard-linked files cannot be modified.", 403)
                handle.write(encoded)
                handle.truncate()
                handle.flush()
                os.fsync(handle.fileno())
        except FileExistsError:
            raise AppError("file_exists", "Use file_update to preview and approve an overwrite.", 409) from None
        return {"path": str(path), "bytes_written": len(encoded)}
