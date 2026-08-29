from dataclasses import dataclass
from math import isfinite
from numbers import Real

from cinegraph.common.error_messages import SeriesAgentErrorMessages
from cinegraph.config.graph_claims import MAX_GRAPH_NAME_LENGTH, MAX_GRAPH_PREDICATE_LENGTH
from cinegraph.config.graph_rag import (
    DEFAULT_GRAPH_RAG_CLAIMS,
    DEFAULT_GRAPH_RAG_EVIDENCE_PER_CLAIM,
    DEFAULT_GRAPH_RAG_HOPS,
    MAX_GRAPH_RAG_CANDIDATE_EPISODES,
    MAX_GRAPH_RAG_CLAIMS,
    MAX_GRAPH_RAG_EVIDENCE_PER_CLAIM,
    MAX_GRAPH_RAG_HOPS,
    MAX_GRAPH_RAG_PREDICATES,
    MAX_GRAPH_RAG_SEEDS,
)

SERIES_TRANSCRIPT_TOOL_NAME = "grounded_transcript_answer"
SERIES_GRAPH_TOOL_NAME = "authorized_graph_relationships"
SERIES_TRANSCRIPT_TOOL_DESCRIPTION = (
    "Answer a series question using authorized grounded transcript evidence."
)
SERIES_GRAPH_TOOL_DESCRIPTION = "Find authorized relationships among bounded semantic seeds."
MAX_SERIES_AGENT_CITATIONS = 32
MAX_SERIES_AGENT_ANSWER_LENGTH = 12_000
SERIES_AGENT_TOOL_NAMES = frozenset(
    {SERIES_TRANSCRIPT_TOOL_NAME, SERIES_GRAPH_TOOL_NAME}
)
SERIES_STRUCTURED_RESPONSE_TOOL_NAME = "_StructuredSeriesResponse"
SERIES_STRUCTURED_RESPONSE_TOOL_MESSAGE = "Structured grounded response."


