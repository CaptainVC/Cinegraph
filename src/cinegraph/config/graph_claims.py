from dataclasses import dataclass

from cinegraph.common.error_messages import GraphErrorMessages

GRAPH_CLAIM_EXTRACTION_REVISION = "graph-claim-v1"
MAX_GRAPH_EXTRACTION_CHUNKS = 256
MAX_GRAPH_CANDIDATES = 512
MAX_GRAPH_ALIASES = 32
MAX_GRAPH_NAME_LENGTH = 256
MAX_GRAPH_PREDICATE_LENGTH = 96
GRAPH_EXTRACTION_BATCH_SIZE = 32


@dataclass(frozen=True, slots=True)
class GraphClaimExtractionConfiguration:
    revision: str = GRAPH_CLAIM_EXTRACTION_REVISION
    max_chunks: int = MAX_GRAPH_EXTRACTION_CHUNKS
    max_candidates: int = MAX_GRAPH_CANDIDATES
    max_aliases: int = MAX_GRAPH_ALIASES
    max_name_length: int = MAX_GRAPH_NAME_LENGTH
    max_predicate_length: int = MAX_GRAPH_PREDICATE_LENGTH
    batch_size: int = GRAPH_EXTRACTION_BATCH_SIZE

    def __post_init__(self) -> None:
        if self.revision != GRAPH_CLAIM_EXTRACTION_REVISION:
            raise ValueError(GraphErrorMessages.CONFIGURATION_INVALID)
        values = (
            self.max_chunks,
            self.max_candidates,
            self.max_aliases,
            self.max_name_length,
            self.max_predicate_length,
            self.batch_size,
        )
        if any(
            isinstance(value, bool) or not isinstance(value, int) or value < 1 for value in values
        ):
            raise ValueError(GraphErrorMessages.CONFIGURATION_INVALID)
        if (
            self.max_chunks > MAX_GRAPH_EXTRACTION_CHUNKS
            or self.max_candidates > MAX_GRAPH_CANDIDATES
            or self.max_aliases > MAX_GRAPH_ALIASES
            or self.max_name_length > MAX_GRAPH_NAME_LENGTH
            or self.max_predicate_length > MAX_GRAPH_PREDICATE_LENGTH
            or self.batch_size > GRAPH_EXTRACTION_BATCH_SIZE
            or self.batch_size > self.max_chunks
        ):
            raise ValueError(GraphErrorMessages.CONFIGURATION_INVALID)


DEFAULT_GRAPH_CLAIM_EXTRACTION_CONFIGURATION = GraphClaimExtractionConfiguration()
