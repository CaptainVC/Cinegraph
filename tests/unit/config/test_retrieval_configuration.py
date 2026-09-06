import math

import pytest

from cinegraph.common.error_messages import RetrievalErrorMessages
from cinegraph.config import EmbeddingConfiguration, HybridRetrievalConfiguration


@pytest.mark.parametrize("value", [True, 1.0, "384", None])
def test_embedding_dense_dimension_requires_a_positive_integer(value: object) -> None:
    with pytest.raises(
        ValueError,
        match=RetrievalErrorMessages.EMBEDDING_DENSE_DIMENSION_MUST_BE_POSITIVE,
    ):
        EmbeddingConfiguration("dense", "sparse", 1, 1.0, value)  # type: ignore[arg-type]


@pytest.mark.parametrize("value", [0, -1, True])
def test_embedding_inference_threads_require_positive_integer(value: object) -> None:
    with pytest.raises(ValueError, match="Embedding inference threads"):
        EmbeddingConfiguration("dense", "sparse", 1, 1.0, inference_threads=value)  # type: ignore[arg-type]


@pytest.mark.parametrize("value", [True, 2.0, "2", None])
def test_hybrid_candidate_limits_require_positive_integers(value: object) -> None:
    with pytest.raises(
        ValueError,
        match=RetrievalErrorMessages.HYBRID_RETRIEVAL_CANDIDATE_LIMITS_INVALID,
    ):
        HybridRetrievalConfiguration(candidate_overfetch_multiplier=value)  # type: ignore[arg-type]


def test_hybrid_overfetch_cap_cannot_be_lower_than_public_result_limit() -> None:
    with pytest.raises(
        ValueError,
        match=RetrievalErrorMessages.HYBRID_RETRIEVAL_CANDIDATE_LIMITS_INVALID,
    ):
        HybridRetrievalConfiguration(
            candidate_overfetch_cap=9,
            max_requested_result_limit=10,
        )


@pytest.mark.parametrize("value", [True, 1, math.nan, math.inf, "0.5", None])
def test_hybrid_overlap_ratio_requires_a_finite_float(value: object) -> None:
    with pytest.raises(
        ValueError,
        match=RetrievalErrorMessages.HYBRID_RETRIEVAL_OVERLAP_RATIO_INVALID,
    ):
        HybridRetrievalConfiguration(maximum_member_overlap_ratio=value)  # type: ignore[arg-type]
