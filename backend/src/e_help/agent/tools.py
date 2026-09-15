from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from langchain_core.tools import tool

from ..chains import IntentChain, NormalizeChain, RagChain, RewriteChain
from ..models import ChatMessage, IntentResult, QueryContext, RetrievedContext


@dataclass(slots=True)
class AgentToolTrace:
    normalized_query: str | None = None
    intent: IntentResult | None = None
    query_context: QueryContext | None = None
    retrieved_context: RetrievedContext | None = None
    recalled_memories: list[dict[str, Any]] = field(default_factory=list)


def build_tools(
    normalize_chain: NormalizeChain,
    intent_chain: IntentChain,
    rewrite_chain: RewriteChain,
    rag_chain: RagChain | None,
    trace: AgentToolTrace,
    recall_memory,
):
    @tool
    def normalize_query(query: str) -> str:
        """规范化用户输入的空格、换行和标点，不改变语义。"""
        normalized = normalize_chain.invoke(query).normalized_query
        trace.normalized_query = normalized
        return normalized

    @tool
    def analyze_query(normalized_query: str) -> dict[str, Any]:
        """对规范化后的问题做安全检查和意图识别。"""
        safety, intent = intent_chain.invoke(normalized_query)
        trace.intent = intent
        trace.query_context = {
            "original_query": normalized_query,
            "normalized_query": normalized_query,
            "contextualized_query": normalized_query,
            "intent": intent.intent,
            "entities": intent.entities.model_dump(exclude_none=True),
            "need_retrieval": intent.need_retrieval,
            "safety_status": safety.status,
            "safety_reason": safety.reason,
        }
        return {
            "safety_status": safety.status,
            "safety_reason": safety.reason,
            "intent": intent.intent,
            "entities": intent.entities.model_dump(exclude_none=True),
            "need_retrieval": intent.need_retrieval,
            "confidence": intent.confidence,
            "reason": intent.reason,
        }

    @tool
    def rewrite_query(normalized_query: str, recent_messages: list[dict[str, str]]) -> dict[str, Any]:
        """结合最近对话把问题改写成可独立检索的问题。"""
        history = [
            ChatMessage(role=message["role"], content=message["content"])
            for message in recent_messages
            if message.get("role") in {"user", "assistant"} and message.get("content")
        ]
        rewrite = rewrite_chain.invoke(normalized_query, history)
        if trace.query_context is not None:
            trace.query_context["contextualized_query"] = rewrite.contextualized_query
            trace.query_context["need_retrieval"] = rewrite.should_retrieve
        return {
            "contextualized_query": rewrite.contextualized_query,
            "should_retrieve": rewrite.should_retrieve,
        }

    @tool
    def retrieve_knowledge(query: str) -> list[dict[str, Any]]:
        """从 Milvus 知识库检索与问题相关的片段，仅用于电商售后知识问答。"""
        if rag_chain is None:
            trace.retrieved_context = RetrievedContext(chunks=[], query=query)
            return []
        retrieved = rag_chain.invoke(query)
        trace.retrieved_context = retrieved
        return [chunk.model_dump() for chunk in retrieved.chunks]

    @tool
    async def recall_user_memory() -> list[dict[str, Any]]:
        """查询该用户已确认的长期偏好，仅用于个性化表达，不能作为知识库事实。"""
        memories = await recall_memory()
        trace.recalled_memories = memories
        return memories

    return [
        normalize_query,
        analyze_query,
        rewrite_query,
        retrieve_knowledge,
        recall_user_memory,
    ]
