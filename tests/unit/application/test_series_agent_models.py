from dataclasses import replace
from uuid import UUID

import pytest
from tests.factories import make_authenticated_corpus_access_scope, make_episode_ref

from cinegraph.application.models.conversation import ConversationalSeriesChatQuery
from cinegraph.application.models.series_agent_context import SeriesAgentRuntimeContext
from cinegraph.application.models.series_agent_result import SeriesAgentCitation, SeriesAgentResult
from cinegraph.common.identifiers import IdentifierGenerator
from cinegraph.config.graph_claims import (
    GRAPH_CLAIM_EXTRACTION_REVISION,
    MAX_GRAPH_NAME_LENGTH,
    MAX_GRAPH_PREDICATE_LENGTH,
)
from cinegraph.config.series_agent import (
    MAX_SERIES_AGENT_ANSWER_LENGTH,
    MAX_SERIES_AGENT_CITATIONS,
)
from cinegraph.domain.enums.enum import GraphClaimPolarity, GraphEntityKind
from cinegraph.domain.exceptions.errors import InvalidModelError


def test_series_context_rejects_mutable_duplicate_and_cross_series_candidates() -> None:
    episode = make_episode_ref()
    with pytest.raises((InvalidModelError, TypeError)):
        SeriesAgentRuntimeContext(
            episode.series_id, [episode], None, make_authenticated_corpus_access_scope()
        )  # type: ignore[arg-type]
    with pytest.raises(InvalidModelError):
        SeriesAgentRuntimeContext(
            episode.series_id, (episode, episode), None, make_authenticated_corpus_access_scope()
        )
    other = replace(episode, series_id=UUID(int=999))
    with pytest.raises(InvalidModelError):
        SeriesAgentRuntimeContext(
            episode.series_id, (other,), None, make_authenticated_corpus_access_scope()
        )
    with pytest.raises(InvalidModelError):
        SeriesAgentRuntimeContext(
            "not-a-uuid", (episode,), None, make_authenticated_corpus_access_scope()
        )  # type: ignore[arg-type]
    with pytest.raises(InvalidModelError):
        SeriesAgentRuntimeContext(
            episode.series_id, (), None, make_authenticated_corpus_access_scope()
        )
    with pytest.raises(InvalidModelError):
        SeriesAgentRuntimeContext(
            episode.series_id,
            (episode,),
            object(),
            make_authenticated_corpus_access_scope(),
        )  # type: ignore[arg-type]


def test_series_query_validates_identity_question_scope_and_candidates() -> None:
    episode = make_episode_ref()
    scope = make_authenticated_corpus_access_scope(revision="scope")
    query = ConversationalSeriesChatQuery(
        UUID(int=1), UUID(int=2), "scope", "Question", episode.series_id, (episode,), scope
    )
    assert query.candidate_episodes == (episode,)
    with pytest.raises(InvalidModelError):
        replace(query, question=" question")
    with pytest.raises(InvalidModelError):
        replace(query, permission_scope_revision="other")


def test_series_result_requires_selected_typed_citations() -> None:
    episode = make_episode_ref()
    citation = SeriesAgentCitation("transcript", episode, 0, 100, segment_id=UUID(int=4))
    assert SeriesAgentResult("grounded", False, (citation,)).citations == (citation,)
    with pytest.raises(ValueError):
        SeriesAgentResult("grounded", False, ())
    with pytest.raises(ValueError):
        SeriesAgentCitation(
            "transcript", episode, 0, 100, segment_id=UUID(int=4), evidence_id=UUID(int=5)
        )


