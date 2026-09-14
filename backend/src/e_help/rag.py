from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import logging
from threading import Lock
from uuid import uuid4

from openai import APIConnectionError
from pymilvus.exceptions import MilvusException
from starlette.concurrency import run_in_threadpool

from .chains import AnswerChain, IntentChain, NormalizeChain, RagChain, RewriteChain
from .config import Settings
from .knowledge import load_document, split_document
from .llm import init_chat_model, init_embeddings
from .milvus_store import MilvusStore
from .models import ChatMessage, IntentResult, QueryContext, RetrievedContext
from .prompts.answer import SAFETY_BLOCK_MESSAGE, TOOL_UNAVAILABLE_MESSAGE
from .retrieval import MilvusRetriever
from .router import route_query

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class ThreadState:
    thread_id: str
    created_at: datetime
    messages: list[ChatMessage] = field(default_factory=list)


@dataclass(slots=True)
class RAGResult:
    thread_id: str
    answer: str
    query_context: QueryContext
    intent: IntentResult
    retrieved_context: RetrievedContext | None = None


class RAGService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.threads: dict[str, ThreadState] = {}
        self.lock = Lock()
        self.index_ready = False
        self.index_error: str | None = None

        self.embeddings = init_embeddings(
            model=settings.embed_model,
            provider="openai",
            api_key=settings.close_api_key,
            base_url=settings.resolved_embedding_base_url(),
            dimensions=settings.embedding_dimension,
        )
        self.chat_model = init_chat_model(
            model=settings.chat_model,
            model_provider="openai",
            api_key=settings.close_api_key,
            base_url=settings.resolved_chat_base_url(),
        )

        self.normalize_chain = NormalizeChain(self.chat_model)
        self.intent_chain = IntentChain(
            self.chat_model,
            confidence_threshold=settings.intent_confidence_threshold,
        )
        self.rewrite_chain = RewriteChain(
            self.chat_model,
            history_turns=settings.rewrite_history_turns,
        )
        self.answer_chain = AnswerChain(self.chat_model)

        self.store: MilvusStore | None = None
        self.retriever: MilvusRetriever | None = None
        self.rag_chain: RagChain | None = None
        # 启动只连接并加载已有 collection，不自动执行 knowledge.txt 重建。
        # Milvus 暂时不可用时保留服务进程，由健康检查暴露降级原因。
        try:
            self._connect_store()
            self.index_ready = True
        except Exception as exc:  # noqa: BLE001
            self.index_ready = False
            self.index_error = self._format_error(exc)
            logger.warning("Milvus initialization skipped: %s", self.index_error)

    def create_thread(self) -> ThreadState:
        thread = ThreadState(thread_id=str(uuid4()), created_at=datetime.utcnow())
        with self.lock:
            self.threads[thread.thread_id] = thread
        return thread

    def get_or_create_thread(self, thread_id: str | None) -> ThreadState:
        if thread_id:
            with self.lock:
                existing = self.threads.get(thread_id)
            if existing is not None:
                return existing
        return self.create_thread()

    def build_query_context(
        self,
        query: str,
        history: list[ChatMessage],
    ) -> tuple[QueryContext, IntentResult]:
        normalized = self.normalize_chain.invoke(query).normalized_query        # 通过模型，规范化问题
        safety, intent = self.intent_chain.invoke(normalized)                   # 安全和意图                  
        context: QueryContext = {
            "original_query": query,                                            # 保留原始提问
            "normalized_query": normalized,                                     # 规范化后的问题
            "contextualized_query": normalized,                                 #
            "intent": intent.intent,                                            # 意图
            "entities": intent.entities.model_dump(exclude_none=True),          #
            "need_retrieval": intent.need_retrieval,                            #
            "safety_status": safety.status,                                     #
            "safety_reason": safety.reason,                                     # 
        }

        if safety.status == "allow" and intent.intent == "rag":
            rewrite = self.rewrite_chain.invoke(normalized, history)            # 重写问题
            context["contextualized_query"] = rewrite.contextualized_query      # 重写后的问题
            context["need_retrieval"] = rewrite.should_retrieve                 # 是否需要检索
        return context, intent

    def route_query(self, context: QueryContext) -> str:
        return route_query(context)

    def answer(self, thread_id: str | None, message: str) -> RAGResult:
        thread = self.get_or_create_thread(thread_id)                       # 获取对话的id
        history = list(thread.messages)                                     # 对话的历史消息
        thread.messages.append(ChatMessage(role="user", content=message))   # 将用户提问加入到对话的历史消息，这个和checkpoint有什么区别？

        context, intent = self.build_query_context(message, history)        # query 预处理 normallize
        route = self.route_query(context)                                   # 路由

        retrieved: RetrievedContext | None = None
        if route == "blocked":
            answer = SAFETY_BLOCK_MESSAGE
        elif route == "chitchat":                                           # 闲聊对话
            answer = self.answer_chain.answer_chitchat(context["normalized_query"])
        elif route == "tool":
            answer = TOOL_UNAVAILABLE_MESSAGE
        elif not context["need_retrieval"]:
            answer = "我不知道"
        elif self.rag_chain is None:
            answer = "我不知道"
        else:
            retrieved = self.rag_chain.invoke(context["contextualized_query"])      # 检索上下文
            if not retrieved.chunks:
                answer = "我不知道"
            else:
                print(f"chat : {context['contextualized_query']}, retrieved: {retrieved}")
                answer = self.answer_chain.answer_rag(context, retrieved)           # 检索后回答问题

        thread.messages.append(ChatMessage(role="assistant", content=answer))
        return RAGResult(
            thread_id=thread.thread_id,
            answer=answer,
            query_context=context,
            intent=intent,
            retrieved_context=retrieved,
        )

    def refresh_index(self) -> int:
        try:
            count = self.reindex()
            self.index_ready = True
            self.index_error = None
            return count
        except Exception as exc:  # noqa: BLE001
            self.index_ready = False
            self.index_error = self._format_error(exc)
            logger.exception("knowledge reindex failed")
            return 0

    def reindex(self) -> int:
        if self.store is None:
            self._connect_store()
        assert self.store is not None
        document = load_document(self.settings.knowledge_path)
        chunks = split_document(
            document,
            chunk_size=self.settings.chunk_size,
            chunk_overlap=self.settings.chunk_overlap,
        )
        self.store.drop_collection()
        self.store.ensure_collection()
        self.store.set_embeddings(self.embeddings)
        self.store.upsert_chunks(chunks)
        return len(chunks)

    async def stream_answer(self, thread_id: str | None, message: str):
        # run_in_threadpool 时Starlette的工具函数，
        # 作用： 在async协程（事件循环）里面，安全同步阻塞函数，把它丢进县城池跑，不会卡死事件循环
        result = await run_in_threadpool(self.answer, thread_id, message)
        context = result.query_context
        yield {
            "event": "intent.completed",
            "data": {
                "intent": context["intent"],
                "need_retrieval": context["need_retrieval"],
                "safety_status": context["safety_status"],
            },
        }
        if context["intent"] == "rag" and context["safety_status"] == "allow":
            yield {
                "event": "rewrite.completed",
                "data": {"query": context["contextualized_query"]},
            }
        if result.retrieved_context is not None:
            yield {
                "event": "retrieval.completed",
                "data": result.retrieved_context.model_dump(),
            }
        yield {
            "event": "message.delta",
            "data": {"content": result.answer},
        }
        yield {
            "event": "message.final",
            "data": {
                "content": result.answer,
                "thread_id": result.thread_id,
            },
        }

    def _connect_store(self) -> None:
        store = MilvusStore(
            uri=self.settings.resolved_milvus_uri(),
            db_name=self.settings.milvus_db_name,
            collection_name=self.settings.milvus_collection_name,
            dimension=self.settings.embedding_dimension,
        )
        store.set_embeddings(self.embeddings)
        store.ensure_collection()
        self.store = store
        self.retriever = MilvusRetriever(
            store=store,
            embeddings=self.embeddings,
            top_k=self.settings.top_k,
        )
        self.rag_chain = RagChain(self.retriever)

    def _format_error(self, exc: Exception) -> str:
        if isinstance(exc, APIConnectionError):
            return (
                "Embedding or Chat API connection failed. "
                f"Check CLOSE_API_BASE_URL={self.settings.resolved_embedding_base_url()}."
            )
        if isinstance(exc, MilvusException):
            return (
                "Milvus connection failed. "
                f"Check MILVUS_URI={self.settings.resolved_milvus_uri()}."
            )
        return str(exc) or exc.__class__.__name__
