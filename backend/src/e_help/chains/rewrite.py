from __future__ import annotations

from langchain_core.prompts import ChatPromptTemplate

from ..models import ChatMessage, RewriteResult
from ..prompts import REWRITE_SYSTEM_PROMPT


class RewriteChain:
    def __init__(self, model, history_turns: int = 6) -> None:
        self.history_turns = history_turns
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
            | model.with_structured_output(RewriteResult)
        )

    def invoke(self, normalized_query: str, history: list[ChatMessage]) -> RewriteResult:
        recent = history[-self.history_turns :]
        if not recent:
            return RewriteResult(contextualized_query=normalized_query, should_retrieve=True)
        recent_messages = "\n".join(f"{message.role}: {message.content}" for message in recent)
        try:
            result = self._chain.invoke(
                {
                    "recent_messages": recent_messages,
                    "normalized_query": normalized_query,
                }
            )
            return result if isinstance(result, RewriteResult) else RewriteResult.model_validate(result)
        except Exception:
            return RewriteResult(contextualized_query=normalized_query, should_retrieve=True)