@pytest.mark.parametrize(
    "citation",
    [
        ("unknown", 0, 100, UUID(int=1), None, None),
        ("transcript", True, 100, UUID(int=1), None, None),
        ("transcript", -1, 100, UUID(int=1), None, None),
        ("transcript", 100, 100, UUID(int=1), None, None),
        ("transcript", 0, 100, None, None, None),
        ("graph", 0, 100, None, None, None),
        ("graph", 0, 100, UUID(int=1), UUID(int=2), UUID(int=3)),
    ],
)
def test_series_citation_rejects_invalid_kind_timing_and_identity_shape(
    citation: tuple[object, object, object, object, object, object],
) -> None:
    episode = make_episode_ref()
    kind, start_ms, end_ms, segment_id, claim_id, evidence_id = citation
    with pytest.raises(ValueError):
        SeriesAgentCitation(
            kind,  # type: ignore[arg-type]
            episode,
            start_ms,  # type: ignore[arg-type]
            end_ms,  # type: ignore[arg-type]
            segment_id=segment_id,  # type: ignore[arg-type]
            claim_id=claim_id,  # type: ignore[arg-type]
            evidence_id=evidence_id,  # type: ignore[arg-type]
        )


def test_series_result_rejects_mutable_refusal_invalid_tools_and_duplicates() -> None:
    episode = make_episode_ref()
    citation = SeriesAgentCitation("transcript", episode, 0, 100, segment_id=UUID(int=4))
    with pytest.raises(ValueError):
        SeriesAgentResult(None, True, [citation])  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        SeriesAgentResult("answer", True, (citation,))
    with pytest.raises(ValueError):
        SeriesAgentResult(" answer", False, (citation,))
    with pytest.raises(ValueError):
        SeriesAgentResult("answer", False, (citation,), ("",))
    with pytest.raises(ValueError):
        SeriesAgentResult("answer", False, (citation, citation))
    with pytest.raises(ValueError):
        SeriesAgentResult(
            "answer", False, (citation,), ("grounded_transcript_answer", "grounded_transcript_answer")
        )
    with pytest.raises(ValueError):
        SeriesAgentResult("answer", False, (citation,), ("untrusted_tool",))
    with pytest.raises(ValueError):
        SeriesAgentResult("x" * (MAX_SERIES_AGENT_ANSWER_LENGTH + 1), False, (citation,))
    many = tuple(
        SeriesAgentCitation("transcript", episode, 0, 100, segment_id=UUID(int=100 + index))
        for index in range(MAX_SERIES_AGENT_CITATIONS + 1)
    )
    with pytest.raises(ValueError):
        SeriesAgentResult("answer", False, many)


def test_graph_citation_requires_complete_bounded_typed_trusted_projection() -> None:
    episode = make_episode_ref()
    source_id, chunk_id = UUID(int=41), UUID(int=42)
    subject_id = IdentifierGenerator.graph_entity_id(
        episode.series_id, GraphEntityKind.CHARACTER, "claire"
    )
    object_id = IdentifierGenerator.graph_entity_id(
        episode.series_id, GraphEntityKind.CHARACTER, "phil"
    )
    claim_id = IdentifierGenerator.graph_claim_id(
        GRAPH_CLAIM_EXTRACTION_REVISION,
        episode.series_id,
        subject_id,
        "married_to",
        object_id,
        GraphClaimPolarity.ASSERTED,
    )
    evidence_id = IdentifierGenerator.graph_evidence_id(claim_id, source_id, chunk_id)
    values = {
        "kind": "graph",
        "episode": episode,
        "start_ms": 0,
        "end_ms": 100,
        "claim_id": claim_id,
        "evidence_id": evidence_id,
        "source_version_id": source_id,
        "transcript_chunk_id": chunk_id,
        "subject_entity_id": subject_id,
        "subject_kind": GraphEntityKind.CHARACTER,
        "subject_display_name": "Claire",
        "predicate": "married_to",
        "object_entity_id": object_id,
        "object_kind": GraphEntityKind.CHARACTER,
        "object_display_name": "Phil",
        "polarity": GraphClaimPolarity.ASSERTED,
        "hop_distance": 1,
        "score": 0.9,
    }
    citation = SeriesAgentCitation(**values)
    assert citation.citation_id == evidence_id
    for changes in (
        {"transcript_chunk_id": None},
        {"hop_distance": True},
        {"score": True},
        {"subject_display_name": "x" * (MAX_GRAPH_NAME_LENGTH + 1)},
        {"predicate": "x" * (MAX_GRAPH_PREDICATE_LENGTH + 1)},
    ):
        with pytest.raises(ValueError):
            SeriesAgentCitation(**{**values, **changes})
