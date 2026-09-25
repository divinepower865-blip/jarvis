from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo
import json
import os

from dotenv import load_dotenv
from pydantic import BaseModel, Field, SecretStr, field_validator


class Settings(BaseModel):
    api_token: SecretStr = SecretStr("")
    provider: Literal["mock", "groq"] = "mock"
    groq_key: SecretStr = SecretStr("")
    groq_model: str = "llama-3.1-8b-instant"
    stt_model: str = "whisper-large-v3-turbo"
    fish_key: SecretStr = SecretStr("")
    fish_voice_id: str = ""
    fish_model: str = "s2.1-pro-free"
    tavily_key: SecretStr = SecretStr("")
    data_dir: Path = Path("data")
    timezone: str = "Europe/Brussels"
    read_roots: list[Path] = Field(default_factory=list)
    write_roots: list[Path] = Field(default_factory=list)
    allowed_origins: list[str] = Field(default_factory=list)
    standing_permissions: list[str] = Field(default_factory=list)
    max_tool_calls: int = Field(12, ge=1, le=50)
    run_timeout: float = Field(180, ge=1, le=900)
    context_chars: int = Field(32000, ge=4000, le=200000)
    output_tokens: int = Field(512, ge=32, le=16000)
    approval_ttl: float = Field(120, ge=1, le=600)
    provider_timeout: float = Field(40, ge=0.1, le=180)
    stt_timeout: float = Field(20, ge=0.1, le=180)
    tts_timeout: float = Field(30, ge=0.1, le=180)
    search_timeout: float = Field(20, ge=0.1, le=180)
    connect_timeout: float = Field(5, ge=0.1, le=30)
    pool_timeout: float = Field(1, ge=0.1, le=10)
    keepalive_expiry: float = Field(60, ge=5, le=300)
    retries: int = Field(2, ge=0, le=4)
    retry_base: float = Field(0.5, ge=0, le=10)
    max_body_bytes: int = 2 * 1024 * 1024
    max_file_bytes: int = 256 * 1024
    max_active_runs: int = 8

    @field_validator("timezone")
    @classmethod
    def valid_zone(cls, value):
        ZoneInfo(value)
        return value

    @classmethod
    def from_env(cls):
        load_dotenv(override=False)
        mapping = {"api_token": "JARVIS_API_TOKEN", "provider": "JARVIS_PROVIDER",
                   "groq_key": "GROQ_API_KEY", "groq_model": "GROQ_MODEL",
                   "stt_model": "GROQ_STT_MODEL", "fish_key": "FISH_API_KEY",
                   "fish_voice_id": "FISH_VOICE_ID", "fish_model": "FISH_MODEL",
                   "tavily_key": "TAVILY_API_KEY"}
        values = {}
        for name in cls.model_fields:
            raw = os.getenv(mapping.get(name, "JARVIS_" + name.upper()))
            if raw is not None:
                values[name] = json.loads(raw) if name in {
                    "read_roots", "write_roots", "allowed_origins", "standing_permissions"
                } else raw
        return cls(**values)

    def secrets(self):
        return [s.get_secret_value() for s in
                (self.api_token, self.groq_key, self.fish_key, self.tavily_key)
                if s.get_secret_value()]

