from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from openai import OpenAI

from cinegraph.adapters.llm.openai_speaker_review_batch_gateway import (
    OpenAISpeakerReviewBatchGateway,
)
from cinegraph.adapters.workflow.langgraph.speaker_review_graph import (
    SpeakerReviewGraphWorkflow,
)
from cinegraph.config import (
    DEFAULT_SPEAKER_REVIEW_CONFIGURATION,
    OpenAISettings,
)
from cinegraph.domain.models.transcript import TERMINAL_SPEAKER_REVIEW_RUN_STATUSES
from cinegraph.ingestion.speaker_review.workflow import (
    SpeakerReviewWorkflow,
    load_run_state,
)


def _workflow(env_file: Path) -> SpeakerReviewGraphWorkflow:
    settings = OpenAISettings(_env_file=env_file)
    client = OpenAI(api_key=settings.openai_api_key.get_secret_value())
    review_workflow = SpeakerReviewWorkflow(
        gateway=OpenAISpeakerReviewBatchGateway(
            client,
            DEFAULT_SPEAKER_REVIEW_CONFIGURATION,
        ),
        configuration=DEFAULT_SPEAKER_REVIEW_CONFIGURATION,
        primary_model=settings.speaker_review_model,
        adjudication_model=settings.speaker_adjudication_model,
        final_review_model=settings.speaker_final_review_model,
        primary_reasoning_effort=settings.speaker_review_reasoning_effort,
        adjudication_reasoning_effort=(
            settings.speaker_adjudication_reasoning_effort
        ),
        final_review_reasoning_effort=(
            settings.speaker_final_review_reasoning_effort
        ),
    )
    return SpeakerReviewGraphWorkflow(review_workflow)


def _summary(run_directory: Path, state) -> dict[str, object]:
    return {
        "run_directory": str(run_directory),
        "run_id": state.run_id,
        "status": state.status.value,
        "candidate_count": state.candidate_count,
        "primary_model": state.primary_model,
        "adjudication_model": state.adjudication_model,
        "final_review_model": state.final_review_model,
        "estimated_primary_cost_usd": round(
            state.estimated_primary_cost_usd,
            6,
        ),
        "actual_total_cost_usd": round(state.actual_total_cost_usd, 6),
        "accepted_by_consensus": state.accepted_by_consensus,
        "accepted_by_adjudication": state.accepted_by_adjudication,
        "accepted_by_final_review": state.accepted_by_final_review,
        "accepted_by_human": state.accepted_by_human,
        "needs_human": state.needs_human,
        "primary_parts": {
            "completed": state.primary_completed_part_count,
            "total": state.primary_part_count,
        },
        "adjudication_parts": {
            "completed": state.adjudication_completed_part_count,
            "total": state.adjudication_part_count,
        },
        "final_review_parts": {
            "completed": state.final_review_completed_part_count,
            "total": state.final_review_part_count,
        },
        "final_review_retry_count": state.final_review_retry_count,
    }


def _run(arguments: argparse.Namespace) -> None:
    workflow = _workflow(arguments.env_file)
    run_directory, state = workflow.start(
        corpus_root=arguments.corpus_root,
        seasons=tuple(arguments.seasons),
    )
    print(json.dumps(_summary(run_directory, state), sort_keys=True), flush=True)
    if not arguments.wait:
        return

    started = time.monotonic()
    prior_status = state.status
    while state.status not in TERMINAL_SPEAKER_REVIEW_RUN_STATUSES:
        if time.monotonic() - started > arguments.maximum_wait_seconds:
            raise TimeoutError("Speaker review wait limit expired; the run is resumable.")
        time.sleep(arguments.poll_interval_seconds)
        _, state = workflow.advance(run_directory)
        if state.status is not prior_status:
            print(json.dumps(_summary(run_directory, state), sort_keys=True), flush=True)
            prior_status = state.status
    print(json.dumps(_summary(run_directory, state), sort_keys=True), flush=True)


def _advance(arguments: argparse.Namespace) -> None:
    workflow = _workflow(arguments.env_file)
    _, state = workflow.advance(arguments.run_directory)
    print(json.dumps(_summary(arguments.run_directory, state), sort_keys=True))


def _submit(arguments: argparse.Namespace) -> None:
    workflow = _workflow(arguments.env_file)
    _, state = workflow.submit(arguments.run_directory)
    print(json.dumps(_summary(arguments.run_directory, state), sort_keys=True))


def _status(arguments: argparse.Namespace) -> None:
    state = load_run_state(arguments.run_directory / "run-state.json")
    print(json.dumps(_summary(arguments.run_directory, state), sort_keys=True))


