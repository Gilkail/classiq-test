"""Pydantic request/response models for the API (REFERENCE §6)."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class SubmitRequest(BaseModel):
    """POST /tasks body: exactly ``{"qc": "<non-empty string>"}``."""

    # forbid unknown keys -> extra fields raise a validation error (F1).
    model_config = ConfigDict(extra="forbid")

    qc: str = Field(min_length=1)


class SubmitResponse(BaseModel):
    """202 body for an accepted submission."""

    task_id: str
    message: str = "Task submitted successfully."


class CompletedResponse(BaseModel):
    """200 body for a finished task; ``result`` is the counts dict."""

    status: str = "completed"
    result: dict[str, int]


class PendingResponse(BaseModel):
    """200 body while a task is still queued/running."""

    status: str = "pending"
    message: str = "Task is still in progress."


class ErrorResponse(BaseModel):
    """Body for error / not-found / validation / queue-unavailable cases."""

    status: str = "error"
    message: str
