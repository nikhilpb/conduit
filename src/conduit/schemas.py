"""Pydantic schemas for the FastAPI surface."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel
from pydantic import Field


class HealthResponse(BaseModel):
    ok: bool
    app_name: str
    model: str
    model_label: str
    provider: str
    provider_api_key_configured: bool


class SessionResponse(BaseModel):
    session_id: str
    last_update_time: float
    event_count: int
    title: str


class SessionListResponse(BaseModel):
    sessions: list[SessionResponse]


class CreateSessionResponse(BaseModel):
    session_id: str


class ToolCall(BaseModel):
    tool_call_id: str | None = None
    name: str
    args: dict[str, Any] = Field(default_factory=dict)
    status: str = "pending"
    error: str | None = None


class TranscriptMessage(BaseModel):
    message_id: str
    role: str
    text: str
    created_at: float
    thinking_trace: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)


class SessionDetailResponse(BaseModel):
    session_id: str
    messages: list[TranscriptMessage] = Field(default_factory=list)


class ChatContextRequest(BaseModel):
    current_time: str = ""
    location: str = ""
    personal_instructions: str = ""


class ChatRequest(BaseModel):
    message: str = Field(min_length=1)
    session_id: str | None = None
    context: ChatContextRequest | None = None


class ChatResponse(BaseModel):
    session_id: str
    reply: str
    tool_calls: list[ToolCall] = Field(default_factory=list)


class ModelOptionResponse(BaseModel):
    key: str
    label: str
    model: str
    provider: str
    available: bool


class ModelSettingsResponse(BaseModel):
    active_key: str
    active_model: str
    active_label: str
    provider: str
    options: list[ModelOptionResponse] = Field(default_factory=list)


class UpdateModelRequest(BaseModel):
    model_key: str = Field(min_length=1)
