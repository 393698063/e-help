from __future__ import annotations

import asyncio
import json
from typing import Any

from langchain_core.tools import tool
from openai import APIConnectionError
from pydantic import BaseModel, Field
from pymilvus.exceptions import MilvusException

from ..chains import IntentChain, NormalizeChain, RagChain, RewriteChain
from ..config import Settings
from ..models import ChatMessage, RetrievedContext
from .runtime import AgentToolTrace, ToolBoundary


def _is_transient(error: Exception) -> bool:
    return isinstance(
        error,
        (APIConnectionError, ConnectionError, MilvusException, TimeoutError),
    )


class NormalizeToolInput(BaseModel):
    query: str = Field(min_length=1, max_length=2000)


class AnalyzeToolInput(BaseModel):
    normalized_query: str = Field(min_length=1, max_length=2000)


class RewriteToolInput(BaseModel):
    normalized_query: str = Field(min_length=1, max_length=2000)
    recent_messages: list[dict[str, str]] = Field(default_factory=list, max_length=12)
    conversation_summary: str = Field(default="", max_length=1200)


class RetrieveToolInput(BaseModel):
    query: str = Field(min_length=1, max_length=2000)


def build_tools(
    normalize_chain: NormalizeChain,
    intent_chain: IntentChain,
    rewrite_chain: RewriteChain,
    rag_chain: RagChain | None,
    trace: AgentToolTrace,
    recall_memory,
    settings: Settings,
):

    # 创建工具边界，用于限制工具调用次数和超时时间
    boundary = ToolBoundary(
        trace,
        max_retries=settings.tool_max_retries,                              # 限制重试次数  
        retry_initial_delay=settings.tool_retry_initial_delay_seconds,      # 限制重试的初始间隔时间
        retry_max_delay=settings.tool_retry_max_delay_seconds,              # 限制重试的最大间隔时间
        max_result_chars=settings.tool_max_result_chars,                    # 限制返回结果字符数
    )

    @tool(args_schema=NormalizeToolInput, description="规范化用户输入，不回答问题。")
    async def normalize_query(query: str) -> str | dict[str, Any]:
        if len(query) > settings.tool_max_query_length:
            return {
                "ok": False,
                "status": "invalid_input",
                "message": "query is too long",
                "retryable": False,
                "request_id": trace.request_id,
            }

        async def operation():
            # 纯 CPU 正则处理，直接调用即可，无需占用线程池。
            return normalize_chain.invoke(query).normalized_query

        normalized = await boundary.run(
            "normalize_query",                              # 工具名称
            operation,                                      # 操作函数
            timeout=settings.tool_model_timeout_seconds,    # 工具超时时间
            signature_data={"query": query},                # 签名数据
            retryable=_is_transient,                        # 判断是否可重试
            model_call=True,                                # 标记为模型调用
        )
        if isinstance(normalized, str):
            trace.normalized_query = normalized
        return normalized

    @tool(args_schema=AnalyzeToolInput, description="执行安全检查和用户意图分析。")
    async def analyze_query(normalized_query: str) -> dict[str, Any]:
        async def operation():
            # LLM 调用走原生异步，避免占用线程池导致高并发下排队。
            return await intent_chain.ainvoke(normalized_query)

        result = await boundary.run(
            "analyze_query",
            operation,
            timeout=settings.tool_model_timeout_seconds,
            signature_data={"normalized_query": normalized_query},
            retryable=_is_transient,
            model_call=True,
        )
        if not isinstance(result, tuple):
            return result

        safety, intent = result
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

    @tool(
        args_schema=RewriteToolInput,
        description="结合最近对话，将问题改写成独立的知识库检索问题。",
    )
    async def rewrite_query(
        normalized_query: str,
        recent_messages: list[dict[str, str]],
        conversation_summary: str = "",
    ) -> dict[str, Any]:
        history = [
            ChatMessage(role=message["role"], content=message["content"])
            for message in recent_messages[-12:]
            if message.get("role") in {"user", "assistant"} and message.get("content")
        ]

        async def operation():
            # LLM 调用走原生异步，避免占用线程池导致高并发下排队。
            return await rewrite_chain.ainvoke(
                normalized_query,
                history,
                conversation_summary,
            )

        rewrite = await boundary.run(
            "rewrite_query",
            operation,
            timeout=settings.tool_model_timeout_seconds,
            signature_data={
                "normalized_query": normalized_query,
                "recent_messages": [item.model_dump() for item in history],
                "conversation_summary": conversation_summary,
            },
            retryable=_is_transient,
            model_call=True,
        )
        if not hasattr(rewrite, "contextualized_query"):
            return rewrite
        if trace.query_context is not None:
            trace.query_context["contextualized_query"] = rewrite.contextualized_query
            trace.query_context["need_retrieval"] = rewrite.should_retrieve
        return {
            "contextualized_query": rewrite.contextualized_query,
            "should_retrieve": rewrite.should_retrieve,
        }

    # 检索工具，如果未配置 RAG 链，则直接返回空列表
    @tool(args_schema=RetrieveToolInput, description="从 Milvus 知识库检索相关片段。")
    async def retrieve_knowledge(query: str) -> list[dict[str, Any]] | dict[str, Any]:
        if rag_chain is None:
            trace.retrieved_context = RetrievedContext(chunks=[], query=query)
            return []

        async def operation():
            # 当前 Milvus 客户端是同步 API，放到线程中避免阻塞 FastAPI 事件循环。
            return await asyncio.to_thread(rag_chain.invoke, query)

        retrieved = await boundary.run(
            "retrieve_knowledge",
            operation,
            timeout=(
                settings.tool_embedding_timeout_seconds
                + settings.tool_milvus_timeout_seconds
            ),
            signature_data={"query": query},
            retryable=_is_transient,
        )
        if not isinstance(retrieved, RetrievedContext):
            return retrieved
        trace.retrieved_context = retrieved
        chunks: list[dict[str, Any]] = []
        used = 0
        for chunk in retrieved.chunks:
            item = chunk.model_dump()
            item_size = len(json.dumps(item, ensure_ascii=False))
            if used + item_size > settings.tool_max_result_chars:
                break
            chunks.append(item)
            used += item_size
        return chunks

    @tool(description="读取用户已确认的长期偏好，只用于个性化表达。")
    async def recall_user_memory() -> list[dict[str, Any]] | dict[str, Any]:
        async def operation():
            return await recall_memory()

        memories = await boundary.run(
            "recall_user_memory",
            operation,
            timeout=settings.tool_memory_timeout_seconds,
            signature_data={"user_id": trace.user_id, "query": trace.normalized_query},
            retryable=_is_transient,
        )
        if isinstance(memories, list):
            trace.recalled_memories = memories
        return memories

    return [
        normalize_query,
        analyze_query,
        rewrite_query,
        retrieve_knowledge,
        recall_user_memory,
    ]
