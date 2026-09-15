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
    @tool(parse_docstring=True)
    def normalize_query(query: str) -> str:
        """规范化用户输入的空格、换行和标点，不改变语义。

        Args:
            query: 待规范化的用户原始输入。

        Returns:
            规范化后的用户输入。
        """
        normalized = normalize_chain.invoke(query).normalized_query
        trace.normalized_query = normalized
        return normalized

    @tool(parse_docstring=True)
    def analyze_query(normalized_query: str) -> dict[str, Any]:
        """对规范化后的问题进行安全检查和意图识别。

        Args:
            normalized_query: 已规范化的用户问题。

        Returns:
            包含安全状态、用户意图、提取实体、是否需要检索和置信度的分析结果。
        """
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
    # TODO: rewrite 是否可以不调用模型
    @tool(parse_docstring=True)
    def rewrite_query(normalized_query: str, recent_messages: list[dict[str, str]]) -> dict[str, Any]:
        """结合最近对话把问题改写成可独立检索的问题。

        Args:
            normalized_query: 已规范化的当前用户问题。
            recent_messages: 最近的对话消息列表。每条消息应包含 ``role`` 和
                ``content`` 字段。

        Returns:
            包含独立检索问题和是否应执行检索的改写结果。
        """
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

    @tool(parse_docstring=True)
    def retrieve_knowledge(query: str) -> list[dict[str, Any]]:
        """从 Milvus 知识库检索与问题相关的片段。

        该工具仅用于电商售后知识问答。

        Args:
            query: 用于检索知识库的用户问题。

        Returns:
            与问题相关的知识片段列表。每个片段均为可序列化的字典。
        """
        if rag_chain is None:
            trace.retrieved_context = RetrievedContext(chunks=[], query=query)
            return []
        retrieved = rag_chain.invoke(query)
        trace.retrieved_context = retrieved
        return [chunk.model_dump() for chunk in retrieved.chunks]

    @tool(parse_docstring=True)
    async def recall_user_memory() -> list[dict[str, Any]]:
        """查询该用户已确认的长期偏好。

        记忆仅用于个性化表达，不能作为知识库事实。

        Returns:
            该用户已确认的长期记忆列表。
        """
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
