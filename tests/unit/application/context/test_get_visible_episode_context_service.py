from datetime import UTC, datetime
from uuid import UUID

from cinegraph.application.models.get_visible_episode_context import (
    GetVisibleEpisodeContextQuery,
)
from cinegraph.application.models.get_visible_episode_summary import (
    GetVisibleEpisodeSummaryResult,
)
from cinegraph.application.service.get_visible_episode_context_service import (
    GetVisibleEpisodeContextService,
)
from cinegraph.domain.enums.enum import Language, RightsStatus
from cinegraph.domain.models.episode_summary.episode_summary_document import (
    EpisodeSummaryDocument,
)
from cinegraph.domain.models.transcript.transcript_segment import TranscriptSegment
from cinegraph.domain.models.watch_state.episode_watch_state import (
    EpisodeWatchProgress,
)
from cinegraph.domain.models.watch_state.profile_watch_state import (
    ProfileWatchState,
)
from cinegraph.domain.models.watch_state.series_watch_state import (
    SeriesWatchState,
)
from cinegraph.domain.policy.spoiler_policy import SpoilerPolicy
from tests.factories import (
    make_authenticated_corpus_access_scope,
    make_episode_ref,
    make_guest_corpus_access_scope,
)

SOURCE_DOCUMENT_ID = UUID("00000000-0000-0000-0000-000000000401")
SOURCE_VERSION_ID = UUID("00000000-0000-0000-0000-000000000501")
SUMMARY_ID = UUID("00000000-0000-0000-0000-000000000601")
PROFILE_ID = UUID("00000000-0000-0000-0000-000000000701")
TIMESTAMP = datetime(2026, 8, 5, 15, 0, tzinfo=UTC)


class StubSummaryService:
    def __init__(self, result: GetVisibleEpisodeSummaryResult) -> None:
        self._result = result
        self.queries = []

    def execute(self, query) -> GetVisibleEpisodeSummaryResult:
        self.queries.append(query)
        return self._result


class StubTranscriptReader:
    def __init__(self, segments: tuple[TranscriptSegment, ...]) -> None:
        self._segments = segments
        self.episodes = []

    def get_active_reviewed_segments(self, episode) -> tuple[TranscriptSegment, ...]:
        self.episodes.append(episode)
        return self._segments


def episode_summary() -> EpisodeSummaryDocument:
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


def transcript_segment(
    *,
    start_ms: int,
    end_ms: int,
    cue_number: int,
) -> TranscriptSegment:
    episode = make_episode_ref()
    return TranscriptSegment(
        segment_id=UUID(int=cue_number),
        source_version_id=SOURCE_VERSION_ID,
        episode=episode,
        start_ms=start_ms,
        end_ms=end_ms,
        text=f"Transcript segment {cue_number}.",
        language=Language.ENGLISH,
        rights_status=RightsStatus.RESTRICTED,
    )


def context_service(
    summary_result: GetVisibleEpisodeSummaryResult,
    segments: tuple[TranscriptSegment, ...],
) -> tuple[GetVisibleEpisodeContextService, StubSummaryService, StubTranscriptReader]:
    summary_service = StubSummaryService(summary_result)
    transcript_reader = StubTranscriptReader(segments)
    service = GetVisibleEpisodeContextService(
        summary_service=summary_service,
        transcript_reader=transcript_reader,
        spoiler_policy=SpoilerPolicy(),
    )
    return service, summary_service, transcript_reader


def fully_watched_profile() -> ProfileWatchState:
    episode = make_episode_ref()
    return ProfileWatchState(
        profile_id=PROFILE_ID,
        profile_name="Maya",
        series_watch_states=(
            SeriesWatchState(
                series_id=episode.series_id,
                episode_progress=(
                    EpisodeWatchProgress(episode=episode, is_completed=True),
                ),
            ),
        ),
    )


def partially_watched_profile(safe_until_ms: int) -> ProfileWatchState:
    episode = make_episode_ref()
    return ProfileWatchState(
        profile_id=PROFILE_ID,
        profile_name="Maya",
        series_watch_states=(
            SeriesWatchState(
                series_id=episode.series_id,
                episode_progress=(
                    EpisodeWatchProgress(
                        episode=episode,
                        safe_until_ms=safe_until_ms,
                    ),
                ),
            ),
        ),
    )


