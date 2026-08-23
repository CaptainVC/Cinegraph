from uuid import UUID

import pytest
from tests.factories import DEFAULT_SERIES_ID, make_authenticated_corpus_access_scope

from cinegraph.adapters.workflow.langgraph.episode_recommendation_graph import (
    EpisodeRecommendationGraphWorkflow,
)
from cinegraph.application.models.episode_recommendation import (
    RankedRecommendationDraft,
    RecommendEpisodesQuery,
)
from cinegraph.application.models.search_visible_hybrid_segments import (
    SearchVisibleHybridSegmentsResult,
)
from cinegraph.application.service.episode_recommendation_service import (
    EpisodeRecommendationService,
)
from cinegraph.common.error_messages import RecommendationErrorMessages
from cinegraph.config.transcript_chunking import TRANSCRIPT_INDEX_REVISION
from cinegraph.domain.enums.enum import (
    Language,
    RightsStatus,
    SpoilerMode,
    WatchPreference,
)
from cinegraph.domain.models.catalogue.catalogue_manifest import CatalogueManifest
from cinegraph.domain.models.catalogue.episode import Episode
from cinegraph.domain.models.catalogue.season import Season
from cinegraph.domain.models.catalogue.series import Series
from cinegraph.domain.models.watch_state import ProfileWatchState
from cinegraph.ports.retrieval import RetrievedSegment


class RecordingSearch:
    def __init__(self, matches: tuple[RetrievedSegment, ...]) -> None:
        self.matches = matches
        self.queries = []

    def execute(self, query):
        self.queries.append(query)
        return SearchVisibleHybridSegmentsResult(
            matches=tuple(
                item for item in self.matches if item.episode in query.candidate_episodes
            ),
            visible_episode_count=len(query.candidate_episodes),
        )


class CandidateBoundRanker:
    def __init__(self) -> None:
        self.requests = []

    def rank(self, request):
        self.requests.append(request)
        candidate = request.candidates[0]
        return (
            RankedRecommendationDraft(
                episode_id=candidate.episode.episode_id,
                score=0.91,
                reason="The visible dialogue matches the requested warm mood.",
                cited_segment_ids=(candidate.evidence[0].segment_id,),
            ),
        )


def make_catalogue() -> CatalogueManifest:
    seasons = []
    for number, runtime, synopsis in (
        (1, 1_200, "A warm family dinner."),
        (2, 1_500, "A playful family competition."),
        (3, 1_800, "A storm disrupts a trip."),
    ):
        season_id = UUID(int=100 + number)
        seasons.append(
            Season(
                series_id=DEFAULT_SERIES_ID,
                season_id=season_id,
                season_number=number,
                episodes=(
                    Episode(
                        series_id=DEFAULT_SERIES_ID,
                        season_id=season_id,
                        episode_id=UUID(int=1_000 + number),
                        episode_number=1,
                        episode_title=f"Episode {number}",
                        synopsis=synopsis,
                        runtime_seconds=runtime,
                    ),
                ),
            )
        )
    return CatalogueManifest(1, (Series(DEFAULT_SERIES_ID, "Example", tuple(seasons)),))


def make_segment(episode, *, text: str, identifier: int) -> RetrievedSegment:
    return RetrievedSegment(
        segment_id=UUID(int=identifier),
        source_version_id=UUID(int=identifier + 100),
        episode=episode,
        start_ms=1_000,
        end_ms=2_000,
        text=text,
        language=Language.ENGLISH,
        rights_status=RightsStatus.ALLOWED,
        score=0.8 + identifier / 100_000,
        member_segment_ids=(UUID(int=identifier + 1000),),
        index_revision=TRANSCRIPT_INDEX_REVISION,
        ordinal=0,
    )


