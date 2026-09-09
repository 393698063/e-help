from __future__ import annotations

from langchain.chat_models import init_chat_model as _init_chat_model
from langchain.embeddings import init_embeddings as _init_embeddings


def init_embeddings(
    *,
    model: str,
    provider: str,
    api_key: str | None,
    base_url: str | None,
    dimensions: int,
):
    options: dict[str, object] = {
        "model": f"{provider}:{model}",
        "api_key": api_key,
        "base_url": base_url,
    }
    # text-embedding-3-small 默认就是 1536 维；部分 OpenAI 兼容代理
    # 不接受 dimensions 参数，因此默认维度时保持与 Jupyter 调用一致。
    if dimensions != 1536:
        options["dimensions"] = dimensions
    return _init_embeddings(
        **options,
    )


def init_chat_model(
    *,
    model: str,
    model_provider: str,
    api_key: str | None,
    base_url: str | None,
):
    return _init_chat_model(
        model=model,
        model_provider=model_provider,
        api_key=api_key,
        base_url=base_url,
    )
