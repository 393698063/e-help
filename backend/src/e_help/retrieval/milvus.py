from __future__ import annotations

from ..milvus_store import MilvusStore
from ..models import RetrievedChunk, RetrievedContext


class MilvusRetriever:
    def __init__(self, store: MilvusStore, embeddings, top_k: int) -> None:
        self.store = store
        self.embeddings = embeddings
        self.top_k = top_k

    def search(self, query: str) -> RetrievedContext:
        hits = self.store.search(query, embeddings=self.embeddings, top_k=self.top_k)
        chunks = [
            RetrievedChunk(
                document_id=hit.document_id,
                chunk_index=hit.chunk_index,
                content=hit.content,
                score=hit.score,
                document_location=hit.document_location,
            )
            for hit in hits
        ]
        return RetrievedContext(chunks=chunks, query=query, source="milvus")
