from __future__ import annotations

from contextlib import AsyncExitStack
from hashlib import sha256
import logging
from typing import Any

from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, ConfigDict, Field

from ..config import Settings

logger = logging.getLogger(__name__)

# 记忆候选模型
class MemoryCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    should_remember: bool
    key: str = Field(default="", description="stable snake_case memory key")
    value: str = Field(default="", description="concise memory value")
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    reason: str = ""


class MemoryExtractionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")                           # 配置项，表示模型允许的额外字段

    memories: list[MemoryCandidate] = Field(default_factory=list)        # 记忆候选列表


class MemoryManager:
    def __init__(self, settings: Settings, model) -> None:
        self.settings = settings
        self.model = model
        self.checkpointer = None
        self.store = None
        self._exit_stack = AsyncExitStack()         # 用于管理异步上下文管理器，AsyncExitStack 是管理异步资源生命周期的强大工具，特别适合需要动态管理多个异步资源的复杂场景
        self._extract_chain = (                     # 用于从对话中提取记忆候选
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

            checkpointer_cm = AsyncPostgresSaver.from_conn_string(self.settings.db_url)     # 类型：AsyncPostgresSaver
            store_cm = AsyncPostgresStore.from_conn_string(self.settings.db_url)            # 类型：AsyncPostgresStore
            self.checkpointer = await self._exit_stack.enter_async_context(checkpointer_cm) # enter_async_context 最重要的方法，用于注册一个新的异步上下文管理器：
            self.store = await self._exit_stack.enter_async_context(store_cm)               # 进入异步上下文管理器，并返回其结果
            await self.checkpointer.setup()                                                 # 初始化检查点
            await self.store.setup()                                                        # 初始化存储
        except Exception as exc:  # noqa: BLE001
            await self._exit_stack.aclose()
            self.checkpointer = None
            self.store = None
            raise RuntimeError("PostgreSQL memory setup failed") from exc

    async def close(self) -> None:
        await self._exit_stack.aclose()                                                     # 关闭所有异步上下文管理器

    async def recall(self, user_id: str | None) -> list[dict[str, Any]]:
        """检索指定用户的长期记忆列表。

        根据用户 ID 构造命名空间，从 PostgreSQL 存储中搜索并返回该用户
        已保存的记忆条目，条目数量受配置的长期记忆上限约束。

        Args:
            user_id: 用户唯一标识，为空时不检索任何记忆。

        Returns:
            记忆条目列表，每项包含 "key" 及记忆值字典中的其他字段；
            若 user_id 为空则返回空列表。

        Raises:
            RuntimeError: 当存储未初始化时抛出。
        """
        if not user_id:
            return []
        if self.store is None:
            raise RuntimeError("PostgreSQL memory store is not initialized")
        namespace = self._namespace(user_id)                                                    # 返回一个元组，表示命名空间
        items = await self.store.asearch(namespace, limit=self.settings.long_term_memory_limit) # 搜索命名空间中的项目
        return [
            {"key": item.key, **item.value}
            for item in items
            if isinstance(item.value, dict)                         # 确保 item.value 是字典类型
        ]

    async def extract_and_save(
        self,
        user_id: str | None,
        user_message: str,
        assistant_message: str,
    ) -> None:
        """从对话中提取记忆候选并保存到 PostgreSQL 存储。"""
        if not user_id:
            return
        if self.store is None:
            raise RuntimeError("PostgreSQL memory store is not initialized")
        try:
            result = await self._extract_chain.ainvoke(                       # 调用提取链，传入对话内容，提取记忆候选
                {
                    "user_message": user_message,
                    "assistant_message": assistant_message,
                }
            )
            extraction = (                                                     # 提取结果，类型为 MemoryExtractionResult
                result
                if isinstance(result, MemoryExtractionResult)
                else MemoryExtractionResult.model_validate(result)
            )
            for candidate in extraction.memories:
                if not self._should_save(candidate):
                    continue
                key = candidate.key or self._key(candidate.value)
                await self.store.aput(                                          # 保存记忆候选到 PostgreSQL 存储
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
        if not candidate.should_remember:                   # 如果 should_remember 为 False，则不保存
            return False
        if candidate.confidence < 0.75:                     # 如果置信度低于 0.75，则不保存
            return False
        if not candidate.value.strip():                     # 如果记忆值为空字符串，则不保存
            return False
        lowered = candidate.value.lower()                   # 将记忆值转换为小写
        sensitive_markers = ("api key", "token", "密钥", "密码", "订单号", "手机号", "身份证")
        return not any(marker in lowered for marker in sensitive_markers)                   # 如果记忆值中包含敏感标记，则不保存

