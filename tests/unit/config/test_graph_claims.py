import pytest

from cinegraph.common.error_messages import GraphErrorMessages
from cinegraph.config.graph_claims import (
    GRAPH_CLAIM_EXTRACTION_REVISION,
    GRAPH_EXTRACTION_BATCH_SIZE,
    MAX_GRAPH_ALIASES,
    MAX_GRAPH_CANDIDATES,
    MAX_GRAPH_EXTRACTION_CHUNKS,
    MAX_GRAPH_NAME_LENGTH,
    MAX_GRAPH_PREDICATE_LENGTH,
    GraphClaimExtractionConfiguration,
)


def test_default_configuration_uses_central_revision_and_caps() -> None:
    configuration = GraphClaimExtractionConfiguration()
    assert configuration.revision == GRAPH_CLAIM_EXTRACTION_REVISION
    assert configuration.max_chunks == MAX_GRAPH_EXTRACTION_CHUNKS
    assert configuration.max_candidates == MAX_GRAPH_CANDIDATES
    assert configuration.max_aliases == MAX_GRAPH_ALIASES
    assert configuration.max_name_length == MAX_GRAPH_NAME_LENGTH
    assert configuration.max_predicate_length == MAX_GRAPH_PREDICATE_LENGTH
    assert configuration.batch_size == GRAPH_EXTRACTION_BATCH_SIZE


@pytest.mark.parametrize(
    "kwargs",
    [
        {"revision": "test-revision"},
        {"max_chunks": 0},
        {"max_candidates": True},
        {"max_aliases": MAX_GRAPH_ALIASES + 1},
        {"max_name_length": MAX_GRAPH_NAME_LENGTH + 1},
        {"max_predicate_length": MAX_GRAPH_PREDICATE_LENGTH + 1},
        {"batch_size": GRAPH_EXTRACTION_BATCH_SIZE + 1},
        {"max_chunks": 2, "batch_size": 3},
    ],
)
def test_configuration_rejects_invalid_types_revision_and_caps(kwargs: dict[str, object]) -> None:
    with pytest.raises(ValueError, match=GraphErrorMessages.CONFIGURATION_INVALID):
        GraphClaimExtractionConfiguration(**kwargs)  # type: ignore[arg-type]
