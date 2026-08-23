from dataclasses import dataclass
from math import isfinite

from cinegraph.common.error_messages import RetrievalErrorMessages


@dataclass(frozen=True, slots=True)
class EmbeddingConfiguration:
    dense_model: str
    sparse_model: str
    empty_sparse_fallback_index: int
    empty_sparse_fallback_value: float
    dense_vector_size: int = 384
    max_batch_size: int = 64

    def __post_init__(self) -> None:
        if any(
            not isinstance(value, str) or not value or value.strip() != value
            for value in (self.dense_model, self.sparse_model)
        ):
            raise ValueError(RetrievalErrorMessages.EMBEDDING_MODEL_NAME_MUST_BE_TRIMMED_NONEMPTY)
        if (
            isinstance(self.empty_sparse_fallback_index, bool)
            or not isinstance(self.empty_sparse_fallback_index, int)
            or self.empty_sparse_fallback_index < 0
        ):
            raise ValueError(RetrievalErrorMessages.EMBEDDING_FALLBACK_INDEX_MUST_BE_NON_NEGATIVE)
        if (
            isinstance(self.empty_sparse_fallback_value, bool)
            or not isinstance(self.empty_sparse_fallback_value, (int, float))
            or not isfinite(self.empty_sparse_fallback_value)
            or self.empty_sparse_fallback_value <= 0
        ):
            raise ValueError(
                RetrievalErrorMessages.EMBEDDING_FALLBACK_VALUE_MUST_BE_FINITE_POSITIVE
            )
        if (
            isinstance(self.dense_vector_size, bool)
            or not isinstance(self.dense_vector_size, int)
            or self.dense_vector_size < 1
        ):
            raise ValueError(RetrievalErrorMessages.EMBEDDING_DENSE_DIMENSION_MUST_BE_POSITIVE)
        if (
            isinstance(self.max_batch_size, bool)
            or not isinstance(self.max_batch_size, int)
            or self.max_batch_size < 1
        ):
            raise ValueError(RetrievalErrorMessages.EMBEDDING_BATCH_SIZE_MUST_BE_POSITIVE)


DEFAULT_EMBEDDING_CONFIGURATION = EmbeddingConfiguration(
    dense_model="BAAI/bge-small-en-v1.5",
    sparse_model="Qdrant/bm25",
    empty_sparse_fallback_index=2_147_483_647,
    empty_sparse_fallback_value=1e-12,
)
