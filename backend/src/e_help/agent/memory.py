from __future__ import annotations

from contextlib import AsyncExitStack
from hashlib import sha256
import logging
from typing import Any

from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, ConfigDict, Field

from ..config import Settings

logger = logging.getLogger(__name__)


class MemoryCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    should_remember: bool
    key: str = Field(default="", description="stable snake_case memory key")
    value: str = Field(default="", description="concise memory value")
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    reason: str = ""


class MemoryExtractionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    memories: list[MemoryCandidate] = Field(default_factory=list)


class MemoryManager:
    def __init__(self, settings: Settings, model) -> None:
        self.settings = settings
        self.model = model
        self.checkpointer = None
        self.store = None
        self._exit_stack = AsyncExitStack()
        self._extract_chain = (
            ChatPromptTemplate.from_messages(
                [
                    (
                        "system",
                        "从客服对话中抽取稳定、可复用的用户偏好或长期事实。"
                        "不要记录一次性问题、订单号、密钥、隐私敏感信息或知识库事实。"
                        "仅当用户明确表达长期偏好、身份背景或持续需求时 should_remember=true。",
                    ),
                    (
                        "human",
                        "用户问题：\n{user_message}\n\n助手回答：\n{assistant_message}",
                    ),
                ]
            )
            | model.bind(max_tokens=256).with_structured_output(MemoryExtractionResult)
        )

    async def setup(self) -> None:
        if not self.settings.db_url:
            raise RuntimeError("DB_URL is required for PostgreSQL memory")

        try:
            from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
            from langgraph.store.postgres import AsyncPostgresStore

            checkpointer_cm = AsyncPostgresSaver.from_conn_string(self.settings.db_url)
            store_cm = AsyncPostgresStore.from_conn_string(self.settings.db_url)
            self.checkpointer = await self._exit_stack.enter_async_context(checkpointer_cm)
            self.store = await self._exit_stack.enter_async_context(store_cm)
            await self.checkpointer.setup()
            await self.store.setup()
        except Exception as exc:  # noqa: BLE001
            await self._exit_stack.aclose()
            self.checkpointer = None
            self.store = None
            raise RuntimeError("PostgreSQL memory setup failed") from exc

    async def close(self) -> None:
        await self._exit_stack.aclose()

    async def recall(self, user_id: str | None) -> list[dict[str, Any]]:
        if not user_id:
            return []
        if self.store is None:
            raise RuntimeError("PostgreSQL memory store is not initialized")
        namespace = self._namespace(user_id)
        items = await self.store.asearch(namespace, limit=self.settings.long_term_memory_limit)
        return [
            {"key": item.key, **item.value}
            for item in items
            if isinstance(item.value, dict)
        ]

    async def extract_and_save(
        self,
        user_id: str | None,
        user_message: str,
        assistant_message: str,
    ) -> None:
        if not user_id:
            return
        if self.store is None:
            raise RuntimeError("PostgreSQL memory store is not initialized")
        try:
            result = await self._extract_chain.ainvoke(
                {
                    "user_message": user_message,
                    "assistant_message": assistant_message,
                }
            )
            extraction = (
                result
                if isinstance(result, MemoryExtractionResult)
                else MemoryExtractionResult.model_validate(result)
            )
            for candidate in extraction.memories:
                if not self._should_save(candidate):
                    continue
                key = candidate.key or self._key(candidate.value)
                await self.store.aput(
                    self._namespace(user_id),
                    key,
                    {
                        "value": candidate.value,
                        "confidence": candidate.confidence,
                        "reason": candidate.reason,
                    },
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Long-term memory extraction skipped: %s", exc)

    @staticmethod
    def _namespace(user_id: str) -> tuple[str, str, str]:
        return ("users", user_id, "memories")

    @staticmethod
    def _key(value: str) -> str:
        return sha256(value.encode()).hexdigest()[:16]

    @staticmethod
    def _should_save(candidate: MemoryCandidate) -> bool:
        if not candidate.should_remember:
            return False
        if candidate.confidence < 0.75:
            return False
        if not candidate.value.strip():
            return False
        lowered = candidate.value.lower()
        sensitive_markers = ("api key", "token", "密钥", "密码", "订单号", "手机号", "身份证")
        return not any(marker in lowered for marker in sensitive_markers)
