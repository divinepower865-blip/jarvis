from datetime import datetime
from typing import Any, Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Empty(StrictModel):
    pass


class Query(StrictModel):
    query: str = Field("", max_length=500)


class ItemCreate(StrictModel):
    title: str = Field(min_length=1, max_length=200)
    content: str = Field("", max_length=16000)


class Identifier(StrictModel):
    id: str = Field(min_length=1, max_length=64)


class ItemUpdate(Identifier):
    title: str | None = Field(None, min_length=1, max_length=200)
    content: str | None = Field(None, max_length=16000)
    status: Literal["active", "completed", "outdated", "superseded"] | None = None


class FilePath(StrictModel):
    path: str = Field(min_length=1, max_length=2048)


class FileSearch(FilePath):
    query: str = Field(min_length=1, max_length=500)


class FileWrite(FilePath):
    content: str = Field(max_length=100000)


class TimeInput(StrictModel):
    timezone: str | None = None

    @field_validator("timezone")
    @classmethod
    def zone(cls, value):
        if value is not None:
            ZoneInfo(value)
        return value


class ReminderCreate(StrictModel):
    title: str = Field(min_length=1, max_length=200)
    content: str = Field("", max_length=4000)
    at: datetime
    timezone: str = "Europe/Brussels"
    recurrence: Literal["once", "daily", "weekly"] = "once"

    @model_validator(mode="after")
    def aware(self):
        ZoneInfo(self.timezone)
        if self.at.tzinfo is None or self.at.utcoffset() is None:
            raise ValueError("at must include a UTC offset")
        return self


class ToolResult(StrictModel):
    ok: bool = True
    data: dict[str, Any] | list[dict[str, Any]] | None = None
    error: dict[str, str] | None = None


class ScheduledToolCreate(ReminderCreate):
    tool: Literal["task_create", "task_complete", "note_create"]
    arguments: dict[str, Any]


class ToolCall(StrictModel):
    id: str = Field(max_length=200)
    name: str = Field(max_length=100)
    arguments: dict[str, Any]


class ModelTurn(StrictModel):
    content: str = ""
    calls: list[ToolCall] = Field(default_factory=list, max_length=50)
    usage: dict[str, Any] | None = None


class RunInput(StrictModel):
    message: str = Field(min_length=1, max_length=16000)
    # These are client-granted capabilities, never inferred from retrieved text.
    permitted_tools: list[str] = Field(default_factory=list, max_length=40)


class ToolInvocation(StrictModel):
    arguments: dict[str, Any]


class ApprovalDecision(StrictModel):
    approve: bool
    digest: str = Field(min_length=64, max_length=64)


class ConversationCreate(StrictModel):
    title: str = Field("New conversation", min_length=1, max_length=200)


class SpeechInput(StrictModel):
    text: str = Field(min_length=1, max_length=4000)
    format: Literal["mp3", "wav", "opus"] = "mp3"


class TranscriptionInput(StrictModel):
    audio_base64: str = Field(min_length=1, max_length=1800000)
    format: Literal["wav", "mp3", "flac", "ogg", "webm"]
