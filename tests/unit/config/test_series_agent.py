from math import inf

import pytest
from pydantic import SecretStr, ValidationError

from cinegraph.config.graph_claims import MAX_GRAPH_NAME_LENGTH, MAX_GRAPH_PREDICATE_LENGTH
from cinegraph.config.graph_rag import (
    MAX_GRAPH_RAG_CANDIDATE_EPISODES,
    MAX_GRAPH_RAG_CLAIMS,
    MAX_GRAPH_RAG_EVIDENCE_PER_CLAIM,
    MAX_GRAPH_RAG_HOPS,
    MAX_GRAPH_RAG_PREDICATES,
    MAX_GRAPH_RAG_SEEDS,
)
from cinegraph.config.models import DEFAULT_MODEL_CONFIGURATION
from cinegraph.config.series_agent import MAX_SERIES_AGENT_CITATIONS, SeriesAgentConfiguration
from cinegraph.config.settings import OpenAISettings


def test_series_agent_defaults_are_bounded_and_route_luna_tools() -> None:
    configuration = SeriesAgentConfiguration()
    assert configuration.graph_seed_limit == 8
    assert configuration.graph_predicate_limit == 8
    assert configuration.model_retry_count == 1
    assert DEFAULT_MODEL_CONFIGURATION.main_model == "gpt-5.6-terra"
    assert DEFAULT_MODEL_CONFIGURATION.rag_answer_model == "gpt-5.6-luna"
    assert DEFAULT_MODEL_CONFIGURATION.agent_tool_selector_model == "gpt-5.6-luna"


@pytest.mark.parametrize(
    "field", ["question_max_length", "total_tool_call_limit", "tool_selector_max_tools"]
)
def test_series_agent_rejects_bool_limits(field: str) -> None:
    with pytest.raises(ValueError):
        SeriesAgentConfiguration(**{field: True})


def test_retry_zero_is_valid_but_nan_and_invalid_relations_are_not() -> None:
    assert SeriesAgentConfiguration(model_retry_count=0).model_retry_count == 0
    with pytest.raises(ValueError):
        SeriesAgentConfiguration(retry_initial_delay=inf)
    with pytest.raises(ValueError):
        SeriesAgentConfiguration(total_tool_call_limit=5)
    with pytest.raises(ValueError):
        SeriesAgentConfiguration(model_call_limit=2)


@pytest.mark.parametrize(
    "overrides",
    [
        {"model_retry_count": True},
        {"model_retry_count": -1},
        {"retry_initial_delay": True},
        {"retry_initial_delay": -0.1},
        {"retry_jitter": 1},
        {"question_max_length": 10, "transcript_question_max_length": 11},
        {"max_candidate_episodes": MAX_GRAPH_RAG_CANDIDATE_EPISODES + 1},
        {"graph_hops": MAX_GRAPH_RAG_HOPS + 1},
        {"graph_claim_limit": MAX_GRAPH_RAG_CLAIMS + 1},
        {"graph_evidence_per_claim": MAX_GRAPH_RAG_EVIDENCE_PER_CLAIM + 1},
        {"graph_seed_max_length": MAX_GRAPH_NAME_LENGTH + 1},
        {"graph_predicate_max_length": MAX_GRAPH_PREDICATE_LENGTH + 1},
        {
            "transcript_tool_call_limit": 3,
            "graph_tool_call_limit": 1,
            "total_tool_call_limit": 2,
        },
        {"graph_seed_limit": MAX_GRAPH_RAG_SEEDS + 1},
        {"graph_predicate_limit": MAX_GRAPH_RAG_PREDICATES + 1},
        {"structured_response_citation_limit": MAX_SERIES_AGENT_CITATIONS + 1},
        {
            "transcript_retrieval_limit": 1,
            "graph_claim_limit": 1,
            "graph_evidence_per_claim": 1,
            "structured_response_citation_limit": 3,
        },
        {"tool_selector_max_tools": 3},
    ],
)
def test_series_agent_rejects_each_governed_cap_and_relation(
    overrides: dict[str, object],
) -> None:
    with pytest.raises(ValueError):
        SeriesAgentConfiguration(**overrides)  # type: ignore[arg-type]


def test_openai_model_fields_are_trimmed_and_defaulted_without_key_loading() -> None:
    settings = OpenAISettings(openai_api_key=SecretStr("test"))
    assert settings.agent_synthesis_model == "gpt-5.6-terra"
    assert settings.agent_tool_selector_model == "gpt-5.6-luna"
    with pytest.raises(ValidationError):
        OpenAISettings(openai_api_key=SecretStr("test"), agent_synthesis_model=" terra")
