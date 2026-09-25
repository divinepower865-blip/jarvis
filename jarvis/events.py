"""Discriminated, typed event envelope shared by persisted replay and clients."""
from typing import Annotated, Any, Literal, Union

from pydantic import Field, TypeAdapter, create_model

from .schemas import StrictModel, ToolResult


class Started(StrictModel):
    provider: str
    mock: bool


class Delta(StrictModel):
    text: str


class ToolStarted(StrictModel):
    tool: str
    call_id: str


class ToolCompleted(ToolStarted):
    result: ToolResult


class ToolFailed(ToolStarted):
    ok: Literal[False]
    error: dict[str, str]


class ApprovalRequired(StrictModel):
    approval_id: str
    expires_at: float
    tool: str
    arguments: dict[str, Any]
    digest: str
    target: dict[str, Any] | None = None
    previous: dict[str, Any] | None = None


class Retry(StrictModel):
    attempt: int


class Usage(StrictModel):
    usage: dict[str, Any]


class Terminal(StrictModel):
    status: Literal["completed", "failed", "cancelled"]
    result: str | None
    error_code: str | None


class Notification(StrictModel):
    job_id: str
    title: str
    content: str
    scheduled_at: float
    late: bool
    result: ToolResult | None = None
    error_code: str | None = None


PAYLOADS = {"run.started": Started, "response.delta": Delta,
            "tool.started": ToolStarted, "tool.completed": ToolCompleted,
            "tool.failed": ToolFailed, "approval.required": ApprovalRequired,
            "provider.retry": Retry, "provider.usage": Usage,
            "run.completed": Terminal, "run.failed": Terminal,
            "run.cancelled": Terminal, "notification.reminder": Notification,
            "notification.job_completed": Notification, "notification.job_failed": Notification}

variants = [create_model(name.replace(".", "_").title(), __base__=StrictModel,
                        event_id=(int, ...), event_type=(Literal[name], ...),
                        conversation_id=(str | None, ...), run_id=(str | None, ...),
                        timestamp=(float, ...), payload=(payload, ...))
            for name, payload in PAYLOADS.items()]
EVENT_ADAPTER = TypeAdapter(Annotated[Union[tuple(variants)], Field(discriminator="event_type")])
