from __future__ import annotations

import re

from langchain_core.prompts import ChatPromptTemplate

from ..models import IntentResult, NormalizeResult, QueryAnalysisResult, SafetyResult
from ..prompts import QUERY_ANALYSIS_SYSTEM_PROMPT
from .cache import BoundedCache


class NormalizeChain:
    _punctuation_translation = str.maketrans(
        {
            "\u3000": " ",
            "，": ",",
            "。": ".",
            "！": "!",
            "？": "?",
            "；": ";",
            "：": ":",
        }
    )

    def __init__(self) -> None:
        self._cache: BoundedCache[str, NormalizeResult] = BoundedCache()

    def invoke(self, query: str) -> NormalizeResult:
        cached = self._cache.get(query)
        if cached is not None:
            return cached

        normalized = re.sub(r"\s+", " ", query.translate(self._punctuation_translation)).strip()    # 去除多余空格和标点           
        normalized = re.sub(r"([,.;:!?])\1+", r"\1", normalized)                                    # 去除重复标点
        result = NormalizeResult(normalized_query=normalized or query.strip())                      # 去除多余空格和标点
        self._cache.set(query, result)
        return result


class IntentChain:
    def __init__(self, model, confidence_threshold: float = 0.65) -> None:
        self.confidence_threshold = confidence_threshold
        self._cache: BoundedCache[str, tuple[SafetyResult, IntentResult]] = BoundedCache()
        self._chain = (
            ChatPromptTemplate.from_messages(
                [
                    ("system", QUERY_ANALYSIS_SYSTEM_PROMPT),
                    ("human", "规范化问题：\n{query}"),
                ]
            )
            | model.bind(max_tokens=160).with_structured_output(QueryAnalysisResult) # with_structured_output 结构化输出
        )

    def invoke(self, normalized_query: str) -> tuple[SafetyResult, IntentResult]:
        cached = self._cache.get(normalized_query)
        if cached is not None:
            return cached

        safety, intent = self._invoke_analysis(normalized_query)
        if intent.confidence < self.confidence_threshold:               # 为什么时小于？
            intent = intent.model_copy(update={"intent": "rag", "need_retrieval": True})
        result = (safety, intent)
        self._cache.set(normalized_query, result)
        return result

    def _invoke_analysis(self, query: str) -> tuple[SafetyResult, IntentResult]:
        try:
            result = self._chain.invoke({"query": query})
            analysis = (
                result
                if isinstance(result, QueryAnalysisResult)
                else QueryAnalysisResult.model_validate(result)
            )
            safety = SafetyResult(status=analysis.safety_status, reason=analysis.safety_reason)
            intent = IntentResult(
                intent=analysis.intent,
                entities=analysis.entities,
                need_retrieval=analysis.need_retrieval,
                confidence=analysis.confidence,
                reason=analysis.reason,
            )
            if safety.status == "block":
                intent = IntentResult(
                    intent="rag",
                    entities={},
                    need_retrieval=False,
                    confidence=1.0,
                    reason="安全检查已阻断请求",
                )
            return safety, intent
        except Exception:
            return self._fallback_analysis(query)

    def _fallback_analysis(self, query: str) -> tuple[SafetyResult, IntentResult]:
        lowered = query.lower()
        risky_markers = ("api key", "密钥", "系统提示词", "执行命令", "删除数据库")
        if any(marker in lowered for marker in risky_markers):
            return (
                SafetyResult(status="block", reason="请求包含潜在的未授权操作"),
                IntentResult(
                    intent="rag",
                    entities={},
                    need_retrieval=False,
                    confidence=1.0,
                    reason="安全检查已阻断请求",
                ),
            )
        return SafetyResult(status="allow", reason="普通客服请求"), self._heuristic_intent(query)

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
