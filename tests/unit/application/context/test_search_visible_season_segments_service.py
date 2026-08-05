from uuid import UUID

import pytest

from cinegraph.application.models.search_visible_season_segments import (
    SearchVisibleSeasonSegmentsQuery,
)
from cinegraph.application.service.search_visible_season_segments_service import (
    SearchVisibleSeasonSegmentsService,
)
from cinegraph.common.error_messages import RetrievalErrorMessages
from cinegraph.domain.enums.enum import Language, RightsStatus
from cinegraph.domain.models.transcript.transcript_segment import TranscriptSegment
from cinegraph.domain.models.watch_state.episode_watch_state import (
    EpisodeRef,
    EpisodeWatchProgress,
)
from cinegraph.domain.models.watch_state.profile_watch_state import (
    ProfileWatchState,
)
from cinegraph.domain.models.watch_state.series_watch_state import (
    SeriesWatchState,
)
from cinegraph.domain.policy.spoiler_policy import SpoilerPolicy
from tests.factories import make_episode_ref


PROFILE_ID = UUID("00000000-0000-0000-0000-000000000701")
SOURCE_VERSION_ID = UUID("00000000-0000-0000-0000-000000000501")
SAFE_UNTIL_MS = 32_000

episode_1 = make_episode_ref(episode_id=UUID(int=1), episode_number=1)
episode_2 = make_episode_ref(episode_id=UUID(int=2), episode_number=2)
episode_3 = make_episode_ref(episode_id=UUID(int=3), episode_number=3)


class StubSeasonEpisodeCatalog:
    def __init__(self, episode_refs: tuple[EpisodeRef, ...] | None) -> None:
        self._episode_refs = episode_refs

    def get_episode_refs(self, series_id, season_id):
        return self._episode_refs


class StubTranscriptSegmentReader:
    def __init__(self, segments_by_episode: dict[UUID, tuple[TranscriptSegment, ...]]) -> None:
        self._segments_by_episode = segments_by_episode

    def get_active_reviewed_segments(self, episode: EpisodeRef) -> tuple[TranscriptSegment, ...]:
        return self._segments_by_episode.get(episode.episode_id, ())


def segment(
    *,
    episode: EpisodeRef,
    segment_id: int,
    start_ms: int,
    end_ms: int,
    text: str,
) -> TranscriptSegment:
    return TranscriptSegment(
        segment_id=UUID(int=segment_id),
        source_version_id=SOURCE_VERSION_ID,
        episode=episode,
        start_ms=start_ms,
        end_ms=end_ms,
        text=text,
        language=Language.ENGLISH,
        rights_status=RightsStatus.RESTRICTED,
    )


def profile_watch_state() -> ProfileWatchState:
    return ProfileWatchState(
        profile_id=PROFILE_ID,
        profile_name="Maya",
        series_watch_states=(
            SeriesWatchState(
                series_id=episode_1.series_id,
                episode_progress=(
                    EpisodeWatchProgress(episode=episode_1, is_completed=True),
                    EpisodeWatchProgress(episode=episode_2, safe_until_ms=SAFE_UNTIL_MS),
                ),
            ),
        ),
    )


def query(*, search_query: str = "Luke banister", limit: int = 10) -> SearchVisibleSeasonSegmentsQuery:
    return SearchVisibleSeasonSegmentsQuery(
        query=search_query,
        series_id=episode_1.series_id,
        season_id=episode_1.season_id,
        profile_watch_state=profile_watch_state(),
        limit=limit,
    )


