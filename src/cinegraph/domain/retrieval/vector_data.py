import math
from dataclasses import dataclass
from numbers import Real

from cinegraph.common.error_messages import RetrievalErrorMessages
from cinegraph.domain.exceptions.errors import InvalidModelError


@dataclass(frozen=True, slots=True)
class DenseVector:
    values: tuple[float, ...]

    # Validate the immutable, finite numeric dense representation.
    def __post_init__(self) -> None:
        if not isinstance(self.values, tuple):
            raise InvalidModelError(RetrievalErrorMessages.DENSE_VECTOR_VALUES_MUST_BE_TUPLE)
        if not self.values:
            raise InvalidModelError(
                RetrievalErrorMessages.DENSE_VECTOR_VALUES_MUST_NOT_BE_EMPTY
            )

        # Check type before applying numeric finiteness validation.
        for value in self.values:
            if isinstance(value, bool) or not isinstance(value, Real):
                raise InvalidModelError(
                    RetrievalErrorMessages.DENSE_VECTOR_VALUES_MUST_BE_NUMERIC
                )
            if not math.isfinite(value):
                raise InvalidModelError(
                    RetrievalErrorMessages.DENSE_VECTOR_VALUES_MUST_BE_FINITE
                )


@dataclass(frozen=True, slots=True)
class SparseVector:
    indices: tuple[int, ...]
    values: tuple[float, ...]

    # Validate the immutable, ordered sparse representation.
    def __post_init__(self) -> None:
        if not isinstance(self.indices, tuple):
            raise InvalidModelError(
                RetrievalErrorMessages.SPARSE_VECTOR_INDICES_MUST_BE_TUPLE
            )
        if not isinstance(self.values, tuple):
            raise InvalidModelError(
                RetrievalErrorMessages.SPARSE_VECTOR_VALUES_MUST_BE_TUPLE
            )
        if not self.indices or not self.values:
            raise InvalidModelError(RetrievalErrorMessages.SPARSE_VECTOR_MUST_NOT_BE_EMPTY)
        if len(self.indices) != len(self.values):
            raise InvalidModelError(
                RetrievalErrorMessages.SPARSE_VECTOR_INDICES_AND_VALUES_MUST_MATCH
            )

        # Validate index types and ordering before checking sparse weights.
        previous_index = -1
        for index in self.indices:
            if isinstance(index, bool) or not isinstance(index, int) or index < 0:
                raise InvalidModelError(
                    RetrievalErrorMessages.SPARSE_VECTOR_INDICES_MUST_BE_NON_NEGATIVE_INTS
                )
            if index <= previous_index:
                raise InvalidModelError(
                    RetrievalErrorMessages.SPARSE_VECTOR_INDICES_MUST_BE_STRICTLY_INCREASING
                )
            previous_index = index

        # Reject invalid sparse weights, including explicit zero entries.
        for value in self.values:
            if isinstance(value, bool) or not isinstance(value, Real):
                raise InvalidModelError(
                    RetrievalErrorMessages.SPARSE_VECTOR_VALUES_MUST_BE_NUMERIC
                )
            if not math.isfinite(value):
                raise InvalidModelError(
                    RetrievalErrorMessages.SPARSE_VECTOR_VALUES_MUST_BE_FINITE
                )
            if value == 0:
                raise InvalidModelError(
                    RetrievalErrorMessages.SPARSE_VECTOR_VALUES_MUST_BE_NONZERO
                )


@dataclass(frozen=True, slots=True)
class HybridVector:
    dense: DenseVector
    sparse: SparseVector

    # Ensure both representations use the vector domain types.
    def __post_init__(self) -> None:
        if not isinstance(self.dense, DenseVector):
            raise InvalidModelError(
                RetrievalErrorMessages.HYBRID_VECTOR_DENSE_MUST_BE_DENSE_VECTOR
            )
        if not isinstance(self.sparse, SparseVector):
            raise InvalidModelError(
                RetrievalErrorMessages.HYBRID_VECTOR_SPARSE_MUST_BE_SPARSE_VECTOR
            )


@dataclass(frozen=True, slots=True)
class QueryVector:
    vector: HybridVector

    # Ensure query vectors carry one complete hybrid representation.
    def __post_init__(self) -> None:
        if not isinstance(self.vector, HybridVector):
            raise InvalidModelError(RetrievalErrorMessages.QUERY_VECTOR_MUST_CONTAIN_HYBRID_VECTOR)


@dataclass(frozen=True, slots=True)
class DocumentVector:
    vector: HybridVector

    # Ensure document vectors carry one complete hybrid representation.
    def __post_init__(self) -> None:
        if not isinstance(self.vector, HybridVector):
            raise InvalidModelError(
                RetrievalErrorMessages.DOCUMENT_VECTOR_MUST_CONTAIN_HYBRID_VECTOR
            )
