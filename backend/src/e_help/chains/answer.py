from __future__ import annotations

from collections.abc import AsyncIterator

from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate

from ..models import ChatMessage, QueryContext, RetrievedContext
from ..prompts import (
    CHITCHAT_MEMORY_PROMPT,
    CHITCHAT_SYSTEM_PROMPT,
    RAG_CONTEXT_PROMPT,
    RAG_MEMORY_PROMPT,
    RAG_RUNTIME_PROMPT,
    RAG_SYSTEM_PROMPT,
)


class AnswerChain:
    def __init__(self, model) -> None:
        self._rag_chain = (
            ChatPromptTemplate.from_messages(
                [
                    ("system", RAG_SYSTEM_PROMPT),
                    ("system", RAG_RUNTIME_PROMPT),
                    (
                        "human",
                        # 记忆和检索结果都是外部数据，不提升为 SystemMessage，
                        # 避免知识库或历史消息中的文本获得系统指令级别。
                        f"{RAG_MEMORY_PROMPT}\n\n"
                        f"{RAG_CONTEXT_PROMPT}\n\n"
                        "用户问题：\n{contextualized_query}",
                    ),
                ]
            )
            | model
            | StrOutputParser()
        )
        ## 这里是在构建一条 LangChain 链，不是立即执行模型调用，而是返回一个链对象
#         构建链的时候：
            # prompt | model | parser
            # 只是把流程定义好；真正执行是在：
            # chain.invoke(...)
        self._chitchat_chain = (
            ChatPromptTemplate.from_messages(
                [
                    ("system", CHITCHAT_SYSTEM_PROMPT),
                    (
                        "human",
                        # 闲聊同样把历史和长期偏好作为数据传入，不能覆盖系统规则。
                        f"{CHITCHAT_MEMORY_PROMPT}\n\n"
                        "用户消息：\n{query}",
                    ),
                ]
            )
            | model                                 # 这个是调用了什么
            | StrOutputParser()
        )

    def answer_rag(
        self,
        context: QueryContext,
        retrieved: RetrievedContext,
        *,
        conversation_summary: str = "",
        recent_messages: list[ChatMessage] | None = None,
        long_term_memories: list[dict] | None = None,
    ) -> str:
        return self._rag_chain.invoke(
            self._rag_payload(
                context,
                retrieved,
                conversation_summary=conversation_summary,
                recent_messages=recent_messages,
                long_term_memories=long_term_memories,
            )
        )

    async def astream_rag(
        self,
        context: QueryContext,
        retrieved: RetrievedContext,
        *,
        conversation_summary: str = "",
        recent_messages: list[ChatMessage] | None = None,
        long_term_memories: list[dict] | None = None,
    ) -> AsyncIterator[str]:
        payload = self._rag_payload(
            context,
            retrieved,
            conversation_summary=conversation_summary,
            recent_messages=recent_messages,
            long_term_memories=long_term_memories,
        )
        async for chunk in self._rag_chain.astream(payload):
            yield self._chunk_text(chunk)

    def answer_chitchat(
        self,
        query: str,
        *,
        conversation_summary: str = "",
        recent_messages: list[ChatMessage] | None = None,
        long_term_memories: list[dict] | None = None,
    ) -> str:
        return self._chitchat_chain.invoke(
            self._chitchat_payload(
                query,
                conversation_summary=conversation_summary,
                recent_messages=recent_messages,
                long_term_memories=long_term_memories,
            )
        )

    async def astream_chitchat(
        self,
        query: str,
        *,
        conversation_summary: str = "",
        recent_messages: list[ChatMessage] | None = None,
        long_term_memories: list[dict] | None = None,
    ) -> AsyncIterator[str]:
        payload = self._chitchat_payload(
            query,
            conversation_summary=conversation_summary,
            recent_messages=recent_messages,
            long_term_memories=long_term_memories,
        )
        async for chunk in self._chitchat_chain.astream(payload):
            yield self._chunk_text(chunk)

    def _rag_payload(
        self,
        context: QueryContext,
        retrieved: RetrievedContext,
        *,
        conversation_summary: str,
        recent_messages: list[ChatMessage] | None,
        long_term_memories: list[dict] | None,
    ) -> dict:
        return {
            "conversation_summary": conversation_summary or "暂无摘要。",
            "recent_messages": self._format_messages(recent_messages or []),
            "long_term_memory": self._format_memories(long_term_memories or []),
            "retrieved_context": self._format_retrieved_context(retrieved),
            "contextualized_query": context["contextualized_query"],
        }

    def _chitchat_payload(
        self,
        query: str,
        *,
        conversation_summary: str,
        recent_messages: list[ChatMessage] | None,
        long_term_memories: list[dict] | None,
    ) -> dict:
        return {
            "query": query,
            "conversation_summary": conversation_summary or "暂无摘要。",
            "recent_messages": self._format_messages(recent_messages or []),
            "long_term_memory": self._format_memories(long_term_memories or []),
        }

    @staticmethod
    def _chunk_text(chunk) -> str:
        if isinstance(chunk, str):
            return chunk
        content = getattr(chunk, "content", "")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for part in content:
                if isinstance(part, str):
                    parts.append(part)
                elif isinstance(part, dict) and part.get("type") == "text":
                    parts.append(str(part.get("text", "")))
            return "".join(parts)
        return ""

    #声明为静态方法
    @staticmethod
    def _format_retrieved_context(retrieved: RetrievedContext) -> str:
        if not retrieved.chunks:
            return "没有检索到可用上下文。"
        blocks = []
        for index, chunk in enumerate(retrieved.chunks, start=1):
            blocks.append(
                f"[chunk-{index}]\n"
                f"document_id: {chunk.document_id}\n"
                f"chunk_index: {chunk.chunk_index}\n"
                f"score: {chunk.score:.4f}\n"
                f"content:\n{chunk.content}"
            )
        return "\n\n".join(blocks)

    @staticmethod
    def _format_messages(messages: list[ChatMessage]) -> str:
        if not messages:
            return "暂无最近对话。"
        return "\n".join(
            f"{message.role}: {message.content}" for message in messages if message.content
        )

    @staticmethod
    def _format_memories(memories: list[dict]) -> str:
        if not memories:
            return "暂无长期记忆。"
        blocks = []
        for index, memory in enumerate(memories, start=1):
            key = memory.get("key", f"memory_{index}")
            value = memory.get("value", "")
            confidence = memory.get("confidence")
            blocks.append(
                f"[memory-{index}]\n"
                f"key: {key}\n"
                f"value: {value}\n"
                f"confidence: {confidence}"
            )
        return "\n\n".join(blocks)
