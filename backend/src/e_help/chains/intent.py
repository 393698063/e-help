from __future__ import annotations

import re

from langchain_core.prompts import ChatPromptTemplate

from ..models import IntentResult, NormalizeResult, SafetyResult
from ..prompts import INTENT_SYSTEM_PROMPT, NORMALIZE_SYSTEM_PROMPT, SAFETY_SYSTEM_PROMPT


class NormalizeChain:
    def __init__(self, model) -> None:
        self._chain = (
            ChatPromptTemplate.from_messages(
                [
                    ("system", NORMALIZE_SYSTEM_PROMPT),
                    ("human", "用户输入：\n{query}"),
                ]
            )
            | model.with_structured_output(NormalizeResult)
        )

    def invoke(self, query: str) -> NormalizeResult:
        try:
            result = self._chain.invoke({"query": query})
            return result if isinstance(result, NormalizeResult) else NormalizeResult.model_validate(result)
        except Exception:
            normalized = re.sub(r"[ \t]+", " ", query.strip())
            return NormalizeResult(normalized_query=normalized or query.strip())


class IntentChain:
    def __init__(self, model, confidence_threshold: float = 0.65) -> None:
        self.confidence_threshold = confidence_threshold
        self._safety_chain = (
            ChatPromptTemplate.from_messages(
                [
                    ("system", SAFETY_SYSTEM_PROMPT),
                    ("human", "用户请求：\n{query}"),
                ]
            )
            | model.with_structured_output(SafetyResult)                # 输出格式化
        )
        self._intent_chain = (
            ChatPromptTemplate.from_messages(
                [
                    ("system", INTENT_SYSTEM_PROMPT),
                    ("human", "规范化问题：\n{query}"),
                ]
            )
            | model.with_structured_output(IntentResult)
        )

    def invoke(self, normalized_query: str) -> tuple[SafetyResult, IntentResult]:
        safety = self._invoke_safety(normalized_query)                  # 安全检查
        if safety.status == "block":
            return safety, IntentResult(
                intent="rag",
                entities={},
                need_retrieval=False,
                confidence=1.0,
                reason="安全检查已阻断请求",
            )

        intent = self._invoke_intent(normalized_query)                  # 意图分析
        if intent.confidence < self.confidence_threshold:               # 为什么时小于？
            intent = intent.model_copy(update={"intent": "rag", "need_retrieval": True})
        return safety, intent

    def _invoke_safety(self, query: str) -> SafetyResult:
        try:
            result = self._safety_chain.invoke({"query": query})
            return result if isinstance(result, SafetyResult) else SafetyResult.model_validate(result)
        except Exception:
            lowered = query.lower()
            risky_markers = ("api key", "密钥", "系统提示词", "执行命令", "删除数据库")
            if any(marker in lowered for marker in risky_markers):
                return SafetyResult(status="block", reason="请求包含潜在的未授权操作")
            return SafetyResult(status="allow", reason="普通客服请求")

    def _invoke_intent(self, query: str) -> IntentResult:
        try:
            result = self._intent_chain.invoke({"query": query})
            return result if isinstance(result, IntentResult) else IntentResult.model_validate(result)
        except Exception:
            return self._heuristic_intent(query)

    def _heuristic_intent(self, query: str) -> IntentResult:
        lowered = query.lower()
        if any(word in query for word in ("你好", "您好", "嗨", "谢谢", "感谢")) and len(query) <= 20:
            return IntentResult(
                intent="chitchat",
                entities={},
                need_retrieval=False,
                confidence=0.8,
                reason="简单寒暄或感谢",
            )
        if any(word in query for word in ("查询订单", "修改订单", "执行", "调用接口", "删除数据")):
            return IntentResult(
                intent="tool",
                entities={},
                need_retrieval=False,
                confidence=0.8,
                reason="请求需要外部系统或执行动作",
            )
        return IntentResult(
            intent="rag",
            entities={},
            need_retrieval=True,
            confidence=0.66 if lowered else 0.0,
            reason="默认按知识库问题处理",
        )