@dataclass(frozen=True, slots=True)
class SeriesAgentConfiguration:
    """Central safety and cost bounds for the series research runtime."""

    question_max_length: int = 2_000
    answer_max_length: int = MAX_SERIES_AGENT_ANSWER_LENGTH
    transcript_question_max_length: int = 1_000
    graph_seed_max_length: int = MAX_GRAPH_NAME_LENGTH
    graph_predicate_max_length: int = MAX_GRAPH_PREDICATE_LENGTH
    max_candidate_episodes: int = MAX_GRAPH_RAG_CANDIDATE_EPISODES
    graph_seed_limit: int = MAX_GRAPH_RAG_SEEDS
    graph_predicate_limit: int = MAX_GRAPH_RAG_PREDICATES
    transcript_retrieval_limit: int = 8
    graph_hops: int = DEFAULT_GRAPH_RAG_HOPS
    graph_claim_limit: int = DEFAULT_GRAPH_RAG_CLAIMS
    graph_evidence_per_claim: int = DEFAULT_GRAPH_RAG_EVIDENCE_PER_CLAIM
    model_call_limit: int = 4
    transcript_tool_call_limit: int = 2
    graph_tool_call_limit: int = 2
    total_tool_call_limit: int = 4
    model_retry_count: int = 1
    retry_initial_delay: float = 0.0
    retry_jitter: bool = False
    structured_response_citation_limit: int = MAX_SERIES_AGENT_CITATIONS
    tool_selector_max_tools: int = 2

    def __post_init__(self) -> None:
        positive_integer_fields = (
            self.question_max_length,
            self.answer_max_length,
            self.transcript_question_max_length,
            self.graph_seed_max_length,
            self.graph_predicate_max_length,
            self.max_candidate_episodes,
            self.graph_seed_limit,
            self.graph_predicate_limit,
            self.transcript_retrieval_limit,
            self.graph_hops,
            self.graph_claim_limit,
            self.graph_evidence_per_claim,
            self.model_call_limit,
            self.transcript_tool_call_limit,
            self.graph_tool_call_limit,
            self.total_tool_call_limit,
            self.structured_response_citation_limit,
            self.tool_selector_max_tools,
        )
        if any(
            isinstance(value, bool) or not isinstance(value, int) or value < 1
            for value in positive_integer_fields
        ):
            raise ValueError(SeriesAgentErrorMessages.CONFIG_LIMITS_POSITIVE)
        if (
            isinstance(self.model_retry_count, bool)
            or not isinstance(self.model_retry_count, int)
            or self.model_retry_count < 0
        ):
            raise ValueError(SeriesAgentErrorMessages.CONFIG_RETRY_NON_NEGATIVE)
        if (
            isinstance(self.retry_initial_delay, bool)
            or not isinstance(self.retry_initial_delay, Real)
            or not isfinite(self.retry_initial_delay)
            or self.retry_initial_delay < 0
        ):
            raise ValueError(SeriesAgentErrorMessages.CONFIG_DELAY_NON_NEGATIVE)
        if not isinstance(self.retry_jitter, bool):
            raise ValueError(SeriesAgentErrorMessages.CONFIG_JITTER_BOOLEAN)
        if self.transcript_question_max_length > self.question_max_length:
            raise ValueError(SeriesAgentErrorMessages.CONFIG_QUESTION_RELATION)
        if self.answer_max_length > MAX_SERIES_AGENT_ANSWER_LENGTH:
            raise ValueError(SeriesAgentErrorMessages.CONFIG_ARGUMENT_CAP)
        if self.max_candidate_episodes > MAX_GRAPH_RAG_CANDIDATE_EPISODES:
            raise ValueError(SeriesAgentErrorMessages.CONFIG_CANDIDATE_CAP)
        if (
            self.graph_hops > MAX_GRAPH_RAG_HOPS
            or self.graph_claim_limit > MAX_GRAPH_RAG_CLAIMS
            or self.graph_evidence_per_claim > MAX_GRAPH_RAG_EVIDENCE_PER_CLAIM
        ):
            raise ValueError(SeriesAgentErrorMessages.CONFIG_GRAPHRAG_CAP)
        if (
            self.graph_seed_max_length > MAX_GRAPH_NAME_LENGTH
            or self.graph_predicate_max_length > MAX_GRAPH_PREDICATE_LENGTH
        ):
            raise ValueError(SeriesAgentErrorMessages.CONFIG_ARGUMENT_CAP)
        if self.total_tool_call_limit < max(
            self.transcript_tool_call_limit, self.graph_tool_call_limit
        ):
            raise ValueError(SeriesAgentErrorMessages.CONFIG_TOTAL_TOOL_MIN)
        if (
            self.total_tool_call_limit
            > self.transcript_tool_call_limit + self.graph_tool_call_limit
        ):
            raise ValueError(SeriesAgentErrorMessages.CONFIG_TOTAL_TOOL_MAX)
        if (
            self.model_call_limit < 2
            or self.model_call_limit < min(self.total_tool_call_limit, 2) + 1
        ):
            raise ValueError(SeriesAgentErrorMessages.CONFIG_MODEL_CALL_CAP)
        if (
            self.graph_seed_limit > MAX_GRAPH_RAG_SEEDS
            or self.graph_predicate_limit > MAX_GRAPH_RAG_PREDICATES
        ):
            raise ValueError(SeriesAgentErrorMessages.CONFIG_SEED_CAP)
        if self.structured_response_citation_limit > MAX_SERIES_AGENT_CITATIONS:
            raise ValueError(SeriesAgentErrorMessages.CONFIG_CITATION_CAP)
        if (
            self.structured_response_citation_limit
            > self.transcript_retrieval_limit
            + self.graph_claim_limit * self.graph_evidence_per_claim
        ):
            raise ValueError(SeriesAgentErrorMessages.CONFIG_CITATION_EVIDENCE_RELATION)
        if self.tool_selector_max_tools > 2:
            raise ValueError(SeriesAgentErrorMessages.CONFIG_SELECTOR_CAP)


DEFAULT_SERIES_AGENT_CONFIGURATION = SeriesAgentConfiguration()
