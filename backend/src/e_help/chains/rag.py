from __future__ import annotations

from ..models import RetrievedContext


class RagChain:
    def __init__(self, retriever) -> None:
        self.retriever = retriever

    def invoke(self, query: str) -> RetrievedContext:
        return self.retriever.search(query)
