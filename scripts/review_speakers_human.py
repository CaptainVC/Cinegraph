from __future__ import annotations

import argparse
import json
from pathlib import Path

from cinegraph.config import DEFAULT_SPEAKER_REVIEW_CONFIGURATION
from cinegraph.ingestion.speaker_review.human_review import (
    HumanSpeakerReviewWorkflow,
)
from cinegraph.ingestion.speaker_review.workflow import load_run_state


def _workflow() -> HumanSpeakerReviewWorkflow:
    return HumanSpeakerReviewWorkflow(DEFAULT_SPEAKER_REVIEW_CONFIGURATION)


def _prepare(arguments: argparse.Namespace) -> None:
    result = _workflow().prepare_workbench(arguments.run_directory)
    print(
        json.dumps(
            {
                "candidate_count": result.candidate_count,
                "queue_sha256": result.queue_sha256,
                "workbench": str(result.path),
            },
            sort_keys=True,
        )
    )


def _apply(arguments: argparse.Namespace) -> None:
    result = _workflow().apply_resolution(
        run_directory=arguments.run_directory,
        resolution_path=arguments.resolution,
    )
    print(
        json.dumps(
            {
                "accepted_by_human": result.state.accepted_by_human,
                "needs_human": result.state.needs_human,
                "resolution_count": result.resolution_count,
                "reviewed_files": len(result.records),
                "status": result.state.status.value,
            },
            sort_keys=True,
        )
    )


def _status(arguments: argparse.Namespace) -> None:
    state = load_run_state(arguments.run_directory / "run-state.json")
    print(
        json.dumps(
            {
                "accepted_by_human": state.accepted_by_human,
                "needs_human": state.needs_human,
                "run_id": state.run_id,
                "status": state.status.value,
            },
            sort_keys=True,
        )
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Prepare and apply private human speaker-review resolutions."
    )
    subparsers = parser.add_subparsers(required=True)

    prepare_parser = subparsers.add_parser("prepare")
    prepare_parser.add_argument("run_directory", type=Path)
    prepare_parser.set_defaults(handler=_prepare)

    apply_parser = subparsers.add_parser("apply")
    apply_parser.add_argument("run_directory", type=Path)
    apply_parser.add_argument("resolution", type=Path)
    apply_parser.set_defaults(handler=_apply)

    status_parser = subparsers.add_parser("status")
    status_parser.add_argument("run_directory", type=Path)
    status_parser.set_defaults(handler=_status)

    arguments = parser.parse_args()
    try:
        arguments.handler(arguments)
    except (
        FileExistsError,
        FileNotFoundError,
        RuntimeError,
        TypeError,
        ValueError,
    ) as error:
        raise SystemExit(str(error)) from None


if __name__ == "__main__":
    main()
