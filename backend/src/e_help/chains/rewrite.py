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
                        "最近对话：\n{recent_messages}\n\n当前问题：\n{normalized_query}",
                    ),
                ]
            )
            | model.bind(max_tokens=192).with_structured_output(RewriteResult)
        )

    def invoke(self, normalized_query: str, history: list[ChatMessage]) -> RewriteResult:
        recent = history[-self.history_turns :]
        if not self.needs_rewrite(normalized_query, recent):
            return RewriteResult(contextualized_query=normalized_query, should_retrieve=True)

        recent_messages = "\n".join(f"{message.role}: {message.content}" for message in recent)
        cache_key = self._cache_key(normalized_query, recent_messages)
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached

        try:
            result = self._chain.invoke(
                {
                    "recent_messages": recent_messages,
                    "normalized_query": normalized_query,
                }
            )
            rewrite = result if isinstance(result, RewriteResult) else RewriteResult.model_validate(result)
        except Exception:
            rewrite = RewriteResult(contextualized_query=normalized_query, should_retrieve=True)
        self._cache.set(cache_key, rewrite)
        return rewrite

    @classmethod
    def needs_rewrite(cls, query: str, history: list[ChatMessage]) -> bool:
        if not history:
            return False
        if len(query) <= 6:
            return True
        return any(marker in query for marker in cls._follow_up_markers)

    @staticmethod
    def _cache_key(query: str, recent_messages: str) -> str:
        payload = f"{query}\n{recent_messages}".encode()
        return sha256(payload).hexdigest()
