from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

from langchain_text_splitters import RecursiveCharacterTextSplitter



from .models import RetrievedChunk


@dataclass(slots=True)
class KnowledgeDocument:
    document_id: str
    document_hash: str
    location: str
    text: str


@dataclass(slots=True)
class KnowledgeChunk:
    id: str
    document_id: str
    chunk_index: int
    content_hash: str
    document_hash: str
    document_location: str
    content: str


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def hash_text(text: str) -> str:
    return sha256(text.encode("utf-8")).hexdigest()


def load_document(path: Path) -> KnowledgeDocument:
    text = read_text(path)
    document_hash = hash_text(text)
    return KnowledgeDocument(
        document_id=path.stem,
        document_hash=document_hash,
        location=str(path),
        text=text,
    )


def split_document(
    document: KnowledgeDocument,
    *,
    chunk_size: int,
    chunk_overlap: int,
) -> list[KnowledgeChunk]:
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        separators=["\n\n", "\n", "。", "，", "；"],
        keep_separator=True,
        strip_whitespace=True,
    )
    texts = splitter.split_text(document.text)
    chunks: list[KnowledgeChunk] = []
    for index, content in enumerate(texts):
        normalized = content.strip()
        if not normalized:
            continue
        content_hash = hash_text(normalized)
        chunks.append(
            KnowledgeChunk(
                id=f"{document.document_id}:{index}",
                document_id=document.document_id,
                chunk_index=index,
                content_hash=content_hash,
                document_hash=document.document_hash,
                document_location=document.location,
                content=normalized,
            )
        )
    return chunks


def chunk_to_retrieved(
    chunk: KnowledgeChunk,
    *,
    score: float,
) -> RetrievedChunk:
    return RetrievedChunk(
        document_id=chunk.document_id,
        chunk_index=chunk.chunk_index,
        content=chunk.content,
        score=score,
        document_location=chunk.document_location,
    )

