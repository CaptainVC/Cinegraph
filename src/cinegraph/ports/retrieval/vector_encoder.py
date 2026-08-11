from typing import Protocol

from cinegraph.domain.retrieval.vector_data import DocumentVector, QueryVector


class VectorEncoder(Protocol):
    # Encode query text into the paired dense and sparse query representation.
    def encode_query(self, text: str) -> QueryVector: ...

    # Encode document text into the paired dense and sparse document representation.
    def encode_document(self, text: str) -> DocumentVector: ...