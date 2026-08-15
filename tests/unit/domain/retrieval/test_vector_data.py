import math

import pytest

from cinegraph.common.error_messages import RetrievalErrorMessages
from cinegraph.domain.exceptions.errors import InvalidModelError
from cinegraph.domain.retrieval.vector_data import (
    DenseVector,
    DocumentVector,
    HybridVector,
    QueryVector,
    SparseVector,
)


# Build a valid hybrid value shared by wrapper tests.
def hybrid_vector() -> HybridVector:
    return HybridVector(DenseVector((0.1, -2)), SparseVector((0, 4), (1.0, -0.5)))


# Confirm finite numeric dense entries are retained exactly.
def test_dense_vector_accepts_valid_values() -> None:
    vector = DenseVector((0.1, -2, 3.5))

    assert vector.values == (0.1, -2, 3.5)


@pytest.mark.parametrize(
    ("values", "message"),
    [
        ([1.0], RetrievalErrorMessages.DENSE_VECTOR_VALUES_MUST_BE_TUPLE),
        ((), RetrievalErrorMessages.DENSE_VECTOR_VALUES_MUST_NOT_BE_EMPTY),
    ],
)
# Reject mutable dense values and empty dense values with central messages.
def test_dense_vector_rejects_invalid_collection_shape(
    values: object, message: str
) -> None:
    with pytest.raises(InvalidModelError, match=message):
        DenseVector(values)  # type: ignore[arg-type]


@pytest.mark.parametrize("value", [math.inf, -math.inf, math.nan])
# Reject non-finite dense entries.
def test_dense_vector_rejects_non_finite_values(value: float) -> None:
    with pytest.raises(
        InvalidModelError,
        match=RetrievalErrorMessages.DENSE_VECTOR_VALUES_MUST_BE_FINITE,
    ):
        DenseVector((value,))


# Reject sparse collections whose lengths do not describe aligned weights.
def test_sparse_vector_rejects_mismatched_lengths() -> None:
    with pytest.raises(
        InvalidModelError,
        match=RetrievalErrorMessages.SPARSE_VECTOR_INDICES_AND_VALUES_MUST_MATCH,
    ):
        SparseVector((1, 2), (1.0,))


@pytest.mark.parametrize(
    "indices",
    [(-1,), (1, 1), (2, 1)],
)
# Reject negative, duplicate, and unsorted sparse indices.
def test_sparse_vector_rejects_invalid_indices(indices: tuple[int, ...]) -> None:
    message = (
        RetrievalErrorMessages.SPARSE_VECTOR_INDICES_MUST_BE_NON_NEGATIVE_INTS
        if indices == (-1,)
        else RetrievalErrorMessages.SPARSE_VECTOR_INDICES_MUST_BE_STRICTLY_INCREASING
    )

    with pytest.raises(InvalidModelError, match=message):
        SparseVector(indices, (1.0,) * len(indices))


@pytest.mark.parametrize(
    ("value", "message"),
    [
        (0.0, RetrievalErrorMessages.SPARSE_VECTOR_VALUES_MUST_BE_NONZERO),
        (math.inf, RetrievalErrorMessages.SPARSE_VECTOR_VALUES_MUST_BE_FINITE),
        (math.nan, RetrievalErrorMessages.SPARSE_VECTOR_VALUES_MUST_BE_FINITE),
    ],
)
# Reject zero and non-finite sparse weights.
def test_sparse_vector_rejects_invalid_values(value: float, message: str) -> None:
    with pytest.raises(InvalidModelError, match=message):
        SparseVector((1,), (value,))


# Confirm both hybrid wrappers preserve their distinct domain types.
def test_hybrid_wrappers_accept_valid_components() -> None:
    vector = hybrid_vector()

    assert QueryVector(vector).vector is vector
    assert DocumentVector(vector).vector is vector


@pytest.mark.parametrize(
    ("factory", "value", "message"),
    [
        (
            HybridVector,
            (SparseVector((1,), (1.0,)), SparseVector((2,), (1.0,))),
            RetrievalErrorMessages.HYBRID_VECTOR_DENSE_MUST_BE_DENSE_VECTOR,
        ),
        (
            HybridVector,
            (DenseVector((1.0,)), DenseVector((1.0,))),
            RetrievalErrorMessages.HYBRID_VECTOR_SPARSE_MUST_BE_SPARSE_VECTOR,
        ),
        (QueryVector, (object(),), RetrievalErrorMessages.QUERY_VECTOR_MUST_CONTAIN_HYBRID_VECTOR),
        (DocumentVector, (object(),), RetrievalErrorMessages.DOCUMENT_VECTOR_MUST_CONTAIN_HYBRID_VECTOR),
    ],
)
# Reject invalid component types at the hybrid and wrapper boundaries.
def test_vector_wrappers_reject_invalid_component_types(
    factory: type, value: tuple[object, ...], message: str
) -> None:
    with pytest.raises(InvalidModelError, match=message):
        factory(*value)
