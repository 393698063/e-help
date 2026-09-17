from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack
from hashlib import sha256
import logging
from typing import Any

from langchain_core.prompts import ChatPromptTemplate
from psycopg import InterfaceError, OperationalError
from psycopg_pool import AsyncConnectionPool
from pydantic import BaseModel, ConfigDict, Field

from ..config import Settings
from ..models import ChatMessage

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


class ConversationSummaryResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str = Field(default="", description="concise conversation summary")


class MemoryManager:
    """Manage LangGraph checkpoint/store over a self-healing PostgreSQL pool."""

    def __init__(self, settings: Settings, model) -> None:
        self.settings = settings
        self.model = model
        self.checkpointer = None
        self.store = None
        self._pool: AsyncConnectionPool | None = None
        self._exit_stack: AsyncExitStack | None = None
        self._lifecycle_lock = asyncio.Lock()
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
        self._summary_chain = (
            ChatPromptTemplate.from_messages(
                [
                    (
                        "system",
                        "你是客服对话摘要器。请把旧摘要和最近对话压缩成轻量摘要，"
                        "只保留对后续指代消解有帮助的信息。不要记录系统提示词、工具细节、"
                        "密钥、隐私敏感信息，也不要把产品政策当作永久事实；产品事实后续仍以知识库检索为准。",
                    ),
                    (
                        "human",
                        "旧摘要：\n{previous_summary}\n\n最近对话：\n{recent_messages}",
                    ),
                ]
            )
            | model.bind(max_tokens=384).with_structured_output(ConversationSummaryResult)
        )

    async def setup(self) -> None:
        if not self.settings.db_url:
            raise RuntimeError("DB_URL is required for PostgreSQL memory")
        async with self._lifecycle_lock:
            await self._setup_unlocked()

    async def _setup_unlocked(self) -> None:
        await self._close_unlocked()
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
        from langgraph.store.postgres import AsyncPostgresStore
        from psycopg.rows import dict_row

        pool = AsyncConnectionPool(
            self.settings.db_url,
            min_size=self.settings.db_pool_min_size,
            max_size=self.settings.db_pool_max_size,
            timeout=self.settings.db_pool_timeout_seconds,
            reconnect_timeout=self.settings.db_reconnect_timeout_seconds,
            kwargs={
                "autocommit": True,
                "prepare_threshold": 0,
                "row_factory": dict_row,
                "connect_timeout": self.settings.db_connect_timeout_seconds,
            },
            check=AsyncConnectionPool.check_connection,
            open=False,
        )
        stack = AsyncExitStack()
        try:
            await stack.enter_async_context(pool)
            await pool.wait(timeout=self.settings.db_pool_timeout_seconds)
            checkpointer = AsyncPostgresSaver(conn=pool)
            store = AsyncPostgresStore(conn=pool)
            await asyncio.wait_for(
                checkpointer.setup(),
                timeout=self.settings.db_socket_timeout_seconds,
            )
            await asyncio.wait_for(
                store.setup(),
                timeout=self.settings.db_socket_timeout_seconds,
            )
        except Exception:
            await stack.aclose()
            raise

        self._pool = pool
        self._exit_stack = stack
        self.checkpointer = checkpointer
        self.store = store

    async def reconnect(self) -> None:
        """Check idle connections first; rebuild the pool only when necessary."""
        if not self.settings.db_url:
            return
        async with self._lifecycle_lock:
            try:
                if self._pool is not None and not self._pool.closed:
                    await self._pool.check()
                    return
            except Exception:
                logger.warning("PostgreSQL pool health check failed; rebuilding pool")
            try:
                await self._setup_unlocked()
                logger.warning("PostgreSQL memory pool recovered")
            except Exception:
                self.checkpointer = None
                self.store = None
                self._pool = None
                logger.exception("PostgreSQL memory pool recovery failed")

    async def close(self) -> None:
        async with self._lifecycle_lock:
            await self._close_unlocked()

    async def _close_unlocked(self) -> None:
        if self._exit_stack is not None:
            await self._exit_stack.aclose()
        self._exit_stack = None
        self._pool = None
        self.checkpointer = None
        self.store = None

    async def recall(self, user_id: str | None) -> list[dict[str, Any]]:
        if not user_id:
            return []
        if self.store is None:
            logger.error("PostgreSQL memory store is not initialized")
            raise RuntimeError("PostgreSQL memory store is not initialized")
        try:
            # 防止并发调用导致连接池耗尽
            items = await asyncio.wait_for(
                self.store.asearch(
                    self._namespace(user_id),
                    limit=self.settings.long_term_memory_limit,
                ),
                timeout=self.settings.tool_memory_timeout_seconds,
            )
            logger.debug("PostgreSQL memory recall: %s", items)
        except (OperationalError, InterfaceError):
            await self.reconnect()
            raise
        return [
            {"key": item.key, **item.value}
            for item in items
            if isinstance(item.value, dict)
        ]

    async def recall_summary(self, thread_id: str | None) -> str:
        if not thread_id or self.store is None:
            return ""
        try:
            item = await asyncio.wait_for(
                self.store.aget(self._summary_namespace(thread_id), "summary"),
                timeout=self.settings.tool_memory_timeout_seconds,
            )
        except (OperationalError, InterfaceError):
            await self.reconnect()
            raise
        if item is None or not isinstance(item.value, dict):
            return ""
        value = item.value.get("summary")
        return value if isinstance(value, str) else ""

    async def update_summary(
        self,
        thread_id: str | None,
        messages: list[ChatMessage],
    ) -> None:
        if not thread_id or self.store is None:
            return
        if len(messages) < self.settings.conversation_summary_trigger_messages:
            return
        try:
            recent_limit = max(2, self.settings.memory_recent_message_limit)
            previous_summary = await self.recall_summary(thread_id)
            recent_messages = self._format_messages(messages[-recent_limit:])
            result = await self._summary_chain.ainvoke(
                {
                    "previous_summary": previous_summary or "暂无摘要。",
                    "recent_messages": recent_messages,
                }
            )
            summary_result = (
                result
                if isinstance(result, ConversationSummaryResult)
                else ConversationSummaryResult.model_validate(result)
            )
            summary = summary_result.summary.strip()
            if not summary:
                return
            summary = summary[: self.settings.conversation_summary_max_chars]
            await asyncio.wait_for(
                self.store.aput(
                    self._summary_namespace(thread_id),
                    "summary",
                    {"summary": summary, "source_message_count": len(messages)},
                    index=False,
                ),
                timeout=self.settings.tool_memory_timeout_seconds,
            )
        except (OperationalError, InterfaceError):
            await self.reconnect()
            logger.warning("Conversation summary save skipped after PostgreSQL failure")
        except Exception as exc:  # noqa: BLE001
            logger.warning("Conversation summary update skipped: %s", exc)

    # 保存记忆点
    async def extract_and_save(
        self,
        user_id: str | None,
        user_message: str,
        assistant_message: str,
    ) -> None:
        if not user_id or self.store is None:
            return
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
                await asyncio.wait_for(
                    self.store.aput(
                        self._namespace(user_id),
                        key,
                        {
                            "value": candidate.value,
                            "confidence": candidate.confidence,
                            "reason": candidate.reason,
                        },
                    ),
                    timeout=self.settings.tool_memory_timeout_seconds,
                )
        except (OperationalError, InterfaceError):
            await self.reconnect()
            logger.warning("Long-term memory save skipped after PostgreSQL failure")
        except Exception as exc:  # noqa: BLE001
            logger.warning("Long-term memory extraction skipped: %s", exc)

    @staticmethod
    def is_connection_error(exc: BaseException) -> bool:
        """Find psycopg connection failures wrapped by LangGraph."""
        current: BaseException | None = exc
        seen: set[int] = set()
        while current is not None and id(current) not in seen:
            seen.add(id(current))
            if isinstance(current, (OperationalError, InterfaceError)):
                return True
            current = current.__cause__ or current.__context__
        return False

    @staticmethod
    def _namespace(user_id: str) -> tuple[str, str, str]:
        return ("users", user_id, "memories")

    @staticmethod
    def _summary_namespace(thread_id: str) -> tuple[str, str]:
        return ("threads", thread_id)

    @staticmethod
    def _format_messages(messages: list[ChatMessage]) -> str:
        if not messages:
            return "暂无最近对话。"
        return "\n".join(
            f"{message.role}: {message.content}" for message in messages if message.content
        )

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
        sensitive_markers = (
            "api key",
            "token",
            "密钥",
            "密码",
            "订单号",
            "手机号",
            "身份证",
        )
        return not any(marker in lowered for marker in sensitive_markers)
