from __future__ import annotations

import asyncio
import json
import logging
import random
from collections import defaultdict
from dataclasses import dataclass, field
from time import monotonic
from typing import Any, Awaitable, Callable
from uuid import uuid4

from langchain_core.callbacks import BaseCallbackHandler
from pydantic import BaseModel

logger = logging.getLogger(__name__)


class AgentRunLimitExceeded(RuntimeError):
    """当前 Agent 运行超出模型或工具预算。"""


class ToolResult(BaseModel):
    """工具对 Agent 返回的稳定协议，不暴露内部堆栈。"""

    ok: bool
    status: str
    data: Any = None
    message: str = ""
    retryable: bool = False
    request_id: str
    tool_call_id: str | None = None


@dataclass(slots=True)
class RunBudget:
    max_model_calls: int
    max_tool_calls: int
    model_calls: int = 0
    tool_calls: int = 0

    def consume_model(self) -> None:
        self.model_calls += 1
        if self.model_calls > self.max_model_calls:
            raise AgentRunLimitExceeded("model call budget exceeded")

    def consume_tool(self) -> None:
        self.tool_calls += 1                                            # 工具调用计数器+1
        if self.tool_calls > self.max_tool_calls:                       # 如果工具调用计数器超过最大工具调用数
            logger.debug("tool call budget exceeded")
            raise AgentRunLimitExceeded("tool call budget exceeded")    # 抛出超过最大调用次数异常


class RunBudgetCallbackHandler(BaseCallbackHandler):
    """在每次聊天模型调用开始前扣减模型预算。"""

    raise_error = True
    run_inline = True

    def __init__(self, budget: RunBudget) -> None:
        self.budget = budget

    def on_chat_model_start(self, serialized, messages, **kwargs) -> None:  # type: ignore[no-untyped-def]
        self.budget.consume_model()

    def on_llm_start(self, serialized, prompts, **kwargs) -> None:  # type: ignore[no-untyped-def]
        self.budget.consume_model()


@dataclass(slots=True)
class AgentToolTrace:
    """保存工具链路状态，并向 SSE 层推送非敏感工具事件。"""

    request_id: str
    thread_id: str
    user_id: str | None
    cancellation_event: asyncio.Event
    event_sink: Callable[[dict[str, Any]], None] | None = None
    normalized_query: str | None = None
    intent: Any = None
    query_context: Any = None
    retrieved_context: Any = None
    recalled_memories: list[dict[str, Any]] = field(default_factory=list)
    budget: RunBudget | None = None
    tool_events: list[dict[str, Any]] = field(default_factory=list)
    _signatures: defaultdict[str, int] = field(default_factory=lambda: defaultdict(int)) #工具调用签名计数器
    _cache: dict[str, Any] = field(default_factory=dict)
    unavailable: bool = False
    unavailable_message: str | None = None
    unavailable_tool_name: str | None = None

    # 本地存档（self.tool_events.append）— 把每条事件追加到 trace 的 tool_events 列表里，
    # 形成这次 Agent 运行的完整工具调用链路记录，
    # 可用于事后回溯、调试或返回给调用方。
    def emit(self, event: str, data: dict[str, Any]) -> None:
        payload = {"event": event, "data": data}
        self.tool_events.append(payload)
        if self.event_sink is not None:
            self.event_sink(payload)

    # 工具调用签名计数器，用于防止工具调用重复执行。
    def signature_count(self, signature: str) -> int:
        return self._signatures[signature]
    
    # 工具调用签名计数器，用于防止工具调用重复执行。
    def record_signature(self, signature: str) -> None:
        self._signatures[signature] += 1

    # 获取缓存工具结果，用于防止工具调用重复执行。
    def cached(self, signature: str) -> Any:
        return self._cache.get(signature)
    # 添加
    def cache(self, signature: str, value: Any) -> None:
        self._cache[signature] = value