def query(
    profile_watch_state: ProfileWatchState | None,
    *,
    episode=None,
    corpus_access_scope=None,
) -> GetVisibleEpisodeContextQuery:
    return GetVisibleEpisodeContextQuery(
        episode=episode or make_episode_ref(),
        summary_source_document_id=SOURCE_DOCUMENT_ID,
        profile_watch_state=profile_watch_state,
        corpus_access_scope=(
            corpus_access_scope or make_authenticated_corpus_access_scope()
        ),
    )


def test_fully_watched_episode_returns_all_reviewed_segments() -> None:
    summary = episode_summary()
    segments = (
        transcript_segment(start_ms=10_000, end_ms=15_000, cue_number=1),
        transcript_segment(start_ms=31_000, end_ms=35_000, cue_number=2),
    )
    service, summary_service, transcript_reader = context_service(
        GetVisibleEpisodeSummaryResult(summary=summary),
        segments,
    )

    result = service.execute(query(fully_watched_profile()))

    assert result.summary == summary
    assert result.transcript_segments == segments
    assert result.safe_until_ms is None
    assert result.summary_is_model_context_only is False
    assert summary_service.queries[0].source_document_id == SOURCE_DOCUMENT_ID
    assert transcript_reader.episodes == [summary.episode]


def test_partial_watch_returns_only_segments_ending_at_or_before_cutoff() -> None:
    safe_until_ms = 32_000
    summary = episode_summary()
    before_cutoff = transcript_segment(start_ms=10_000, end_ms=15_000, cue_number=1)
    at_cutoff = transcript_segment(start_ms=25_000, end_ms=32_000, cue_number=2)
    crossing_cutoff = transcript_segment(start_ms=31_000, end_ms=35_000, cue_number=3)
    after_cutoff = transcript_segment(start_ms=35_000, end_ms=40_000, cue_number=4)
    service, _summary_service, _transcript_reader = context_service(
        GetVisibleEpisodeSummaryResult(
            summary=summary,
            safe_until_ms=safe_until_ms,
            is_model_context_only=True,
        ),
        (before_cutoff, at_cutoff, crossing_cutoff, after_cutoff),
    )

    result = service.execute(query(partially_watched_profile(safe_until_ms)))

    assert result.summary == summary
    assert result.transcript_segments == (before_cutoff, at_cutoff)
    assert result.safe_until_ms == safe_until_ms
    assert result.summary_is_model_context_only is True


def test_unwatched_episode_returns_no_summary_or_segments() -> None:
    summary = episode_summary()
    segments = (transcript_segment(start_ms=10_000, end_ms=15_000, cue_number=1),)
    service, _summary_service, _transcript_reader = context_service(
        GetVisibleEpisodeSummaryResult(summary=summary),
        segments,
    )

    result = service.execute(query(None))

    assert result.summary is None
    assert result.transcript_segments == ()
    assert result.safe_until_ms is None
    assert result.summary_is_model_context_only is False


def test_unavailable_summary_does_not_hide_visible_transcript_segments() -> None:
    segments = (transcript_segment(start_ms=10_000, end_ms=15_000, cue_number=1),)
    service, _summary_service, _transcript_reader = context_service(
        GetVisibleEpisodeSummaryResult(summary=None),
        segments,
    )

    result = service.execute(query(fully_watched_profile()))

    assert result.summary is None
    assert result.transcript_segments == segments
    assert result.summary_is_model_context_only is False


def test_guest_scope_rejects_season_three_before_private_ports_are_read() -> None:
    service, summary_service, transcript_reader = context_service(
        GetVisibleEpisodeSummaryResult(summary=episode_summary()),
        (transcript_segment(start_ms=10_000, end_ms=15_000, cue_number=1),),
    )

    result = service.execute(
        query(
            fully_watched_profile(),
            episode=make_episode_ref(season_number=3),
            corpus_access_scope=make_guest_corpus_access_scope(),
        )
    )

    assert result.transcript_segments == ()
    assert result.summary is None
    assert summary_service.queries == []
    assert transcript_reader.episodes == []
