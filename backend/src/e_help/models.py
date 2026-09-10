from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class ThreadCreateResponse(BaseModel):
    thread_id: str
    created_at: datetime = Field(default_factory=datetime.utcnow)


class ChatMessage(BaseModel):
    role: str
    content: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class RetrievedChunk(BaseModel):
    document_id: str
    chunk_index: int
    content: str
    score: float
    document_location: str


class ChatRequest(BaseModel):
    thread_id: str | None = None
    message: str = Field(min_length=1, max_length=10000)


class ReindexResponse(BaseModel):
    status: str
    document_id: str
    chunks_indexed: int
