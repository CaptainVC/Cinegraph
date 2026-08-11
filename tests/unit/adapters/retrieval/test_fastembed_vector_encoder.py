import math
from dataclasses import dataclass

import pytest

from cinegraph.adapters.retrieval.fastembed_vector_encoder import (
    FastEmbedVectorEncoder,
)
from cinegraph.common.error_messages import RetrievalErrorMessages
from cinegraph.domain.exceptions.errors import InvalidModelError
from cinegraph.domain.retrieval.vector_data import DocumentVector, QueryVector


@dataclass
class FakeSparseEmbedding:
    indices: tuple[object, ...]
    values: tuple[object, ...]


class FakeBackend:
    def __init__(self, result: object) -> None:
        self.result = result
        self.query_texts: list[str] = []
        self.passage_texts: list[tuple[str, ...]] = []

    def query_embed(self, text: str):
        self.query_texts.append(text)
        return iter((self.result,))

    def passage_embed(self, texts: tuple[str, ...]):
        self.passage_texts.append(texts)
        return iter((self.result,))


class EmptyBackend:
    def query_embed(self, text: str):
        return iter(())

    def passage_embed(self, texts: tuple[str, ...]):
        return iter(())


def make_encoder(
    dense_result: object = (1, 2),
    sparse_result: object | None = None,
) -> tuple[FastEmbedVectorEncoder, FakeBackend, FakeBackend]:
    sparse_result = sparse_result or FakeSparseEmbedding((0, 2), (0.5, 0.25))
    dense_backend = FakeBackend(dense_result)
    sparse_backend = FakeBackend(sparse_result)
    return FastEmbedVectorEncoder(dense_backend, sparse_backend), dense_backend, sparse_backend


def test_encode_query_returns_query_vector_and_only_calls_query_methods() -> None:
    encoder, dense_backend, sparse_backend = make_encoder()

    result = encoder.encode_query("exact query")

    assert isinstance(result, QueryVector)
    assert dense_backend.query_texts == ["exact query"]
    assert sparse_backend.query_texts == ["exact query"]
    assert dense_backend.passage_texts == []
    assert sparse_backend.passage_texts == []


def test_encode_document_returns_document_vector_and_passes_single_text_batch() -> None:
    encoder, dense_backend, sparse_backend = make_encoder()

    result = encoder.encode_document("exact document")

    assert isinstance(result, DocumentVector)
    assert dense_backend.passage_texts == [("exact document",)]
    assert sparse_backend.passage_texts == [("exact document",)]
    assert dense_backend.query_texts == []
    assert sparse_backend.query_texts == []


def test_unsorted_sparse_results_are_sorted_with_matching_values() -> None:
    encoder, _, _ = make_encoder(
        sparse_result=FakeSparseEmbedding((4, 1, 3), (0.4, 0.1, 0.3))
    )

    result = encoder.encode_query("query")

    assert result.vector.sparse.indices == (1, 3, 4)
    assert result.vector.sparse.values == (0.1, 0.3, 0.4)


@pytest.mark.parametrize("empty_backend", ["dense", "sparse"])
def test_empty_backend_result_raises_central_error(empty_backend: str) -> None:
    dense_backend = EmptyBackend() if empty_backend == "dense" else FakeBackend((1,))
    sparse_backend = EmptyBackend() if empty_backend == "sparse" else FakeBackend(
        FakeSparseEmbedding((0,), (1,))
    )
    encoder = FastEmbedVectorEncoder(dense_backend, sparse_backend)

    with pytest.raises(
        ValueError,
        match=RetrievalErrorMessages.VECTOR_ENCODER_BACKEND_RESULT_MUST_NOT_BE_EMPTY,
    ):
        encoder.encode_query("query")


def test_nonfinite_dense_output_is_rejected_by_domain_validation() -> None:
    encoder, _, _ = make_encoder(dense_result=(math.inf,))

    with pytest.raises(
        InvalidModelError,
        match=RetrievalErrorMessages.DENSE_VECTOR_VALUES_MUST_BE_FINITE,
    ):
        encoder.encode_query("query")