def make_query(catalogue: CatalogueManifest, **changes) -> RecommendEpisodesQuery:
    query = {
        "series_id": DEFAULT_SERIES_ID,
        "mood": "warm and playful",
        "characters": ("Alex",),
        "excluded_themes": (),
        "watch_preference": WatchPreference.ANY,
        "requested_count": 2,
        "profile_watch_state": ProfileWatchState(
            profile_id=UUID(int=50),
            profile_name="Viewer",
            spoiler_mode=SpoilerMode.RELAXED,
        ),
        "corpus_access_scope": make_authenticated_corpus_access_scope(),
        "maximum_runtime_seconds": None,
    }
    query.update(changes)
    return RecommendEpisodesQuery(**query)


def test_graph_ranks_only_filtered_candidates_and_visible_evidence() -> None:
    catalogue = make_catalogue()
    refs = catalogue.episode_refs()
    search = RecordingSearch(
        (
            make_segment(refs[0], text="The family shares a warm meal.", identifier=10),
            make_segment(refs[1], text="Alex turns the game into a joke.", identifier=20),
            make_segment(refs[2], text="The storm grows louder.", identifier=30),
        )
    )
    ranker = CandidateBoundRanker()
    workflow = EpisodeRecommendationGraphWorkflow(
        EpisodeRecommendationService(catalogue, search, ranker)
    )

    result = workflow.execute(
        make_query(
            catalogue,
            excluded_themes=("storm",),
            maximum_runtime_seconds=1_500,
        )
    )

    assert result.visible_candidate_count == 2
    assert len(result.recommendations) == 1
    assert result.recommendations[0].episode == refs[1]
    assert result.recommendations[0].citations[0].segment_id == UUID(int=20)
    assert tuple(item.episode for item in ranker.requests[0].candidates) == (
        refs[1],
        refs[0],
    )


def test_watch_preference_is_a_hard_filter_before_retrieval() -> None:
    catalogue = make_catalogue()
    refs = catalogue.episode_refs()
    state = ProfileWatchState(
        profile_id=UUID(int=51),
        profile_name="Viewer",
        spoiler_mode=SpoilerMode.RELAXED,
    ).mark_episode_watched(refs[0])
    search = RecordingSearch(
        tuple(
            make_segment(item, text="Visible evidence.", identifier=40 + index)
            for index, item in enumerate(refs)
        )
    )
    service = EpisodeRecommendationService(catalogue, search, CandidateBoundRanker())

    candidates = service.filter_candidates(
        make_query(
            catalogue,
            profile_watch_state=state,
            watch_preference=WatchPreference.UNWATCHED,
        )
    )

    assert tuple(item.episode for item in candidates) == refs[1:]


def test_rejects_ranker_episode_or_citation_injection() -> None:
    catalogue = make_catalogue()
    episode = catalogue.episode_refs()[0]
    segment = make_segment(episode, text="Visible evidence.", identifier=80)
    service = EpisodeRecommendationService(
        catalogue,
        RecordingSearch((segment,)),
        CandidateBoundRanker(),
    )
    query = make_query(catalogue)
    candidate = service.retrieve_candidate_evidence(
        query,
        service.filter_candidates(query),
    )[0]

    with pytest.raises(
        ValueError,
        match=RecommendationErrorMessages.RANKER_RESULT_MUST_REFERENCE_CANDIDATE,
    ):
        service.validate_ranked_candidates(
            query,
            (candidate,),
            (
                RankedRecommendationDraft(
                    UUID(int=999),
                    0.9,
                    "Injected recommendation.",
                    (segment.segment_id,),
                ),
            ),
            visible_candidate_count=1,
        )

    with pytest.raises(
        ValueError,
        match=RecommendationErrorMessages.RANKER_CITATIONS_MUST_BE_VISIBLE,
    ):
        service.validate_ranked_candidates(
            query,
            (candidate,),
            (
                RankedRecommendationDraft(
                    episode.episode_id,
                    0.9,
                    "Recommendation with a foreign citation.",
                    (UUID(int=998),),
                ),
            ),
            visible_candidate_count=1,
        )
