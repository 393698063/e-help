from __future__ import annotations

import json
#函数“asynccontextmanager”已弃用
#   Annotating the return type as `-> AsyncIterator[Foo]` with `@asynccontextmanager` is deprecated. Use `-> AsyncGenerator[Foo]` instead.Pylance
from contextlib import asynccontextmanager
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
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]: # 生命周期函数，在服务器启动和关闭时执行
    global service
    settings = get_settings()
    service = RAGService(settings)
    app.state.service = service         #FastAPI 的 app.state 是一个全局状态容器，可以挂任意对象。把 service 挂上去之后，路由函数里可以通过 request.app.state.service 访问到它
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


@app.get("/api/health")
async def health():
    rag = get_service()
    return {
        "status": "ok",
        "index_ready": rag.index_ready,
        "index_error": rag.index_error,
    }


@app.post("/api/threads", response_model=ThreadCreateResponse)
async def create_thread():
    thread = get_service().create_thread()
    return ThreadCreateResponse(thread_id=thread.thread_id, created_at=thread.created_at)


@app.post("/api/knowledge/reindex", response_model=ReindexResponse)
async def reindex():
    rag = get_service()
    count = rag.refresh_index()
    if rag.index_ready:
        return ReindexResponse(status="ok", document_id="knowledge", chunks_indexed=count)
    return ReindexResponse(status=f"degraded: {rag.index_error}", document_id="knowledge", chunks_indexed=count)


@app.post("/api/chat/stream")
async def chat_stream(request: Request, payload: ChatRequest):
    del request
    rag = get_service()

    async def event_generator():
        if not payload.thread_id:
            thread = rag.create_thread()
            yield _sse_event("thread.created", {"thread_id": thread.thread_id})
            thread_id = thread.thread_id
        else:
            thread_id = payload.thread_id
        try:
            for event in rag.stream_answer(thread_id, payload.message):
                yield _sse_event(event["event"], event["data"])
        except Exception as exc:  # noqa: BLE001
            yield _sse_event("error", {"message": str(exc)})

    return StreamingResponse(event_generator(), media_type="text/event-stream")


def _sse_event(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
