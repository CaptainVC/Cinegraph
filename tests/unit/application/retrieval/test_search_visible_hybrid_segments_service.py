from typing import Any
from uuid import UUID

import pytest
from tests.factories import (
    make_episode_ref,
    make_guest_corpus_access_scope,
)

from cinegraph.application.models.search_visible_hybrid_segments import (
    SearchVisibleHybridSegmentsQuery,
)
from cinegraph.application.service.search_visible_hybrid_segments_service import (
    SearchVisibleHybridSegmentsService,
)
from cinegraph.common.error_messages import RetrievalErrorMessages
from cinegraph.domain.enums.enum import Language, RightsStatus, SpoilerMode
from cinegraph.domain.exceptions.errors import InvalidModelError
from cinegraph.domain.models.watch_state import EpisodeRef, ProfileWatchState
from cinegraph.domain.policy.spoiler_policy import SpoilerPolicy
from cinegraph.domain.retrieval import (
    DenseVector,
    HybridVector,
    QueryVector,
    RetrievalScope,
    RetrievalScopeCompiler,
    SparseVector,
)
from cinegraph.ports.retrieval import RetrievedSegment

SERIES_ID = UUID("00000000-0000-0000-0000-000000000011")
SOURCE_VERSION_ID = UUID("00000000-0000-0000-0000-000000000701")


class RecordingEncoder:
    def __init__(self) -> None:
        self.texts: list[str] = []
        self.query_vector = QueryVector(
            HybridVector(DenseVector((0.5,)), SparseVector((1,), (1.0,)))
        )

    def encode_query(self, text: str) -> QueryVector:
        self.texts.append(text)
        return self.query_vector


class RecordingVectorIndex:
    def __init__(self, matches: tuple[RetrievedSegment, ...] = ()) -> None:
        self.matches = matches
        self.calls: list[dict[str, Any]] = []

    def search_hybrid(
        self,
        query: QueryVector,
        scope: RetrievalScope,
        limit: int,
    ) -> tuple[RetrievedSegment, ...]:
        self.calls.append({"query": query, "scope": scope, "limit": limit})
        return self.matches


def make_match(
    episode: EpisodeRef,
    *,
    segment_id: UUID = UUID(int=2001),
    end_ms: int = 2_000,
    score: float = 0.8,
) -> RetrievedSegment:
    return RetrievedSegment(
        segment_id=segment_id,
        source_version_id=SOURCE_VERSION_ID,
        episode=episode,
        start_ms=1_000,
        end_ms=end_ms,
        text="Claire says hello.",
        language=Language.ENGLISH,
        rights_status=RightsStatus.ALLOWED,
        score=score,
    )


def make_query(
    episodes: tuple[EpisodeRef, ...],
    *,
    query: str = "family dinner",
    limit: int = 5,
) -> SearchVisibleHybridSegmentsQuery:
    return SearchVisibleHybridSegmentsQuery(
        query=query,
        series_id=SERIES_ID,
        candidate_episodes=episodes,
        profile_watch_state=ProfileWatchState(
            profile_id=UUID(int=1),
            profile_name="Guest",
            spoiler_mode=SpoilerMode.RELAXED,
        ),
        corpus_access_scope=make_guest_corpus_access_scope(),
        limit=limit,
    )


def make_service(
    matches: tuple[RetrievedSegment, ...] = (),
) -> tuple[SearchVisibleHybridSegmentsService, RecordingEncoder, RecordingVectorIndex]:
    encoder = RecordingEncoder()
    index = RecordingVectorIndex(matches)
    service = SearchVisibleHybridSegmentsService(
        RetrievalScopeCompiler(SpoilerPolicy()),
        encoder,
        index,
    )
    return service, encoder, index


def test_guest_scope_filters_private_season_before_encoding_and_search() -> None:
    season_three = make_episode_ref(season_number=3, episode_id=UUID(int=3))
    service, encoder, index = make_service()

    result = service.execute(make_query((season_three,)))

    assert result.matches == ()
    assert result.visible_episode_count == 0
    assert encoder.texts == []
    assert index.calls == []


def test_visible_scope_is_encoded_once_and_passed_to_index() -> None:
    season_one = make_episode_ref(season_number=1, episode_id=UUID(int=1))
    season_two = make_episode_ref(season_number=2, episode_id=UUID(int=2))
    match = make_match(season_two)
    service, encoder, index = make_service((match,))

    result = service.execute(make_query((season_one, season_two), limit=7))

    assert result.matches == (match,)
    assert result.visible_episode_count == 2
    assert encoder.texts == ["family dinner"]
    assert len(index.calls) == 1
    assert index.calls[0]["query"] is encoder.query_vector
    assert index.calls[0]["limit"] == 7
    assert tuple(item.episode for item in index.calls[0]["scope"].episode_scopes) == (
        season_one,
        season_two,
    )


@pytest.mark.parametrize("query_text", ["", " family", "family "])
def test_invalid_query_rejects_before_dependencies(query_text: str) -> None:
    episode = make_episode_ref()
    service, encoder, index = make_service()

    with pytest.raises(
        ValueError,
        match=RetrievalErrorMessages.SEARCH_QUERY_MUST_BE_TRIMMED_NONEMPTY,
    ):
        service.execute(make_query((episode,), query=query_text))

    assert encoder.texts == []
    assert index.calls == []


def test_duplicate_candidate_episode_rejects_before_dependencies() -> None:
    episode = make_episode_ref()
    service, encoder, index = make_service()

    with pytest.raises(
        ValueError,
        match=RetrievalErrorMessages.CANDIDATE_EPISODE_IDS_MUST_BE_UNIQUE,
    ):
        service.execute(make_query((episode, episode)))

    assert encoder.texts == []
    assert index.calls == []


def test_result_outside_compiled_scope_is_rejected() -> None:
    visible = make_episode_ref(season_number=1, episode_id=UUID(int=1))
    private = make_episode_ref(season_number=3, episode_id=UUID(int=3))
    service, _, _ = make_service((make_match(private),))

    with pytest.raises(
        InvalidModelError,
        match=RetrievalErrorMessages.VECTOR_INDEX_RESULT_MUST_MATCH_SCOPE,
    ):
        service.execute(make_query((visible,)))


def test_duplicate_or_excess_backend_results_are_rejected() -> None:
    episode = make_episode_ref()
    duplicate = make_match(episode)
    service, _, _ = make_service((duplicate, duplicate))

    with pytest.raises(
        InvalidModelError,
        match=RetrievalErrorMessages.VECTOR_INDEX_RESULT_COUNT_MUST_NOT_EXCEED_LIMIT,
    ):
        service.execute(make_query((episode,), limit=1))

    service, _, _ = make_service((duplicate, duplicate))
    with pytest.raises(
        InvalidModelError,
        match=RetrievalErrorMessages.VECTOR_INDEX_RESULT_IDS_MUST_BE_UNIQUE,
    ):
        service.execute(make_query((episode,), limit=2))
