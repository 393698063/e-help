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

    def resolved_embedding_base_url(self) -> str | None:
        return self.close_api_base_url or self.closeai_base_url

    def resolved_chat_base_url(self) -> str | None:
        return self.closeai_base_url or self.close_api_base_url

    def resolved_milvus_uri(self) -> str:
        return self.milvus_uri or ""


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
