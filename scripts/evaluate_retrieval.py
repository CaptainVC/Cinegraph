from __future__ import annotations

import argparse
import json
from pathlib import Path

from cinegraph.adapters.catalogue import JsonCatalogueManifestLoader
from cinegraph.adapters.evaluation import JsonRetrievalEvaluationDatasetLoader
from cinegraph.application.service.retrieval_evaluation_service import (
    RetrievalEvaluationService,
)
from cinegraph.bootstrap import CinegraphCompositionRoot
from cinegraph.config import (
    DEFAULT_RETRIEVAL_EVALUATION_THRESHOLDS,
    CinegraphRuntimeSettings,
    RetrievalEvaluationThresholds,
)


def _parser() -> argparse.ArgumentParser:
    defaults = DEFAULT_RETRIEVAL_EVALUATION_THRESHOLDS
    parser = argparse.ArgumentParser(
        description="Evaluate hybrid retrieval quality and access leakage."
    )
    parser.add_argument("--catalogue-manifest", required=True, type=Path)
    parser.add_argument("--dataset", required=True, type=Path)
    parser.add_argument("--env-file", default=Path(".env"), type=Path)
    parser.add_argument("--minimum-hit-rate", default=defaults.minimum_hit_rate, type=float)
    parser.add_argument(
        "--minimum-mrr",
        default=defaults.minimum_mean_reciprocal_rank,
        type=float,
    )
    parser.add_argument(
        "--maximum-leaks",
        default=defaults.maximum_forbidden_episode_leaks,
        type=int,
    )
    return parser


def main() -> None:
    arguments = _parser().parse_args()
    manifest = JsonCatalogueManifestLoader().load(arguments.catalogue_manifest).manifest
    dataset = JsonRetrievalEvaluationDatasetLoader().load(manifest, arguments.dataset)
    runtime = CinegraphCompositionRoot(
        CinegraphRuntimeSettings(_env_file=arguments.env_file)
    )
    try:
        report = RetrievalEvaluationService(
            runtime.hybrid_search_service,
            RetrievalEvaluationThresholds(
                minimum_hit_rate=arguments.minimum_hit_rate,
                minimum_mean_reciprocal_rank=arguments.minimum_mrr,
                maximum_forbidden_episode_leaks=arguments.maximum_leaks,
            ),
        ).execute(dataset)
    finally:
        runtime.close()
    output = {
        "passed": report.passed,
        "case_count": len(report.case_results),
        "hit_rate": report.hit_rate,
        "mean_reciprocal_rank": report.mean_reciprocal_rank,
        "forbidden_episode_leak_count": report.forbidden_episode_leak_count,
        "cases": [
            {
                "case_id": item.case_id,
                "first_expected_rank": item.first_expected_rank,
                "leaked_episode_ids": sorted(str(value) for value in item.leaked_episode_ids),
            }
            for item in report.case_results
        ],
    }
    print(json.dumps(output, sort_keys=True))
    if not report.passed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
