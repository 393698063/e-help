from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, TypedDict

from pydantic import BaseModel, ConfigDict, Field, field_validator

IntentType = Literal["chitchat", "rag", "tool"]
SafetyStatus = Literal["allow", "block"]


class QueryContext(TypedDict):
    original_query: str                 # 原始用户问题
    normalized_query: str               # 标准化后的用户问题
    contextualized_query: str           # 标准化后的用户问题 + 检索到的上下文
    intent: IntentType                  # 意图类型
    entities: dict[str, Any]            # 标准化后的用户问题中提取的实体
    need_retrieval: bool                # 是否需要检索上下文
    safety_status: SafetyStatus         # 安全状态
    safety_reason: str | None           # 安全状态原因


class NormalizeResult(BaseModel):
    normalized_query: str = Field(min_length=1)


class SafetyResult(BaseModel):
    status: SafetyStatus
    reason: str


class IntentEntities(BaseModel):
    """实体分类固定，避免结构化输出生成任意键的 JSON 对象。"""

    model_config = ConfigDict(extra="forbid") #表示如果模型返回未声明的字段，就拒绝这个对象

    product: str | None = None
    package: str | None = None
    topic: str | None = None
    time: str | None = None
    value: str | None = None


class IntentResult(BaseModel):
    intent: IntentType
    entities: IntentEntities = Field(default_factory=IntentEntities) # entities: dict[str, Any] = Field(default_factory=dict) ntities: dict[str, Any] 这种任意字典结构无法满足严格 JSON Schema 要求。
    need_retrieval: bool
    confidence: float = Field(ge=0, le=1)                   # 置信度、确信分数
    reason: str


class QueryAnalysisResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    safety_status: SafetyStatus
    safety_reason: str
    intent: IntentType
    entities: IntentEntities = Field(default_factory=IntentEntities)
    need_retrieval: bool
    confidence: float = Field(ge=0, le=1)
    reason: str


class RewriteResult(BaseModel):
    contextualized_query: str = Field(min_length=1)
    should_retrieve: bool = True


class RetrievedChunk(BaseModel):
    document_id: str
    chunk_index: int
    content: str
    score: float
    document_location: str


class RetrievedContext(BaseModel):
    chunks: list[RetrievedChunk] = Field(default_factory=list)
    query: str
    source: str = "milvus"


class ThreadCreateResponse(BaseModel):
    thread_id: str
    created_at: datetime = Field(default_factory=datetime.utcnow)


class ChatMessage(BaseModel):
    role: str
    content: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class ChatRequest(BaseModel):
    thread_id: str | None = None
    message: str = Field(min_length=1, max_length=10000)

    #这几行是 Pydantic 的字段校验器，作用是：在创建 ChatRequest 对象时，专门校验 message 字段。Pydantic 会在创建模型时自动调用它：
    @field_validator("message")
    #因此这里的 @classmethod 主要是配合 Pydantic 字段校验器的常见写法。Pydantic 2 推荐字段校验器写成：
    # 这个函数以类方法方式接收第一个参数 cls
    @classmethod
    def validate_message(cls, value: str) -> str:
        """校验并规范化消息内容。
        
            去除首尾空白字符后，确保消息不为空白字符串。
    
            Args:
                value: 待校验的原始消息内容。
    
            Returns:
                去除首尾空白后的消息内容。
    
            Raises:
                ValueError: 当消息去除空白后为空时抛出。
        """
        stripped = value.strip()
        if not stripped:
            raise ValueError("message must not be blank")
        return stripped


class ReindexResponse(BaseModel):
    status: str
    document_id: str
    chunks_indexed: int
