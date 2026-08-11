from cinegraph.domain.retrieval.vector_data import (
    DenseVector,
    DocumentVector,
    HybridVector,
    QueryVector,
    SparseVector,
)
from cinegraph.ports.retrieval.vector_encoder import VectorEncoder


class RecordingVectorEncoder:
    # Initialize a fake that records the original text for each encoding path.
    def __init__(self) -> None:
        self.queries: list[str] = []
        self.documents: list[str] = []
        self.hybrid = HybridVector(DenseVector((0.25,)), SparseVector((2,), (1.0,)))

    # Record query text and return the distinct query wrapper.
    def encode_query(self, text: str) -> QueryVector:
        self.queries.append(text)
        return QueryVector(self.hybrid)

    # Record document text and return the distinct document wrapper.
    def encode_document(self, text: str) -> DocumentVector:
        self.documents.append(text)
        return DocumentVector(self.hybrid)


# Verify a local fake satisfies the encoder port without an external model.
def test_vector_encoder_preserves_text_and_wrapper_distinction() -> None:
    encoder: VectorEncoder = RecordingVectorEncoder()
    query_text = "Claire asks about the family dinner"
    document_text = "Phil describes the family dinner"

    query = encoder.encode_query(query_text)
    document = encoder.encode_document(document_text)

    assert isinstance(query, QueryVector)
    assert isinstance(document, DocumentVector)
    assert query.vector.dense.values == (0.25,)
    assert query.vector.sparse.values == (1.0,)
    assert document.vector.dense.values == (0.25,)
    assert document.vector.sparse.values == (1.0,)
    assert isinstance(encoder, RecordingVectorEncoder)
    assert encoder.queries == [query_text]
    assert encoder.documents == [document_text]