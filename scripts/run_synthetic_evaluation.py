"""Run a deterministic retrieval gate using invented, in-repository evidence."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

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
from cinegraph.domain.enums.enum import CorpusAccessMode, Language, RightsStatus
from cinegraph.domain.models.access import CorpusAccessScope
from cinegraph.domain.models.watch_state import EpisodePosition, EpisodeRef
from cinegraph.ports.retrieval import RetrievedSegment

FIXTURE = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "synthetic_retrieval_evaluation.json"


def _episode(series_id: UUID, season: int, number: int) -> EpisodeRef:
    return EpisodeRef(
        series_id=series_id,
        season_id=uuid5(NAMESPACE_URL, f"cinegraph:synthetic:season:{season}"),
        episode_id=uuid5(NAMESPACE_URL, f"cinegraph:synthetic:episode:{season}:{number}"),
        position=EpisodePosition(season_number=season, episode_number=number),
    )


class _DeterministicSearch:
    def __init__(self, matches: dict[str, RetrievedSegment]) -> None:
        self._matches = matches

    def execute(self, query: Any) -> SearchVisibleHybridSegmentsResult:
        return SearchVisibleHybridSegmentsResult(
            matches=(self._matches[query.query],),
            visible_episode_count=len(query.candidate_episodes),
        )


def main() -> None:
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    series_id = UUID(fixture["series_id"])
    cases: list[RetrievalEvaluationCase] = []
    matches: dict[str, RetrievedSegment] = {}
    for item in fixture["cases"]:
        target = _episode(series_id, item["season_number"], item["episode_number"])
        candidates = tuple(
            _episode(series_id, 1, number) for number in (1, 2)
        )
        forbidden = frozenset(
            _episode(series_id, 1, number).episode_id
            for number in item["forbidden_episode_numbers"]
        )
        cases.append(
            RetrievalEvaluationCase(
                case_id=item["case_id"],
                query=item["query"],
                series_id=series_id,
                candidate_episodes=candidates,
                expected_episode_ids=frozenset({target.episode_id}),
                forbidden_episode_ids=forbidden,
                corpus_access_scope=CorpusAccessScope(
                    mode=CorpusAccessMode.AUTHENTICATED,
                    revision="synthetic-fixture-v1",
                    allowed_seasons=frozenset(),
                    unrestricted=True,
                ),
                limit=2,
            )
        )
        matches[item["query"]] = RetrievedSegment(
            segment_id=uuid5(NAMESPACE_URL, f"cinegraph:synthetic:segment:{item['case_id']}"),
            source_version_id=uuid5(NAMESPACE_URL, "cinegraph:synthetic:source:v1"),
            episode=target,
            start_ms=0,
            end_ms=1000,
            text=item["evidence_text"],
            language=Language.ENGLISH,
            rights_status=RightsStatus.ALLOWED,
            score=1.0,
        )

    report = RetrievalEvaluationService(_DeterministicSearch(matches)).execute(
        RetrievalEvaluationDataset(schema_version=fixture["schema_version"], cases=tuple(cases))
    )
    output = {
        "case_count": len(report.case_results),
        "hit_rate": report.hit_rate,
        "mean_reciprocal_rank": report.mean_reciprocal_rank,
        "forbidden_episode_leak_count": report.forbidden_episode_leak_count,
        "passed": report.passed,
    }
    print(json.dumps(output, sort_keys=True))
    if not report.passed:
        raise SystemExit("Synthetic retrieval evaluation failed")


if __name__ == "__main__":
    main()