# 工具执行包装器
class ToolBoundary:
    """统一处理工具预算、取消、超时、重试、错误分类和工具事件。"""

    def __init__(
        self,
        trace: AgentToolTrace,              # 工具链路状态
        *,
        max_retries: int,                   # 最大重试次数
        retry_initial_delay: float,         # 初始重试延迟
        retry_max_delay: float,             # 最大重试延迟
        max_result_chars: int,              # 最大结果字符数
    ) -> None:
        self.trace = trace
        self.max_retries = max(0, max_retries)
        self.retry_initial_delay = max(0.0, retry_initial_delay)
        self.retry_max_delay = max(self.retry_initial_delay, retry_max_delay)
        self.max_result_chars = max(256, max_result_chars)

    async def run(
        self,
        tool_name: str,
        operation: Callable[[], Awaitable[Any]],
        *,
        timeout: float,
        signature_data: Any,
        retryable: Callable[[Exception], bool] | None = None,
        model_call: bool = False,
    ) -> Any:
        signature = self._signature(tool_name, signature_data)

        if self.trace.cancellation_event.is_set(): # 检查是否取消事件被触发
            raise asyncio.CancelledError           # 取消事件被触发，抛出异常

        cached = self.trace.cached(signature)      # 获取缓存工具结果
        if cached is not None:
            self.trace.emit(                        # 工具链路状态emit事件
                "tool.completed",
                self._event_data(tool_name, "success", 0, cached=True),
            )
            return cached

        self.trace.record_signature(signature)      # 工具调用签名计数器+1
        if self.trace.signature_count(signature) > 2:   # 工具调用签名计数器超过2次
            logger.debug(f"repeated tool call: {tool_name}")
            raise AgentRunLimitExceeded(f"repeated tool call: {tool_name}") # 抛出重复工具调用异常
        if self.trace.budget is not None:               # 预算不为空
            self.trace.budget.consume_tool()            # 消费工具预算

        tool_call_id = str(uuid4())                     # 生成工具调用ID
        self.trace.emit(                                # 发送工具调用开始事件
            "tool.started",
            self._event_data(tool_name, "started", 0, tool_call_id=tool_call_id),
        )

        attempts = self.max_retries + 1                 # 最大重试次数+1
        started = monotonic()                           # 记录开始时间
        logger.debug(f"tool call: {tool_name}--attempts: {attempts}")
        for attempt in range(1, attempts + 1):          # 循环尝试次数
            if self.trace.cancellation_event.is_set():  #g工具执行前检查是否取消事件被触发
                raise asyncio.CancelledError
            try:
                if model_call and self.trace.budget is not None:                # 如果是模型调用且预算不为空
                    self.trace.budget.consume_model()                           # 消费模型预算
                value = await asyncio.wait_for(operation(), timeout=timeout)    # 等待工具执行完成或超时， operation()是工具执行函数
                value = self._limit_result(value)                               # 限制结果长度
                self.trace.cache(signature, value)                              # 缓存工具结果
                self.trace.emit(                                                # 发送工具调用完成事件
                    "tool.completed",
                    self._event_data(
                        tool_name,
                        "success",
                        attempt,
                        tool_call_id=tool_call_id,
                        duration_ms=self._duration_ms(started),
                    ),
                )
                logger.debug(f"tool call: {tool_name}--success:{self._event_data(
                                        tool_name,
                                        "success",
                                        attempt,
                                        tool_call_id=tool_call_id,
                                        duration_ms=self._duration_ms(started),
                                    )}")
                return value
            except asyncio.CancelledError:
                self.trace.emit(                                                # 发送工具调用失败事件
                    "tool.failed",
                    self._event_data(
                        tool_name,
                        "cancelled",
                        attempt,
                        tool_call_id=tool_call_id,
                    ),
                )
                raise
            except asyncio.TimeoutError as exc:
                error = exc
                status = "timeout"
            except AgentRunLimitExceeded:
                raise
            except Exception as exc:  # noqa: BLE001
                error = exc
                status = "transient_error" if self._is_retryable(exc, retryable) else "internal_error"

            # 工具调用失败，检查是否可以重试
            can_retry = attempt < attempts and self._is_retryable(error, retryable)
            if can_retry:                                                   # 可以重试
                # 这段是**指数退避 + 上限封顶（后面还紧跟一行抖动）**的经典重试等待算法
                delay = min(                                                #
                    self.retry_max_delay,
                    self.retry_initial_delay * (2 ** (attempt - 1)),        # 指数退避
                )
                delay *= 0.8 + random.random() * 0.4                        # 抖动，防止重试请求过于集中
                await asyncio.sleep(delay)                                  # 等待延迟时间
                continue

            # 只有知识检索失败才会影响最终事实回答；长期记忆失败可以降级为空。
            if tool_name == "retrieve_knowledge":
                self.trace.unavailable = True
                self.trace.unavailable_tool_name = tool_name
                self.trace.unavailable_message = self._safe_message(tool_name, status)

            self.trace.emit(                                                # 发送工具调用失败事件
                "tool.failed",
                self._event_data(
                    tool_name,
                    status,
                    attempt,
                    tool_call_id=tool_call_id,
                    duration_ms=self._duration_ms(started),
                    retryable=False,
                ),
            )
            return ToolResult(
                ok=False,
                status=status,
                message=self._safe_message(tool_name, status),
                retryable=False,
                request_id=self.trace.request_id,
                tool_call_id=tool_call_id,
            ).model_dump()                                          # 返回工具调用失败结果

        raise RuntimeError("unreachable tool boundary state")


    # 限制工具结果长度
    def _limit_result(self, value: Any) -> Any:
        # 工具内部需要继续读取 Pydantic 对象；只限制最终传回模型的文本/集合。
        logger.debug(f"limiting result: {value}")
        if isinstance(value, BaseModel):
            return value
        if isinstance(value, str):
            return value[: self.max_result_chars]               # 限制字符串长度
        if isinstance(value, list):
            result = []
            used = 0
            for item in value:
                encoded = json.dumps(item, ensure_ascii=False, default=str) # 序列化字符串
                if used + len(encoded) > self.max_result_chars:              # 限制总长度
                    break
                result.append(item)
                used += len(encoded)
            return result

        # 默认返回 JSON 序列化后的字符串
        encoded = json.dumps(value, ensure_ascii=False, default=str)
        if len(encoded) <= self.max_result_chars:
            return value
        return encoded[: self.max_result_chars]


    # 工具调用签名，用于缓存和去重。以工具名称和参数 JSON 序列化后拼接。
    @staticmethod
    def _signature(tool_name: str, data: Any) -> str:
        return f"{tool_name}:{json.dumps(data, ensure_ascii=False, sort_keys=True, default=str)}"

    @staticmethod
    def _is_retryable(error: Exception, predicate: Callable[[Exception], bool] | None) -> bool:
        if isinstance(error, asyncio.TimeoutError):                     # 超时错误      
            return True
        return predicate(error) if predicate is not None else False     # 自定义重试条件


    # 返回工具调用失败的安全信息
    @staticmethod
    def _safe_message(tool_name: str, status: str) -> str:
        if status == "timeout":
            return f"{tool_name} timed out"
        if status == "transient_error":
            return f"{tool_name} is temporarily unavailable"
        return f"{tool_name} failed"

    @staticmethod
    def _duration_ms(started: float) -> int:
        return int((monotonic() - started) * 1000)

    def _event_data(
        self,
        tool_name: str,
        status: str,
        attempt: int,
        *,
        tool_call_id: str | None = None,
        duration_ms: int | None = None,
        retryable: bool | None = None,
        cached: bool = False,
    ) -> dict[str, Any]:
        data: dict[str, Any] = {
            "tool_name": tool_name,
            "status": status,
            "attempt": attempt,
            "request_id": self.trace.request_id,
            "cached": cached,
        }
        if tool_call_id:
            data["tool_call_id"] = tool_call_id
        if duration_ms is not None:
            data["duration_ms"] = duration_ms
        if retryable is not None:
            data["retryable"] = retryable
        return data
