from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from contextlib import suppress
from time import monotonic
from typing import AsyncGenerator

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from starlette.concurrency import run_in_threadpool

from .config import get_settings
from .agent.runtime import AgentRunLimitExceeded
from .logger import configure_logging, logger
from .models import ChatRequest, ReindexResponse, ThreadCreateRequest, ThreadCreateResponse
from .rag import RAGService


service: RAGService | None = None

# yield 之前：服务器启动时执行一次，做初始化工作（连接数据库、加载模型、预热缓存等）
# yield：服务器正常运行，处理请求
# yield 之后：服务器关闭时执行一次，做清理工作（关闭连接、释放资源等）

# @asynccontextmanager 异步上下文管理器，它将函数转化为所谓的“异步上下文管理器
@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    global service
    settings = get_settings()
    configure_logging(settings.log_level)
    service = RAGService(settings)
    app.state.service = service
    await service.setup()
    try:
        yield
    finally:
        await service.close()


app = FastAPI(title="e-help", lifespan=lifespan)
# CORSMiddleware 跨域中间件，允许所有来源的请求
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],            # 个允许跨域请求的源列表
    # allow_origin_regex - 一个正则表达式字符串，匹配的源允许跨域请求。例如 'https://.*\.example\.org'
    allow_credentials=False,         # 指示跨域请求支持 cookies。默认是 False
    allow_methods=["*"],            # 一个允许跨域请求的 HTTP 方法列表。默认为 ['GET']。你可以使用 ['*'] 来允许所有标准方法
    allow_headers=["*"],            # 个允许跨域请求的 HTTP 请求头列表。默认为 []。你可以使用 ['*'] 允许所有的请求头。Accept、Accept-Language、Content-Language 以及 Content-Type 这几个请求头在简单 CORS 请求中总是被允许
)

# 当前 RAGService 是一个共享状态的单进程服务。它已经具备基于 thread_id 和 user_id 的逻辑隔离，
# 但真正的安全隔离还依赖身份认证、持久化的线程归属校验，以及同一会话的并发控制
def get_service() -> RAGService:
    if service is None:
        raise RuntimeError("service not initialized")
    return service

# 健康检查api
@app.get("/api/health")
async def health():
    rag = get_service()
    return {
        "status": "ok",
        "index_ready": rag.index_ready,
        "index_error": rag.index_error,
    }

# 创建thread
@app.post("/api/threads", response_model=ThreadCreateResponse)  # response_model 指定返回的响应模型
async def create_thread(payload: ThreadCreateRequest | None = None):
    thread = get_service().create_thread(                       # 通过requestid和userid获取缓存的thread ID，如果没有则创建新的thread
        user_id=payload.user_id if payload else None,
        request_id=payload.request_id if payload else None,
    )
    return ThreadCreateResponse(thread_id=thread.thread_id, created_at=thread.created_at)

# 重建索引
@app.post("/api/knowledge/reindex", response_model=ReindexResponse)
async def reindex():
    rag = get_service()
    count = await run_in_threadpool(rag.refresh_index)
    if rag.index_ready:
        return ReindexResponse(status="ok", document_id="knowledge", chunks_indexed=count)
    return ReindexResponse(status=f"degraded: {rag.index_error}", document_id="knowledge", chunks_indexed=count)


# SSE 并不是 FastAPI 单独“建立一条特殊连接”的 API。

# 本质上是：客户端发起一个普通 HTTP 请求 → FastAPI 不立即返回完整响应 
# → StreamingResponse 持续向这个 HTTP Response 写数据 → 浏览器持续收到数据
@app.post("/api/chat/stream")
async def chat_stream(request: Request, payload: ChatRequest): # ChatRequest接受用户的输入，去掉首尾空白字符
    rag = get_service()

    async def event_generator():
        if await request.is_disconnected():
            return
        # 获取会话线程id
        thread_id = payload.thread_id
        stream = None
        next_event: asyncio.Task | None = None
        # time 模块提供的一个单调递增计时器，它返回的是一个表示“程序运行后经过了多久”的时间值
        last_ping = monotonic()
        try:
            
            if not thread_id:
                logger.info("SSE: no thread_id")
                thread = rag.create_thread(
                    payload.user_id,
                    request_id=payload.request_id,
                )
                yield _sse_event(
                    "thread.created",
                    {"thread_id": thread.thread_id, "request_id": payload.request_id},
                )
                thread_id = thread.thread_id

            # 这里只创建异步生成器，不会执行 stream_answer 的函数体。
            stream = rag.stream_answer(
                thread_id,
                payload.message,
                user_id=payload.user_id,
                request_id=payload.request_id,
            )
            while True:
                if await request.is_disconnected():
                    return

                if next_event is None:
                    # 从异步生成器 stream 中异步获取下一个事件，并把这个读取操作放到一个独立的 asyncio 任务中执行
                    next_event = asyncio.create_task(stream.__anext__()) # __anext__() 是一个异步生成器的方法，用于获取下一个值。

                # 最多等待 1 秒，检查这个任务是否完成。
                done, _ = await asyncio.wait({next_event}, timeout=1.0)
                if not done: # 如果任务还没完成，继续循环
                    logger.debug("SSE: 1s 未完成任务，waiting for next event")
                    if await request.is_disconnected():   # 如果客户端已经断开连接
                        next_event.cancel()    # 取消这个任务，防止它继续执行

                        # try:
                        #     some_operation()
                        # except SomeException:
                        #     pass           
                        # 等待任务结束；
                        # 如果它因为取消而抛出 CancelledError，
                        # 这是预期行为，忽略它即可。
                        with suppress(asyncio.CancelledError): # 在指定代码块中，如果发生指定类型的异常，就忽略这个异常，不让它继续向外抛出。
                            await next_event
                        return
                    # 每隔 15 秒发送一个 ping 事件，保持连接不被关闭
                    if monotonic() - last_ping >= 15:
                        logger.debug("SSE: 15s 未发送 ping，发送 ping") 
                        yield ": ping\n\n"
                        last_ping = monotonic()
                    continue


                # 如果任务已经完成，获取它的结果
                try:
                    event = next_event.result()
                    logger.debug("SSE: 收到事件 %s", event["event"], "-data:",event["data"])
                except StopAsyncIteration:
                    break
                finally:
                    next_event = None

                yield _sse_event(event["event"], event["data"])
                if await request.is_disconnected():
                    return
        except Exception as exc:  # noqa: BLE001
            if not await request.is_disconnected():
                yield _sse_event("error", {"message": _public_error(exc)})
        finally:
            if next_event is not None and not next_event.done():
                next_event.cancel()
                with suppress(asyncio.CancelledError):
                    await next_event
            if stream is not None:
                await stream.aclose()

    # StreamingResponse 持续向这个 HTTP Response 写数据，StreamingResponse 
    # 本身只是让 FastAPI 可以边生成边发送数据，
    # 它不会自动保持连接永久打开。连接什么时候结束，取决于传入的生成器什么时候结束
    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


def _sse_event(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _public_error(exc: Exception) -> str:
    if isinstance(exc, PermissionError):
        return "无权访问该对话。"
    if isinstance(exc, ValueError):
        return "请求参数无效，请检查 request_id、thread_id 和消息内容。"
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
        return "当前服务响应较慢，请稍后重试。"
    if isinstance(exc, AgentRunLimitExceeded):
        return "当前请求执行步骤过多，请换一种方式描述问题。"
    return "服务处理失败，请稍后重试。"
