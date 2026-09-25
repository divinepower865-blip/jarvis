import hashlib
import json
import re


class AppError(Exception):
    def __init__(self, code: str, message: str, status: int = 400):
        self.code, self.message, self.status = code, message, status
        super().__init__(message)


SECRET_PATTERN = re.compile(
    r"(?:\b(?:sk[-_]|gsk_|tvly-)[A-Za-z0-9_-]{8,}|"
    r"(?i:bearer)\s+[A-Za-z0-9._-]{8,}|"
    r"(?i:password|api[_ -]?key|access[_ -]?token|secret)\s*[:=]\s*\S+|"
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----)"
)


class Redactor:
    def __init__(self, secrets=()):
        self.secrets = sorted((s for s in secrets if s), key=len, reverse=True)

    def text(self, value: str) -> str:
        for secret in self.secrets:
            value = value.replace(secret, "[REDACTED]")
        return SECRET_PATTERN.sub("[REDACTED]", value)

    def clean(self, value):
        if isinstance(value, str):
            return self.text(value)
        if isinstance(value, list):
            return [self.clean(v) for v in value]
        if isinstance(value, dict):
            return {self.text(str(k)): self.clean(v) for k, v in value.items()}
        return value

    def reject_secret(self, text: str):
        if self.text(text) != text:
            raise AppError("secret_rejected", "Credentials cannot be stored here.", 422)


def action_hash(tool: str, arguments: dict) -> str:
    raw = json.dumps([tool, arguments], sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()

