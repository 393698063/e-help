from __future__ import annotations

from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate

from ..models import ChatMessage, QueryContext, RetrievedContext
from ..prompts import CHITCHAT_SYSTEM_PROMPT, RAG_SYSTEM_PROMPT


class AnswerChain:
    def __init__(self, model) -> None:
        self._rag_chain = (
            ChatPromptTemplate.from_messages(
                [
                    ("system", RAG_SYSTEM_PROMPT),
                    (
                        "human",
                        "<conversation_summary>\n{conversation_summary}\n</conversation_summary>\n\n"
                        "<recent_messages>\n{recent_messages}\n</recent_messages>\n\n"
                        "<long_term_memory>\n{long_term_memory}\n</long_term_memory>\n\n"
                        "<retrieved_context>\n{retrieved_context}\n</retrieved_context>\n\n"
                        "用户问题：\n{contextualized_query}",
                    ),
                ]
            )
            | model
            | StrOutputParser() # StrOutputParser() 会从这个消息对象中提取 content，最终得到：content = retrieved_context + 用户问题： + contextualized_query
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
                        "<conversation_summary>\n{conversation_summary}\n</conversation_summary>\n\n"
                        "<recent_messages>\n{recent_messages}\n</recent_messages>\n\n"
                        "<long_term_memory>\n{long_term_memory}\n</long_term_memory>\n\n"
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
            {
                "conversation_summary": conversation_summary or "暂无摘要。",
                "recent_messages": self._format_messages(recent_messages or []),
                "long_term_memory": self._format_memories(long_term_memories or []),
                "retrieved_context": self._format_retrieved_context(retrieved),
                "contextualized_query": context["contextualized_query"],
            }
        )

    def answer_chitchat(
        self,
        query: str,
        *,
        conversation_summary: str = "",
        recent_messages: list[ChatMessage] | None = None,
        long_term_memories: list[dict] | None = None,
    ) -> str:
        return self._chitchat_chain.invoke(
            {
                "query": query,
                "conversation_summary": conversation_summary or "暂无摘要。",
                "recent_messages": self._format_messages(recent_messages or []),
                "long_term_memory": self._format_memories(long_term_memories or []),
            }
        )

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
