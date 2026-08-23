from uuid import UUID

from tests.factories import make_authenticated_corpus_access_scope, make_episode_ref

from cinegraph.application.models.retrieval_evaluation import (
    RetrievalEvaluationCase,
    RetrievalEvaluationDataset,
)
from cinegraph.application.models.search_visible_hybrid_segments import (
    SearchVisibleHybridSegmentsResult,
)
from cinegraph.application.service.retrieval_evaluation_service import (
    RetrievalEvaluationService,
)
from cinegraph.config import RetrievalEvaluationThresholds
from cinegraph.config.transcript_chunking import TRANSCRIPT_INDEX_REVISION
from cinegraph.domain.enums.enum import Language, RightsStatus
from cinegraph.ports.retrieval import RetrievedSegment

SOURCE_VERSION_ID = UUID("00000000-0000-0000-0000-000000000701")


class FakeSearchService:
    def __init__(self, matches_by_query: dict[str, tuple[RetrievedSegment, ...]]) -> None:
        self._matches_by_query = matches_by_query
        self.queries = []

    def execute(self, query) -> SearchVisibleHybridSegmentsResult:
        self.queries.append(query)
        return SearchVisibleHybridSegmentsResult(
            matches=self._matches_by_query[query.query],
            visible_episode_count=len(query.candidate_episodes),
        )


def make_match(episode, segment_id: int, score: float) -> RetrievedSegment:
    return RetrievedSegment(
        segment_id=UUID(int=segment_id),
        source_version_id=SOURCE_VERSION_ID,
        episode=episode,
        start_ms=1_000,
        end_ms=2_000,
        text="Relevant evidence.",
        language=Language.ENGLISH,
        rights_status=RightsStatus.ALLOWED,
        score=score,
        member_segment_ids=(UUID(int=segment_id + 1000),),
        index_revision=TRANSCRIPT_INDEX_REVISION,
        ordinal=0,
    )


def make_case(
    case_id: str,
    query: str,
    candidates,
    expected,
    forbidden=(),
) -> RetrievalEvaluationCase:
    return RetrievalEvaluationCase(
        case_id=case_id,
        query=query,
        series_id=candidates[0].series_id,
        candidate_episodes=tuple(candidates),
        expected_episode_ids=frozenset(item.episode_id for item in expected),
        forbidden_episode_ids=frozenset(item.episode_id for item in forbidden),
        corpus_access_scope=make_authenticated_corpus_access_scope(),
        limit=5,
    )


def test_report_measures_rank_misses_and_forbidden_leaks() -> None:
    first = make_episode_ref(episode_id=UUID(int=1), episode_number=1)
    second = make_episode_ref(episode_id=UUID(int=2), episode_number=2)
    third = make_episode_ref(episode_id=UUID(int=3), episode_number=3)
    cases = (
        make_case("rank-two", "rank two", (first, second), (second,)),
        make_case("miss", "miss", (first, second), (second,)),
        make_case("leak", "leak", (first, third), (first,), (third,)),
    )
    search = FakeSearchService(
        {
            "rank two": (make_match(first, 1, 0.9), make_match(second, 2, 0.8)),
            "miss": (make_match(first, 3, 0.7),),
            "leak": (make_match(first, 4, 0.9), make_match(third, 5, 0.5)),
        }
    )
    service = RetrievalEvaluationService(
        search,
        RetrievalEvaluationThresholds(0.60, 0.40, 0),
    )

    report = service.execute(RetrievalEvaluationDataset(1, cases))

    assert report.hit_rate == 2 / 3
    assert report.mean_reciprocal_rank == 0.5
    assert report.forbidden_episode_leak_count == 1
    assert report.passed is False
    assert report.case_results[0].first_expected_rank == 2
    assert report.case_results[1].first_expected_rank is None
    assert report.case_results[2].leaked_episode_ids == frozenset({third.episode_id})
    assert all(query.profile_watch_state is not None for query in search.queries)


def test_report_passes_when_every_expected_episode_ranks_first_without_leaks() -> None:
    episode = make_episode_ref()
    case = make_case("pass", "pass", (episode,), (episode,))
    search = FakeSearchService({"pass": (make_match(episode, 10, 1.0),)})

    report = RetrievalEvaluationService(search).execute(RetrievalEvaluationDataset(1, (case,)))

    assert report.hit_rate == 1.0
    assert report.mean_reciprocal_rank == 1.0
    assert report.forbidden_episode_leak_count == 0
    assert report.passed is True


def test_rank_metrics_count_each_episode_only_once() -> None:
    first = make_episode_ref(episode_id=UUID(int=11), episode_number=1)
    expected = make_episode_ref(episode_id=UUID(int=12), episode_number=2)
    case = make_case("deduplicated", "deduplicated", (first, expected), (expected,))
    search = FakeSearchService(
        {
            "deduplicated": (
                make_match(first, 20, 1.0),
                make_match(first, 21, 0.9),
                make_match(expected, 22, 0.8),
            )
        }
    )

    report = RetrievalEvaluationService(
        search,
        RetrievalEvaluationThresholds(0.0, 0.0, 0, 0.0, 0.0),
    ).execute(RetrievalEvaluationDataset(1, (case,)))

    assert report.case_results[0].retrieved_episode_ids == (
        first.episode_id,
        expected.episode_id,
    )
    assert report.case_results[0].first_expected_rank == 2
    assert report.mean_reciprocal_rank == 0.5
