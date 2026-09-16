from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import datetime
from threading import Lock
from uuid import uuid4

from langchain_core.messages import AIMessage
from openai import APIConnectionError
from pymilvus.exceptions import MilvusException
from starlette.concurrency import run_in_threadpool

from .agent import AgentRunLimitExceeded, MemoryManager, RunBudget, build_agent, build_tools
from .agent.runtime import AgentToolTrace, RunBudgetCallbackHandler
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
    user_id: str | None = None
    messages: list[ChatMessage] = field(default_factory=list)


@dataclass(slots=True)
class RAGResult:
    thread_id: str
    answer: str
    query_context: QueryContext
    intent: IntentResult
    retrieved_context: RetrievedContext | None = None


@dataclass(slots=True)
class RequestRecord:
    fingerprint: str
    done: asyncio.Event = field(default_factory=asyncio.Event)
    result: RAGResult | None = None
    error: Exception | None = None


class RAGService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.threads: dict[str, ThreadState] = {}
        self.lock = Lock()
        self.request_records: dict[str, RequestRecord] = {}
        self.request_threads: dict[tuple[str | None, str], str] = {}
        self.request_lock = asyncio.Lock()                              #协程锁，用于保护 request_records 和 request_threads 的并发访问
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

        self.normalize_chain = NormalizeChain()
        self.intent_chain = IntentChain(
            self.chat_model,
            confidence_threshold=settings.intent_confidence_threshold,
        )
        self.rewrite_chain = RewriteChain(
            self.chat_model,
            history_turns=settings.rewrite_history_turns,
        )
        self.answer_chain = AnswerChain(self.chat_model)
        self.memory = MemoryManager(settings, self.chat_model)

        self.store: MilvusStore | None = None
        self.retriever: MilvusRetriever | None = None
        self.rag_chain: RagChain | None = None
        self._memory_tasks: set[asyncio.Task] = set()

        # 启动只连接已有 collection，不自动重建知识库。
        try:
            self._connect_store()
            self.index_ready = True
        except Exception as exc:  # noqa: BLE001
            self.index_error = self._format_error(exc)
            logger.warning("Milvus initialization skipped: %s", self.index_error)

    async def setup(self) -> None:
        await self.memory.setup()

    async def close(self) -> None:
        if self._memory_tasks:
            await asyncio.gather(*self._memory_tasks, return_exceptions=True)
        await self.memory.close()

    def create_thread(
        self,
        user_id: str | None = None,
        request_id: str | None = None,
    ) -> ThreadState:
        if request_id:
            key = (user_id, request_id)
            with self.lock:
                existing_thread_id = self.request_threads.get(key)
                if existing_thread_id:
                    return self.threads[existing_thread_id]
        thread = ThreadState(
            thread_id=str(uuid4()),
            created_at=datetime.utcnow(),
            user_id=user_id,
        )
        with self.lock:
            self.threads[thread.thread_id] = thread
            if request_id:
                self.request_threads[(user_id, request_id)] = thread.thread_id
        return thread

    def get_or_create_thread(
        self,
        thread_id: str | None,
        user_id: str | None = None,
    ) -> ThreadState:
        if not thread_id:
            return self.create_thread(user_id)

        with self.lock:
            existing = self.threads.get(thread_id)
            if existing is not None:
                if existing.user_id and user_id and existing.user_id != user_id:
                    raise PermissionError("thread does not belong to user")
                if existing.user_id is None:
                    existing.user_id = user_id
                return existing

            # Checkpointer 可以恢复未知 thread；本地状态只负责轻量归属校验。
            thread = ThreadState(
                thread_id=thread_id,
                created_at=datetime.utcnow(),
                user_id=user_id,
            )
            self.threads[thread_id] = thread
            return thread

    def build_query_context(
        self,
        query: str,
        history: list[ChatMessage],
    ) -> tuple[QueryContext, IntentResult]:
        normalized = self.normalize_chain.invoke(query).normalized_query
        safety, intent = self.intent_chain.invoke(normalized)
        context: QueryContext = {
            "original_query": query,
            "normalized_query": normalized,
            "contextualized_query": normalized,
            "intent": intent.intent,
            "entities": intent.entities.model_dump(exclude_none=True),
            "need_retrieval": intent.need_retrieval,
            "safety_status": safety.status,
            "safety_reason": safety.reason,
        }
        if safety.status == "allow" and intent.intent == "rag":
            rewrite = self.rewrite_chain.invoke(normalized, history)
            context["contextualized_query"] = rewrite.contextualized_query
            context["need_retrieval"] = rewrite.should_retrieve
        return context, intent

    def route_query(self, context: QueryContext) -> str:
        return route_query(context)

    async def answer(
        self,
        thread_id: str | None,
        message: str,
        user_id: str | None = None,
        request_id: str | None = None,
        trace: AgentToolTrace | None = None,
    ) -> RAGResult:
        request_id = request_id or str(uuid4())
        thread = self.get_or_create_thread(thread_id, user_id)
        trace = trace or AgentToolTrace(
            request_id=request_id,
            thread_id=thread.thread_id,
            user_id=user_id,
            cancellation_event=asyncio.Event(),
        )
        record, owner = await self._reserve_request( #预占请求记录
            request_id,
            user_id=user_id,
            thread_id=thread.thread_id,
            message=message,
        )
        #这段是请求幂等（去重）机制里"非拥有者"分支的处理——当同一个 request_id 被并发重复请求时，只让第一个真正跑 Agent，其余的都在这里"搭便车"等结果
        if not owner:                       # 我不是拥有者 → 别人正在/已经处理这个 request_id
            await record.done.wait()        # ① 阻塞等待，直到拥有者把活干完
            if record.error is not None:    # ② 拥有者是失败结束的
                raise record.error          #    → 把同一个异常抛给我,行为和拥有者一致
            if record.result is None:       # ③ 完成了但结果为空 → 不该发生的状态
                raise RuntimeError("idempotent request completed without result")
            return record.result            # ④ 正常:直接复用拥有者算好的结果

        try:
            budget = RunBudget(
                max_model_calls=self.settings.agent_max_model_calls,
                max_tool_calls=self.settings.agent_max_tool_calls,
            )
            logger.debug("Starting request_id=%s", request_id, "RunBudget=%s", budget)
            trace.budget = budget

            async def recall_user_memory() -> list[dict]:
                return await self.memory.recall(user_id)

            tools = build_tools(
                normalize_chain=self.normalize_chain,
                intent_chain=self.intent_chain,
                rewrite_chain=self.rewrite_chain,
                rag_chain=self.rag_chain,
                trace=trace,
                recall_memory=recall_user_memory,
                settings=self.settings,
            )
            agent = build_agent(
                model=self.chat_model,
                tools=tools,
                checkpointer=self.memory.checkpointer,
                store=self.memory.store,
            )
            config = {
                "recursion_limit": self.settings.agent_recursion_limit,
                "callbacks": [RunBudgetCallbackHandler(budget)],
                "configurable": {
                    "thread_id": thread.thread_id,
                    "user_id": user_id or "anonymous",
                },
            }

            result = await asyncio.wait_for(
                agent.ainvoke(
                    {"messages": [{"role": "user", "content": message}]},
                    config=config,
                ),
                timeout=self.settings.agent_timeout_seconds,
            )
            logger.info(
                "Agent completed request_id=%s model_calls=%s tool_calls=%s",
                request_id,
                budget.model_calls,
                budget.tool_calls,
            )
            agent_answer = self._extract_answer(result)

            if trace.query_context is None or trace.intent is None:
                context, intent = await asyncio.wait_for(
                    run_in_threadpool(
                        self.build_query_context,
                        message,
                        [],
                    ),
                    timeout=self.settings.tool_model_timeout_seconds * 2,
                )
            else:
                context = trace.query_context
                context["original_query"] = message
                intent = trace.intent

            route = self.route_query(context)
            retrieved: RetrievedContext | None = None
            if route == "blocked":
                answer = SAFETY_BLOCK_MESSAGE
            elif route == "chitchat":
                answer = agent_answer or await asyncio.wait_for(
                    run_in_threadpool(
                        self.answer_chain.answer_chitchat,
                        context["normalized_query"],
                    ),
                    timeout=self.settings.tool_model_timeout_seconds,
                )
            elif route == "tool":
                answer = TOOL_UNAVAILABLE_MESSAGE
            elif trace.unavailable:
                answer = "当前相关服务暂不可用，请稍后重试。"
            elif not context["need_retrieval"]:
                answer = "我不知道"
            elif self.rag_chain is None:
                answer = "当前知识库服务暂不可用，请稍后重试。"
            else:
                retrieved = trace.retrieved_context
                if retrieved is None:
                    retrieved = await run_in_threadpool(
                        self.rag_chain.invoke,
                        context["contextualized_query"],
                    )
                if not retrieved.chunks:
                    answer = "我不知道"
                else:
                    answer = agent_answer or await asyncio.wait_for(
                        run_in_threadpool(
                            self.answer_chain.answer_rag,
                            context,
                            retrieved,
                        ),
                        timeout=self.settings.tool_model_timeout_seconds,
                    )

            memory_task = asyncio.create_task(
                self.memory.extract_and_save(
                    user_id=user_id,
                    user_message=message,
                    assistant_message=answer,
                )
            )
            self._memory_tasks.add(memory_task)
            memory_task.add_done_callback(self._memory_tasks.discard)
            final_result = RAGResult(
                thread_id=thread.thread_id,
                answer=answer,
                query_context=context,
                intent=intent,
                retrieved_context=retrieved,
            )
            await self._complete_request(request_id, result=final_result)
            return final_result
        except asyncio.CancelledError as exc:
            await self._complete_request(
                request_id,
                error=RuntimeError("request cancelled"),
            )
            raise exc
        except Exception as exc:
            # A timed-out checkpoint query can invalidate the old connection.
            # Rebuild the pool so the next request does not inherit
            # "connection is closed".
            if self.memory.is_connection_error(exc):
                try:
                    await asyncio.wait_for(
                        self.memory.reconnect(),
                        timeout=self.settings.db_reconnect_timeout_seconds,
                    )
                except Exception:  # noqa: BLE001
                    logger.exception("PostgreSQL recovery after Agent failure failed")
            await self._complete_request(request_id, error=exc)
            raise

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

    async def stream_answer(
        self,
        thread_id: str | None,
        message: str,
        user_id: str | None = None,
        request_id: str | None = None,
    ):
        request_id = request_id or str(uuid4())
        thread = self.get_or_create_thread(thread_id, user_id)
        #是异步事件对象，用来让一个协程等待某个条件发生
        # 1.await event.wait()等待事件, 2. event.set()触发事件, 3. event.clear()清除事件状态, 4. event.is_set()检查事件是否被设置
        cancellation_event = asyncio.Event()                        # 用于取消任务
        event_queue: asyncio.Queue[dict] = asyncio.Queue()          # 用于异步传递事件

        trace = AgentToolTrace(
            request_id=request_id,
            thread_id=thread.thread_id,
            user_id=user_id,
            cancellation_event=cancellation_event,
            #这是 Python 里很常见的「把对象的方法当函数传递
            #payload = {"event": event, "data": data}
                    # if self.event_sink is not None:
                    #     self.event_sink(payload)
            event_sink=event_queue.put_nowait,                      # .put_nowait 是它的一个方法（非阻塞入队：队列没满就立刻放进去，满了抛 QueueFull；这里无界队列不会满         
        )

        # 异步任务，执行答案生成逻辑  answer_task（生产者）和 stream_answer 循环（消费者）并发跑，往里放/取不用自己加锁
        answer_task = asyncio.create_task(
            self.answer(
                thread.thread_id,
                message,
                user_id=user_id,
                request_id=request_id,
                trace=trace,
            )
        )
        try:
            # 工具边界会实时把非敏感事件放进队列，避免等到最终答案才通知前端。
            # answer_task.done(): 判断任务是否完成
            # event_queue.empty(): 判断队列是否为空
            while not answer_task.done() or not event_queue.empty():
                if event_queue.empty() and not answer_task.done(): #如果队列为空且任务未完成，则创建一个新任务来获取队列中的事件

                    #项目里生产端用 put_nowait（event_sink=event_queue.put_nowait，非阻塞入队），消费端用 get()。
                    # 为什么不直接写 event = await event_queue.get()？关键问题在于:
                    # await event_queue.get() 在队列空时会一直挂起等待。 
                    # 如果此时 answer_task 已经跑完、不会再往队列放任何事件，那这个 get() 就会永远等下去（挂死
                    #所以项目用了一个技巧:
                    # ① 把 event_queue.get() 包成一个 Task（get_event）。
                    # ② 用 asyncio.wait({get_event, answer_task}, return_when=FIRST_COMPLETED) 同时等两件事：要么队列来了新事件（get_event 完成），
                    # 要么 Agent 整个跑完了（answer_task 完成），谁先发生就先返回。
                    # ③ 如果是 get_event 先完成 → get_event.result() 就是取到的事件，yield 出去。
                    # ④ 如果是 answer_task 先完成（说明不会再有过程事件了）→ 主动 cancel() 那个还在傻等的 get()，并用 suppress(CancelledError) 吞掉取消异常，避免挂死。
                    # ⑤ 另一个分支：如果进循环时队列本来就有积压事件，就用 get_nowait() 直接同步取走，不必走上面等待的逻辑。
                    get_event = asyncio.create_task(event_queue.get()) # 创建任务来获取队列中的事件
                    # asyncio.wait 的语义是：监视一组 task，等到满足条件（这里是"第一个完成"）就返回。
                    # 它返回 (done, pending) 两个集合，告诉你"哪些完成了、哪些还没
                    done, _ = await asyncio.wait(
                        {get_event, answer_task},
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                   
                    if get_event in done:            # 说明队列里来新事件了，get_event（即 event_queue.get() 的 Task）已经拿到结果    
                        yield get_event.result()    # get_event.result() 取出这个 Task 的返回值——也就是从队列取到的那条事件 dict，直接 yield 给上层（server 的 SSE 生成器），实时推给前端
                    else:                           #进 else 意味着不是 get_event 先完成，而是 answer_task 先完成了（Agent 整个跑完）
                        #由于 Agent 已经结束、不会再往队列 emit 任何过程事件，这个 get_event 如果不管它，就会永远挂起 → 泄漏一个 Task。所以要清理
                        get_event.cancel()         # get_event.cancel() —— 向这个挂起的 Task 发取消请求。注意 cancel() 只是"请求"，不是立即杀死；被取消的 await event_queue.get() 会在下次调度时抛出 asyncio.CancelledError
                        with suppress(asyncio.CancelledError): # with suppress(asyncio.CancelledError) —— 这里的取消是我们主动发起的、预期内的行为，不是错误，所以用 contextlib.suppress 把这个异常安静吞掉，避免它向外传播中断整个循环
                            await get_event         # await get_event —— 必须 await 一下，才能让取消真正生效并回收这个 Task。但 被取消的 Task 在 await 时会把 CancelledError 重新抛出
                elif not event_queue.empty():               #队列里有货
                    yield event_queue.get_nowait()          #直接取队列里的第一条事件，不阻塞，没有就报错（队列空时 get_nowait() 会抛 asyncio.QueueEmpty）
                else:
                    break

            #await asyncio.wait(...) 和 await answer_task 是两件完全不同的事。
            # 前者只是"等状态"，后者才是"取结果"。 对同一个 task 用这两者不冲突，也不重复
            result = await answer_task
            context = result.query_context
#TODO: 这里是等任务都完成了才一步步推送消息，后续改成实时推送
            yield {
                "event": "intent.completed",
                "data": {
                    "intent": context["intent"],
                    "need_retrieval": context["need_retrieval"],
                    "safety_status": context["safety_status"],
                },
            }
            # 如果是 RAG 意图，允许继续执行，则 emit 重写事件
            if context["intent"] == "rag" and context["safety_status"] == "allow":
                yield {
                    "event": "rewrite.completed",
                    "data": {"query": context["contextualized_query"]},
                }
            if result.retrieved_context is not None:    #如果检索上下文不为空
                yield {
                    "event": "retrieval.completed",
                    "data": result.retrieved_context.model_dump(),
                }
            # 最终答案事件
            yield {"event": "message.delta", "data": {"content": result.answer}}
            # 最终消息事件
            yield {
                "event": "message.final",
                "data": {
                    "content": result.answer,
                    "thread_id": result.thread_id,
                    "request_id": request_id,
                },
            }
        finally:
            if not answer_task.done():
                cancellation_event.set()
                answer_task.cancel()
                with suppress(asyncio.CancelledError):
                    await answer_task

    async def _reserve_request( # 预占请求记录，用于请求去重与幂等控制
        self,
        request_id: str,
        *,
        user_id: str | None,
        thread_id: str,
        message: str,
    ) -> tuple[RequestRecord, bool]:
        """
        预占请求记录，用于请求去重与幂等控制。

        根据 user_id、thread_id 和 message 计算请求指纹。若 request_id 首次出现，
        则创建并登记新的请求记录；若已存在且指纹一致，则返回已有记录；若指纹不一致，
        说明同一 request_id 被用于不同的请求数据，抛出异常。

        Args:
            request_id: 请求的唯一标识。
            user_id: 用户 ID，可为 None。
            thread_id: 会话线程 ID。
            message: 请求的消息内容。

        Returns:
            tuple[RequestRecord, bool]: 二元组，第一个元素为对应的请求记录，
                第二个元素表示该记录是否为本次新建（True 为新建，False 为已存在）。

        Raises:
            ValueError: 当 request_id 已被使用且请求数据（指纹）不一致时抛出。
        """
        fingerprint = hashlib.sha256(
            json.dumps(
                {"user_id": user_id, "thread_id": thread_id, "message": message},
                ensure_ascii=False,
                sort_keys=True,
            ).encode()
        ).hexdigest()

        async with self.request_lock:
            record = self.request_records.get(request_id)
            if record is None:
                record = RequestRecord(fingerprint=fingerprint)
                self.request_records[request_id] = record
                return record, True
            if record.fingerprint != fingerprint:
                raise ValueError("request_id was already used with different request data")
            return record, False

    async def _complete_request(
        self,
        request_id: str,
        *,
        result: RAGResult | None = None,
        error: Exception | None = None,
    ) -> None:
        async with self.request_lock:
            record = self.request_records.get(request_id)
            if record is None:
                return
            record.result = result
            record.error = error
            record.done.set()
            # 幂等记录只需覆盖短期重试窗口，避免长时间运行进程无限增长。
            if len(self.request_records) > 2048:
                for key, candidate in self.request_records.items():
                    if candidate.done.is_set() and key != request_id:
                        self.request_records.pop(key, None)
                        break

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
            top_k=min(self.settings.top_k, self.settings.tool_max_retrieval_top_k),
        )
        self.rag_chain = RagChain(self.retriever)

    @staticmethod
    def _extract_answer(agent_result) -> str:
        messages = agent_result.get("messages", []) if isinstance(agent_result, dict) else []
        if not messages:
            return ""
        message = messages[-1]
        content = message.content if isinstance(message, AIMessage) else getattr(message, "content", "")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for part in content:
                if isinstance(part, dict) and part.get("type") == "text":
                    parts.append(str(part.get("text", "")))
                elif isinstance(part, str):
                    parts.append(part)
            return "".join(parts).strip()
        return str(content).strip()

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
        if isinstance(exc, AgentRunLimitExceeded):
            return "Agent execution budget exceeded."
        return str(exc) or exc.__class__.__name__
