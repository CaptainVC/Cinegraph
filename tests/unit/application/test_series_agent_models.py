from dataclasses import replace
from uuid import UUID

import pytest
from tests.factories import make_authenticated_corpus_access_scope, make_episode_ref

from cinegraph.application.models.conversation import ConversationalSeriesChatQuery
from cinegraph.application.models.series_agent_context import SeriesAgentRuntimeContext
from cinegraph.application.models.series_agent_result import SeriesAgentCitation, SeriesAgentResult
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