def _final_review(arguments: argparse.Namespace) -> None:
    workflow = _workflow(arguments.env_file)
    _, state = workflow.final_review(arguments.run_directory)
    print(json.dumps(_summary(arguments.run_directory, state), sort_keys=True))


def _retry_incomplete(arguments: argparse.Namespace) -> None:
    workflow = _workflow(arguments.env_file)
    _, state = workflow.retry_incomplete(arguments.run_directory)
    print(json.dumps(_summary(arguments.run_directory, state), sort_keys=True))


def _reconcile_costs(arguments: argparse.Namespace) -> None:
    workflow = _workflow(arguments.env_file)
    _, state = workflow.reconcile_costs(arguments.run_directory)
    print(json.dumps(_summary(arguments.run_directory, state), sort_keys=True))


def _wait(arguments: argparse.Namespace) -> None:
    workflow = _workflow(arguments.env_file)
    state = load_run_state(arguments.run_directory / "run-state.json")
    print(json.dumps(_summary(arguments.run_directory, state), sort_keys=True))
    started = time.monotonic()
    prior_status = state.status
    while state.status not in TERMINAL_SPEAKER_REVIEW_RUN_STATUSES:
        if time.monotonic() - started > arguments.maximum_wait_seconds:
            raise TimeoutError("Speaker review wait limit expired; the run is resumable.")
        time.sleep(arguments.poll_interval_seconds)
        _, state = workflow.advance(arguments.run_directory)
        if state.status is not prior_status:
            print(json.dumps(_summary(arguments.run_directory, state), sort_keys=True))
            prior_status = state.status
    print(json.dumps(_summary(arguments.run_directory, state), sort_keys=True))


def main() -> None:
    configuration = DEFAULT_SPEAKER_REVIEW_CONFIGURATION
    parser = argparse.ArgumentParser(
        description="Run resumable OpenAI Batch speaker review for private subtitles."
    )
    subparsers = parser.add_subparsers(required=True)

    run_parser = subparsers.add_parser("run")
    run_parser.add_argument("--corpus-root", required=True, type=Path)
    run_parser.add_argument("--seasons", required=True, nargs="+", type=int)
    run_parser.add_argument("--env-file", default=Path(".env"), type=Path)
    run_parser.add_argument("--wait", action="store_true")
    run_parser.add_argument(
        "--poll-interval-seconds",
        default=configuration.poll_interval_seconds,
        type=int,
    )
    run_parser.add_argument(
        "--maximum-wait-seconds",
        default=configuration.maximum_wait_seconds,
        type=int,
    )
    run_parser.set_defaults(handler=_run)

    advance_parser = subparsers.add_parser("advance")
    advance_parser.add_argument("run_directory", type=Path)
    advance_parser.add_argument("--env-file", default=Path(".env"), type=Path)
    advance_parser.set_defaults(handler=_advance)

    submit_parser = subparsers.add_parser("submit")
    submit_parser.add_argument("run_directory", type=Path)
    submit_parser.add_argument("--env-file", default=Path(".env"), type=Path)
    submit_parser.set_defaults(handler=_submit)

    status_parser = subparsers.add_parser("status")
    status_parser.add_argument("run_directory", type=Path)
    status_parser.set_defaults(handler=_status)

    final_review_parser = subparsers.add_parser("final-review")
    final_review_parser.add_argument("run_directory", type=Path)
    final_review_parser.add_argument("--env-file", default=Path(".env"), type=Path)
    final_review_parser.set_defaults(handler=_final_review)

    retry_parser = subparsers.add_parser("retry-incomplete")
    retry_parser.add_argument("run_directory", type=Path)
    retry_parser.add_argument("--env-file", default=Path(".env"), type=Path)
    retry_parser.set_defaults(handler=_retry_incomplete)

    reconcile_parser = subparsers.add_parser("reconcile-costs")
    reconcile_parser.add_argument("run_directory", type=Path)
    reconcile_parser.add_argument("--env-file", default=Path(".env"), type=Path)
    reconcile_parser.set_defaults(handler=_reconcile_costs)

    wait_parser = subparsers.add_parser("wait")
    wait_parser.add_argument("run_directory", type=Path)
    wait_parser.add_argument("--env-file", default=Path(".env"), type=Path)
    wait_parser.add_argument(
        "--poll-interval-seconds",
        default=configuration.poll_interval_seconds,
        type=int,
    )
    wait_parser.add_argument(
        "--maximum-wait-seconds",
        default=configuration.maximum_wait_seconds,
        type=int,
    )
    wait_parser.set_defaults(handler=_wait)

    arguments = parser.parse_args()
    try:
        arguments.handler(arguments)
    except RuntimeError as error:
        raise SystemExit(str(error)) from None


if __name__ == "__main__":
    main()