def test_season_search_respects_per_episode_watch_boundaries() -> None:
    episode_1_segment = segment(
        episode=episode_1,
        segment_id=1,
        start_ms=10_000,
        end_ms=15_000,
        text="Luke got stuck in the banister.",
    )
    episode_2_safe_segment = segment(
        episode=episode_2,
        segment_id=2,
        start_ms=25_000,
        end_ms=32_000,
        text="Luke is worried about the banister.",
    )
    episode_2_crossing_segment = segment(
        episode=episode_2,
        segment_id=3,
        start_ms=31_000,
        end_ms=35_000,
        text="Luke reveals a later banister detail.",
    )
    episode_3_segment = segment(
        episode=episode_3,
        segment_id=4,
        start_ms=10_000,
        end_ms=15_000,
        text="Luke has a future banister spoiler.",
    )

    catalogue = StubSeasonEpisodeCatalog((episode_1, episode_2, episode_3))
    transcript_reader = StubTranscriptSegmentReader(
        {
            episode_1.episode_id: (episode_1_segment,),
            episode_2.episode_id: (episode_2_safe_segment, episode_2_crossing_segment),
            episode_3.episode_id: (episode_3_segment,),
        }
    )
    service = SearchVisibleSeasonSegmentsService(
        catalogue=catalogue,
        transcript_reader=transcript_reader,
        spoiler_policy=SpoilerPolicy(),
    )

    result = service.execute(query())

    matched_segments = {match.segment for match in result.matches}
    assert matched_segments == {episode_1_segment, episode_2_safe_segment}
    assert episode_2_crossing_segment not in matched_segments
    assert episode_3_segment not in matched_segments


def test_season_search_orders_by_score_then_episode_position() -> None:
    weak_match = segment(
        episode=episode_1,
        segment_id=1,
        start_ms=10_000,
        end_ms=15_000,
        text="Luke is having breakfast.",
    )
    strong_match = segment(
        episode=episode_2,
        segment_id=2,
        start_ms=5_000,
        end_ms=10_000,
        text="Luke got stuck in the banister.",
    )
    catalogue = StubSeasonEpisodeCatalog((episode_1, episode_2))
    transcript_reader = StubTranscriptSegmentReader(
        {
            episode_1.episode_id: (weak_match,),
            episode_2.episode_id: (strong_match,),
        }
    )
    service = SearchVisibleSeasonSegmentsService(
        catalogue=catalogue,
        transcript_reader=transcript_reader,
        spoiler_policy=SpoilerPolicy(),
    )

    result = service.execute(query(search_query="Luke stuck banister"))

    assert tuple(match.segment for match in result.matches) == (
        strong_match,
        weak_match,
    )


def test_season_search_applies_limit() -> None:
    first_segment = segment(
        episode=episode_1,
        segment_id=1,
        start_ms=1_000,
        end_ms=2_000,
        text="Luke banister one.",
    )
    second_segment = segment(
        episode=episode_1,
        segment_id=2,
        start_ms=3_000,
        end_ms=4_000,
        text="Luke banister two.",
    )
    catalogue = StubSeasonEpisodeCatalog((episode_1,))
    transcript_reader = StubTranscriptSegmentReader(
        {episode_1.episode_id: (first_segment, second_segment)}
    )
    service = SearchVisibleSeasonSegmentsService(
        catalogue=catalogue,
        transcript_reader=transcript_reader,
        spoiler_policy=SpoilerPolicy(),
    )

    result = service.execute(query(search_query="Luke banister", limit=1))

    assert len(result.matches) == 1


def test_season_search_returns_no_matches_for_unknown_season() -> None:
    catalogue = StubSeasonEpisodeCatalog(None)
    transcript_reader = StubTranscriptSegmentReader({})
    service = SearchVisibleSeasonSegmentsService(
        catalogue=catalogue,
        transcript_reader=transcript_reader,
        spoiler_policy=SpoilerPolicy(),
    )

    result = service.execute(query())

    assert result.matches == ()


def test_season_search_rejects_non_positive_limit() -> None:
    catalogue = StubSeasonEpisodeCatalog((episode_1,))
    transcript_reader = StubTranscriptSegmentReader({})
    service = SearchVisibleSeasonSegmentsService(
        catalogue=catalogue,
        transcript_reader=transcript_reader,
        spoiler_policy=SpoilerPolicy(),
    )

    with pytest.raises(ValueError, match=RetrievalErrorMessages.SEARCH_LIMIT_MUST_BE_POSITIVE):
        service.execute(query(limit=0))
