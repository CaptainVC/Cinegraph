from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class EmbeddingConfiguration:
    dense_model: str
    sparse_model: str
    empty_sparse_fallback_index: int
    empty_sparse_fallback_value: float


DEFAULT_EMBEDDING_CONFIGURATION = EmbeddingConfiguration(
    dense_model="BAAI/bge-small-en-v1.5",
    sparse_model="Qdrant/bm25",
    empty_sparse_fallback_index=2_147_483_647,
    empty_sparse_fallback_value=1e-12,
)
