from datetime import UTC, datetime
from uuid import UUID

import pytest

from cinegraph.application.models.get_visible_episode_context import (
    GetVisibleEpisodeContextResult,
)
from cinegraph.application.models.search_visible_episode_segments import (
    SearchVisibleEpisodeSegmentsQuery,
)
from cinegraph.application.service.search_visible_episode_segments_service import (
    SearchVisibleEpisodeSegmentsService,
)
from cinegraph.common.error_messages import RetrievalErrorMessages
from cinegraph.domain.enums.enum import Language, RightsStatus
from cinegraph.domain.models.episode_summary.episode_summary_document import (
    EpisodeSummaryDocument,
)
from cinegraph.domain.models.transcript.transcript_segment import TranscriptSegment
from tests.factories import make_episode_ref


SOURCE_DOCUMENT_ID = UUID("00000000-0000-0000-0000-000000000401")
SOURCE_VERSION_ID = UUID("00000000-0000-0000-0000-000000000501")
SUMMARY_ID = UUID("00000000-0000-0000-0000-000000000601")
TIMESTAMP = datetime(2026, 8, 5, 15, 0, tzinfo=UTC)


class StubVisibleEpisodeContextService:
    def __init__(self, result: GetVisibleEpisodeContextResult) -> None:
        self._result = result
        self.queries = []

    def execute(self, query) -> GetVisibleEpisodeContextResult:
        self.queries.append(query)
        return self._result


def summary() -> EpisodeSummaryDocument:
    episode = make_episode_ref()
    return EpisodeSummaryDocument(
        summary_id=SUMMARY_ID,
        source_version_id=SOURCE_VERSION_ID,
        episode=episode,
        text="A concise episode summary.",
        language=Language.ENGLISH,
        rights_status=RightsStatus.ALLOWED,
        canonical_url="https://en.wikipedia.org/wiki/Pilot_(Modern_Family)",
        revision_id=123,
        revision_timestamp=TIMESTAMP,
        attribution="Wikipedia contributors, CC BY-SA",
    )


def segment(
    *,
    segment_id: int,
    start_ms: int,
    text: str,
) -> TranscriptSegment:
    return TranscriptSegment(
        segment_id=UUID(int=segment_id),
        source_version_id=SOURCE_VERSION_ID,
        episode=make_episode_ref(),
        start_ms=start_ms,
        end_ms=start_ms + 1_000,
        text=text,
        language=Language.ENGLISH,
        rights_status=RightsStatus.RESTRICTED,
    )


def query(*, search_query: str, limit: int = 5) -> SearchVisibleEpisodeSegmentsQuery:
    return SearchVisibleEpisodeSegmentsQuery(
        query=search_query,
        episode=make_episode_ref(),
        summary_source_document_id=SOURCE_DOCUMENT_ID,
        profile_watch_state=None,
        limit=limit,
    )


def test_ranks_safe_segments_by_lexical_score_and_limit() -> None:
    top_match = segment(
        segment_id=1,
        start_ms=10_000,
        text="Luke got his head stuck in the banister.",
    )
    second_match = segment(
        segment_id=2,
        start_ms=5_000,
        text="Luke is worried about the banister.",
    )
    non_match = segment(
        segment_id=3,
        start_ms=1_000,
        text="Dinner is ready for the family.",
    )
    context_service = StubVisibleEpisodeContextService(
        GetVisibleEpisodeContextResult(
            summary=summary(),
            transcript_segments=(second_match, non_match, top_match),
            safe_until_ms=None,
            summary_is_model_context_only=False,
        )
    )
    service = SearchVisibleEpisodeSegmentsService(context_service)

    result = service.execute(query(search_query="Luke stuck banister", limit=1))

    assert tuple(match.segment for match in result.matches) == (top_match,)
    assert result.summary_is_model_context_only is False
    assert result.safe_until_ms is None
    assert (
        context_service.queries[0].summary_source_document_id
        == SOURCE_DOCUMENT_ID
    )


def test_preserves_partial_context_metadata_without_reintroducing_segments() -> None:
    safe_segment = segment(
        segment_id=1,
        start_ms=10_000,
        text="Luke got stuck in the banister.",
    )
    context_service = StubVisibleEpisodeContextService(
        GetVisibleEpisodeContextResult(
            summary=summary(),
            transcript_segments=(safe_segment,),
            safe_until_ms=32_000,
            summary_is_model_context_only=True,
        )
    )
    service = SearchVisibleEpisodeSegmentsService(context_service)

    result = service.execute(query(search_query="Luke banister"))

    assert tuple(match.segment for match in result.matches) == (safe_segment,)
    assert result.summary_is_model_context_only is True
    assert result.safe_until_ms == 32_000


def test_rejects_non_positive_search_limit() -> None:
    context_service = StubVisibleEpisodeContextService(
        GetVisibleEpisodeContextResult(
            summary=None,
            transcript_segments=(),
            safe_until_ms=None,
            summary_is_model_context_only=False,
        )
    )
    service = SearchVisibleEpisodeSegmentsService(context_service)

    with pytest.raises(ValueError, match=RetrievalErrorMessages.SEARCH_LIMIT_MUST_BE_POSITIVE):
        service.execute(query(search_query="Luke", limit=0))