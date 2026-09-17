from __future__ import annotations

from hashlib import sha256

from langchain_core.prompts import ChatPromptTemplate

from ..models import ChatMessage, RewriteResult
from ..prompts import REWRITE_SYSTEM_PROMPT
from .cache import BoundedCache


class RewriteChain:
    _follow_up_markers = (
        "它",
        "这个",
        "那个",
        "这样",
        "那样",
        "上一个",
        "上一条",
        "刚才",
        "前面",
        "之前",
        "这个呢",
        "那怎么办",
        "然后呢",
    )

    def __init__(self, model, history_turns: int = 6) -> None:
        self.history_turns = history_turns
        self._cache: BoundedCache[str, RewriteResult] = BoundedCache()
        self._chain = (
            ChatPromptTemplate.from_messages(
                [
                    ("system", REWRITE_SYSTEM_PROMPT),
                    (
                        "human",
                        "对话摘要：\n{conversation_summary}\n\n"
                        "最近对话：\n{recent_messages}\n\n当前问题：\n{normalized_query}",
                    ),
                ]
            )
            | model.bind(max_tokens=192).with_structured_output(RewriteResult)
        )

    def invoke(
        self,
        normalized_query: str,
        history: list[ChatMessage],
        conversation_summary: str = "",
    ) -> RewriteResult:
        early, cache_key, payload = self._precheck(
            normalized_query,
            history,
            conversation_summary,
        )
        if early is not None:
            return early

        try:
            rewrite = self._parse(self._chain.invoke(payload))
        except Exception:
            rewrite = RewriteResult(contextualized_query=normalized_query, should_retrieve=True)
        self._cache.set(cache_key, rewrite)
        return rewrite

    async def ainvoke(
        self,
        normalized_query: str,
        history: list[ChatMessage],
        conversation_summary: str = "",
    ) -> RewriteResult:
        early, cache_key, payload = self._precheck(
            normalized_query,
            history,
            conversation_summary,
        )
        if early is not None:
            return early

        try:
            rewrite = self._parse(await self._chain.ainvoke(payload))
        except Exception:
            rewrite = RewriteResult(contextualized_query=normalized_query, should_retrieve=True)
        self._cache.set(cache_key, rewrite)
        return rewrite

    def _precheck(
        self,
        normalized_query: str,
        history: list[ChatMessage],
        conversation_summary: str,
    ):
        """公共预处理：命中免改写/缓存则返回结果，否则给出缓存键和模型入参。"""
        recent = history[-max(1, self.history_turns) * 2 :]
        if not self.needs_rewrite(normalized_query, recent, conversation_summary):
            return RewriteResult(contextualized_query=normalized_query, should_retrieve=True), None, None

        recent_messages = "\n".join(f"{message.role}: {message.content}" for message in recent)
        cache_key = self._cache_key(normalized_query, recent_messages, conversation_summary)
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached, None, None

        payload = {
            "conversation_summary": conversation_summary or "暂无摘要。",
            "recent_messages": recent_messages or "暂无最近对话。",
            "normalized_query": normalized_query,
        }
        return None, cache_key, payload

    @staticmethod
    def _parse(result) -> RewriteResult:
        return result if isinstance(result, RewriteResult) else RewriteResult.model_validate(result)

    @classmethod
    def needs_rewrite(
        cls,
        query: str,
        history: list[ChatMessage],
        conversation_summary: str = "",
    ) -> bool:
        if not history and not conversation_summary:
            return False
        if len(query) <= 6:
            return True
        return any(marker in query for marker in cls._follow_up_markers)

    @staticmethod
    def _cache_key(query: str, recent_messages: str, conversation_summary: str) -> str:
        payload = f"{query}\n{conversation_summary}\n{recent_messages}".encode()
        return sha256(payload).hexdigest()
