from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from pymilvus import DataType, MilvusClient

from .knowledge import KnowledgeChunk


@dataclass(slots=True)
class SearchHit:
    chunk_id: str
    document_id: str
    chunk_index: int
    content: str
    document_hash: str
    content_hash: str
    document_location: str
    score: float


class MilvusStore:
    def __init__(self, *, uri: str, db_name: str, collection_name: str, dimension: int) -> None:
        self.uri = uri or "http://127.0.0.1:19530"
        self.db_name = db_name
        self.collection_name = collection_name
        self.dimension = dimension
        self._embeddings = None
        self._collection_ready = False
        self._client = MilvusClient(uri=self.uri, db_name=self.db_name)

    def set_embeddings(self, embeddings) -> None:
        self._embeddings = embeddings

    def ensure_collection(self) -> None:
        if self._collection_ready:
            return

        if not self._client.has_collection(self.collection_name):
            schema = self._client.create_schema(
                auto_id=False,
                enable_dynamic_field=False,
            )
            schema.add_field(
                field_name="id",
                datatype=DataType.VARCHAR,
                is_primary=True,
                max_length=128,
            )
            schema.add_field(
                field_name="document_id",
                datatype=DataType.VARCHAR,
                max_length=128,
            )
            schema.add_field(
                field_name="chunk_index",
                datatype=DataType.INT64,
            )
            schema.add_field(
                field_name="content_hash",
                datatype=DataType.VARCHAR,
                max_length=128,
            )
            schema.add_field(
                field_name="document_hash",
                datatype=DataType.VARCHAR,
                max_length=128,
            )
            schema.add_field(
                field_name="document_location",
                datatype=DataType.VARCHAR,
                max_length=512,
            )
            schema.add_field(
                field_name="content",
                datatype=DataType.VARCHAR,
                max_length=4096,
            )
            schema.add_field(
                field_name="embedding",
                datatype=DataType.FLOAT_VECTOR,
                dim=self.dimension,
            )

            index_params = self._client.prepare_index_params()
            index_params.add_index(
                field_name="embedding",
                index_type="AUTOINDEX",
                metric_type="COSINE",
                params={},
            )
            self._client.create_collection(
                collection_name=self.collection_name,
                schema=schema,
                index_params=index_params,
            )

        self._client.load_collection(self.collection_name)
        self._collection_ready = True

    def drop_collection(self) -> None:
        if self._client.has_collection(self.collection_name):
            self._client.drop_collection(self.collection_name)
        self._collection_ready = False

    def existing_content_hashes(self, document_hash: str) -> set[str]:
        self.ensure_collection()
        rows = self._client.query(
            collection_name=self.collection_name,
            filter=f'document_hash == "{document_hash}"',
            output_fields=["content_hash"],
        )
        return {str(row["content_hash"]) for row in rows}

    def upsert_chunks(self, chunks: Iterable[KnowledgeChunk]) -> int:
        self.ensure_collection()
        chunk_list = list(chunks)
        if not chunk_list:
            return 0

        existing_hashes = self.existing_content_hashes(chunk_list[0].document_hash)
        pending = [chunk for chunk in chunk_list if chunk.content_hash not in existing_hashes]
        if not pending:
            return 0

        contents = [chunk.content for chunk in pending]
        embeddings = self._embed_texts(contents)
        rows = [
            {
                "id": chunk.id,
                "document_id": chunk.document_id,
                "chunk_index": chunk.chunk_index,
                "content_hash": chunk.content_hash,
                "document_hash": chunk.document_hash,
                "document_location": chunk.document_location,
                "content": chunk.content,
                "embedding": embedding,
            }
            for chunk, embedding in zip(pending, embeddings, strict=True)
        ]
        #用 upsert 的场景是：你不想预先查重，直接让数据库处理"有则更新、无则插入"
        self._client.insert(collection_name=self.collection_name, data=rows)
        self._client.flush(collection_name=self.collection_name)
        return len(pending)

    def search(self, query: str, *, embeddings, top_k: int) -> list[SearchHit]:
        self.ensure_collection()
        query_vector = embeddings.embed_query(query)
        search_results = self._client.search(
            collection_name=self.collection_name,
            data=[query_vector],
            anns_field="embedding",
            search_params={"metric_type": "COSINE", "params": {}},
            limit=top_k,
            output_fields=[
                "id",
                "document_id",
                "chunk_index",
                "content_hash",
                "document_hash",
                "document_location",
                "content",
            ],
        )

        hits: list[SearchHit] = []
        for hit in search_results[0]:
            hits.append(
                SearchHit(
                    chunk_id=str(hit["id"]),
                    document_id=str(hit["document_id"]),
                    chunk_index=int(hit["chunk_index"]),
                    content=str(hit["content"]),
                    document_hash=str(hit["document_hash"]),
                    content_hash=str(hit["content_hash"]),
                    document_location=str(hit["document_location"]),
                    score=float(hit["distance"]),
                )
            )
        return hits

    def _embed_texts(self, texts: list[str]) -> list[list[float]]:
        if self._embeddings is None:
            raise RuntimeError("Embedding function not attached")
        return self._embeddings.embed_documents(texts)
