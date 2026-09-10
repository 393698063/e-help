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

from .config import get_settings
from .models import ChatRequest, ReindexResponse, ThreadCreateResponse
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
    service = RAGService(settings)
    app.state.service = service
    yield


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
@app.post("/api/threads", response_model=ThreadCreateResponse)
async def create_thread():
    thread = get_service().create_thread()
    return ThreadCreateResponse(thread_id=thread.thread_id, created_at=thread.created_at)

# 重建索引
@app.post("/api/knowledge/reindex", response_model=ReindexResponse)
async def reindex():
    rag = get_service()
    count = rag.refresh_index()
    if rag.index_ready:
        return ReindexResponse(status="ok", document_id="knowledge", chunks_indexed=count)
    return ReindexResponse(status=f"degraded: {rag.index_error}", document_id="knowledge", chunks_indexed=count)


# SSE 并不是 FastAPI 单独“建立一条特殊连接”的 API。

# 本质上是：客户端发起一个普通 HTTP 请求 → FastAPI 不立即返回完整响应 
# → StreamingResponse 持续向这个 HTTP Response 写数据 → 浏览器持续收到数据
@app.post("/api/chat/stream")
async def chat_stream(request: Request, payload: ChatRequest):
    rag = get_service()

    async def event_generator():
        if await request.is_disconnected():
            return

        if not payload.thread_id:
            thread = rag.create_thread()
            yield _sse_event("thread.created", {"thread_id": thread.thread_id})
            thread_id = thread.thread_id
        else:
            thread_id = payload.thread_id

        stream = rag.stream_answer(thread_id, payload.message)
        next_event: asyncio.Task | None = None
        last_ping = monotonic()
        try:
            while True:
                if await request.is_disconnected():
                    return

                if next_event is None:
                    next_event = asyncio.create_task(stream.__anext__())

                done, _ = await asyncio.wait({next_event}, timeout=1.0)
                if not done:
                    if await request.is_disconnected():
                        next_event.cancel()
                        with suppress(asyncio.CancelledError):
                            await next_event
                        return
                    if monotonic() - last_ping >= 15:
                        yield ": ping\n\n"
                        last_ping = monotonic()
                    continue

                try:
                    event = next_event.result()
                except StopAsyncIteration:
                    break
                finally:
                    next_event = None

                yield _sse_event(event["event"], event["data"])
                if await request.is_disconnected():
                    return
        except Exception as exc:  # noqa: BLE001
            if not await request.is_disconnected():
                yield _sse_event("error", {"message": str(exc)})
        finally:
            if next_event is not None and not next_event.done():
                next_event.cancel()
                with suppress(asyncio.CancelledError):
                    await next_event
            await stream.aclose()

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
