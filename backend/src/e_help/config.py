from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ENV_FILE = Path(__file__).resolve().parents[2] / ".env"
load_dotenv(ENV_FILE, override=True)

class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "e-help"
    backend_host: str = "0.0.0.0"
    backend_port: int = 8000
    log_level: str = "INFO"
    knowledge_path: Path = Field(
        default_factory=lambda: Path(__file__).resolve().parents[2] / "knowledge.txt"
    )

    milvus_uri: str = Field(
        default_factory=lambda: "",
        validation_alias=AliasChoices("milvus_uri", "MILVUS_URI", "MILVUS_DB_URL"),
    )
    milvus_db_name: str = "e_help_db"
    milvus_collection_name: str = "e_help_after_sale"

    close_api_key: str | None = None
    close_api_base_url: str | None = None
    closeai_base_url: str | None = None

    embed_model: str = "text-embedding-3-small"
    chat_model: str = "gpt-5.4-mini"
    embedding_dimension: int = 1536
    chunk_size: int = 400
    chunk_overlap: int = 80
    top_k: int = 5
    intent_confidence_threshold: float = 0.65
    rewrite_history_turns: int = 6
    db_url: str | None = Field(
        default=None,
        validation_alias=AliasChoices("db_url", "DB_URL"),
    )
    long_term_memory_limit: int = 5

    # Agent 运行预算和外部工具边界，均可由环境变量覆盖。
    agent_timeout_seconds: float = 60.0
    agent_recursion_limit: int = 20
    agent_max_model_calls: int = 10
    agent_max_tool_calls: int = 12
    tool_model_timeout_seconds: float = 30.0
    tool_embedding_timeout_seconds: float = 15.0
    tool_milvus_timeout_seconds: float = 5.0
    tool_memory_timeout_seconds: float = 3.0
    tool_max_retries: int = 2                       # 工具最大重试次数
    tool_retry_initial_delay_seconds: float = 0.5
    tool_retry_max_delay_seconds: float = 4.0
    tool_max_query_length: int = 2000
    tool_max_result_chars: int = 12000              # 工具返回的最大字符数
    tool_max_retrieval_top_k: int = 10

    # PostgreSQL pool and socket boundaries.
    db_pool_min_size: int = 1
    db_pool_max_size: int = 8
    db_pool_timeout_seconds: float = 10.0
    db_connect_timeout_seconds: float = 10.0
    db_socket_timeout_seconds: float = 30.0
    db_reconnect_timeout_seconds: float = 30.0

    def resolved_embedding_base_url(self) -> str | None:
        return self.close_api_base_url or self.closeai_base_url

    def resolved_chat_base_url(self) -> str | None:
        return self.closeai_base_url or self.close_api_base_url

    def resolved_milvus_uri(self) -> str:
        return self.milvus_uri or ""


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
