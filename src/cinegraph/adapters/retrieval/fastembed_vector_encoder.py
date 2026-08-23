from collections.abc import Iterable
from typing import Any, Protocol, TypeVar, cast

from cinegraph.common.error_messages import RetrievalErrorMessages
from cinegraph.config import DEFAULT_EMBEDDING_CONFIGURATION, EmbeddingConfiguration
from cinegraph.domain.retrieval.vector_data import (
    DenseVector,
    DocumentVector,
    HybridVector,
    QueryVector,
    SparseVector,
)


class SparseEmbeddingResult(Protocol):
    indices: Iterable[object]
    values: Iterable[object]


class DenseEmbeddingBackend(Protocol):
    # Return dense query embeddings for one text.
    def query_embed(self, text: str) -> Iterable[Iterable[object]]: ...

    # Return dense passage embeddings for one batch of texts.
    def passage_embed(self, texts: tuple[str, ...]) -> Iterable[Iterable[object]]: ...


class SparseEmbeddingBackend(Protocol):
    # Return sparse query embeddings for one text.
    def query_embed(self, text: str) -> Iterable[SparseEmbeddingResult]: ...

    # Return sparse passage embeddings for one batch of texts.
    def passage_embed(self, texts: tuple[str, ...]) -> Iterable[SparseEmbeddingResult]: ...


EmbeddingResult = TypeVar("EmbeddingResult")


class FastEmbedVectorEncoder:
    # Store injected dense and sparse FastEmbed-compatible backends.
    def __init__(
        self,
        dense_backend: DenseEmbeddingBackend,
        sparse_backend: SparseEmbeddingBackend,
        configuration: EmbeddingConfiguration = DEFAULT_EMBEDDING_CONFIGURATION,
    ) -> None:
        self._dense_backend = dense_backend
        self._sparse_backend = sparse_backend
        self._configuration = configuration

    @classmethod
    # Build an encoder using FastEmbed's configured dense and sparse models.
    def from_default_models(cls) -> "FastEmbedVectorEncoder":
        from fastembed import SparseTextEmbedding, TextEmbedding

        return cls(
            dense_backend=TextEmbedding(model_name=DEFAULT_EMBEDDING_CONFIGURATION.dense_model),
            sparse_backend=SparseTextEmbedding(
                model_name=DEFAULT_EMBEDDING_CONFIGURATION.sparse_model
            ),
        )

    # Encode query text into one validated hybrid query vector.
    def encode_query(self, text: str) -> QueryVector:
        # Invoke each backend once and consume only its first result.
        dense_result = self._first_result(self._dense_backend.query_embed(text))
        sparse_result = self._first_result(self._sparse_backend.query_embed(text))
        return QueryVector(self._build_hybrid_vector(dense_result, sparse_result))

    # Encode document text into one validated hybrid document vector.
    def encode_document(self, text: str) -> DocumentVector:
        return self.encode_documents((text,))[0]

    def encode_documents(self, texts: tuple[str, ...]) -> tuple[DocumentVector, ...]:
        if not texts:
            return ()
        encoded: list[DocumentVector] = []
        size = self._configuration.max_batch_size
        for offset in range(0, len(texts), size):
            batch = texts[offset : offset + size]
            dense_results = tuple(self._dense_backend.passage_embed(batch))
            sparse_results = tuple(self._sparse_backend.passage_embed(batch))
            if len(dense_results) != len(batch) or len(sparse_results) != len(batch):
                raise ValueError(
                    RetrievalErrorMessages.VECTOR_ENCODER_BACKEND_RESULT_CARDINALITY_MUST_MATCH
                )
            for dense_result, sparse_result in zip(dense_results, sparse_results):
                encoded.append(
                    DocumentVector(self._build_hybrid_vector(dense_result, sparse_result))
                )
        return tuple(encoded)

    # Consume exactly one backend result or raise the adapter's empty-result error.
    @staticmethod
    def _first_result(results: Iterable[EmbeddingResult]) -> EmbeddingResult:
        # Avoid consuming a second result from a backend iterable.
        result = next(iter(results), None)
        if result is None:
            raise ValueError(RetrievalErrorMessages.VECTOR_ENCODER_BACKEND_RESULT_MUST_NOT_BE_EMPTY)
        return result

    # Convert backend outputs before constructing domain-owned vector types.
    def _build_hybrid_vector(
        self, dense_result: Iterable[object], sparse_result: SparseEmbeddingResult
    ) -> HybridVector:
        # Convert dense values and materialize both sparse sequences before sorting.
        dense_vector = DenseVector(tuple(float(cast(Any, value)) for value in dense_result))
        configured_dimension = self._configuration.dense_vector_size
        if configured_dimension is not None and len(dense_vector.values) != configured_dimension:
            raise ValueError(RetrievalErrorMessages.VECTOR_ENCODER_DENSE_DIMENSION_MUST_MATCH)
        sparse_indices = tuple(int(cast(Any, index)) for index in sparse_result.indices)
        sparse_values = tuple(float(cast(Any, value)) for value in sparse_result.values)
        if not sparse_indices and not sparse_values:
            sparse_indices = (self._configuration.empty_sparse_fallback_index,)
            sparse_values = (self._configuration.empty_sparse_fallback_value,)
        if len(sparse_indices) != len(sparse_values):
            return HybridVector(
                dense=dense_vector,
                sparse=SparseVector(indices=sparse_indices, values=sparse_values),
            )

        # Pair equal-length sparse sequences so values stay attached to indices.
        sparse_pairs = sorted(zip(sparse_indices, sparse_values))
        sparse_vector = SparseVector(
            indices=tuple(index for index, _ in sparse_pairs),
            values=tuple(value for _, value in sparse_pairs),
        )
        return HybridVector(dense=dense_vector, sparse=sparse_vector)
