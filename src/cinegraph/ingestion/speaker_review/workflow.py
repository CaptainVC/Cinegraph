from __future__ import annotations

import json
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path

from cinegraph.common.error_messages import SpeakerReviewErrorMessages
from cinegraph.config import SpeakerReviewConfiguration
from cinegraph.domain.enums.enum import (
    SpeakerReviewAction,
    SpeakerReviewDisposition,
    SpeakerReviewRunStatus,
)
from cinegraph.domain.models.transcript import (
    SpeakerReviewCandidate,
    SpeakerReviewDecision,
    SpeakerReviewVerdict,
)
from cinegraph.ingestion.speaker_review.batch_requests import (
    build_adjudication_batch_requests,
    build_final_review_batch_requests,
    build_primary_batch_requests,
)
from cinegraph.ingestion.speaker_review.batch_results import parse_batch_results
from cinegraph.ingestion.speaker_review.candidates import (
    build_speaker_review_candidates,
    candidate_from_dict,
)
from cinegraph.ingestion.speaker_review.costs import (
    actual_batch_output_cost_usd,
    enforce_budget,
    estimate_batch_cost_usd,
    partition_batch_requests,
)
from cinegraph.ingestion.speaker_review.decisions import (
    apply_adjudication,
    apply_final_review,
    decide_primary_consensus,
)
from cinegraph.ingestion.speaker_review.reviewed_output import write_reviewed_outputs
from cinegraph.ports.llm.speaker_review_batch_gateway import (
    BatchSnapshot,
    BatchSubmission,
    SpeakerReviewBatchGateway,
)


@dataclass(frozen=True, slots=True)
class SpeakerReviewRunState:
    schema_version: int
    run_id: str
    status: SpeakerReviewRunStatus
    created_at: str
    updated_at: str
    candidate_count: int
    primary_model: str
    adjudication_model: str
    prompt_version: str
    maximum_cost_usd: float
    estimated_primary_cost_usd: float
    actual_primary_cost_usd: float
    actual_adjudication_cost_usd: float
    final_review_model: str = ""
    actual_final_review_cost_usd: float = 0.0
    primary_batch_id: str | None = None
    primary_input_file_id: str | None = None
    adjudication_batch_id: str | None = None
    adjudication_input_file_id: str | None = None
    primary_part_count: int = 0
    primary_completed_part_count: int = 0
    primary_batch_ids: tuple[str, ...] = ()
    primary_input_file_ids: tuple[str, ...] = ()
    adjudication_part_count: int = 0
    adjudication_completed_part_count: int = 0
    adjudication_batch_ids: tuple[str, ...] = ()
    adjudication_input_file_ids: tuple[str, ...] = ()
    final_review_part_count: int = 0
    final_review_completed_part_count: int = 0
    final_review_batch_ids: tuple[str, ...] = ()
    final_review_input_file_ids: tuple[str, ...] = ()
    final_review_batch_id: str | None = None
    final_review_input_file_id: str | None = None
    final_review_retry_count: int = 0
    accepted_by_consensus: int = 0
    accepted_by_adjudication: int = 0
    accepted_by_final_review: int = 0
    needs_human: int = 0

    @property
    def actual_total_cost_usd(self) -> float:
        return (
            self.actual_primary_cost_usd
            + self.actual_adjudication_cost_usd
            + self.actual_final_review_cost_usd
        )

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["status"] = self.status.value
        payload["actual_total_cost_usd"] = self.actual_total_cost_usd
        return payload


class SpeakerReviewWorkflow:
    def __init__(
        self,
        *,
        gateway: SpeakerReviewBatchGateway,
        configuration: SpeakerReviewConfiguration,
        primary_model: str,
        adjudication_model: str,
        final_review_model: str,
        primary_reasoning_effort: str,
        adjudication_reasoning_effort: str,
        final_review_reasoning_effort: str,
    ) -> None:
        self._gateway = gateway
        self._configuration = configuration
        self._primary_model = primary_model
        self._adjudication_model = adjudication_model
        self._final_review_model = final_review_model
        self._primary_reasoning_effort = primary_reasoning_effort
        self._adjudication_reasoning_effort = adjudication_reasoning_effort
        self._final_review_reasoning_effort = final_review_reasoning_effort

    def prepare(
        self,
        *,
        corpus_root: Path,
        seasons: tuple[int, ...],
    ) -> tuple[Path, SpeakerReviewRunState]:
        source_paths: dict[str, Path] = {}
        candidates: list[SpeakerReviewCandidate] = []
        for season in seasons:
            source_pdf = corpus_root / self._configuration.script_pdf_filename_template.format(
                season=season
            )
            season_directories = tuple(
                corpus_root.glob(
                    self._configuration.season_directory_glob_template.format(
                        season=season
                    )
                )
            )
            if len(season_directories) != 1:
                raise ValueError(
                    f"Expected one corpus directory for season {season}, found "
                    f"{len(season_directories)}."
                )
            aligned_paths = tuple(
                sorted(
                    (season_directories[0] / "script-aligned").glob(
                        self._configuration.aligned_subtitle_glob
                    )
                )
            )
            for path in aligned_paths:
                if path.name in source_paths:
                    raise ValueError(f"Duplicate aligned subtitle filename: {path.name}")
                source_paths[path.name] = path.resolve()
            candidates.extend(
                build_speaker_review_candidates(
                    source_pdf=source_pdf,
                    aligned_subtitles=aligned_paths,
                    configuration=self._configuration,
                )
            )
        if not candidates:
            raise ValueError(SpeakerReviewErrorMessages.NO_UNCERTAIN_SPEAKER_LABELS)

        candidate_tuple = tuple(candidates)
        primary_requests = build_primary_batch_requests(
            candidates=candidate_tuple,
            model=self._primary_model,
            reasoning_effort=self._primary_reasoning_effort,
            configuration=self._configuration,
        )
        estimated_primary_cost = estimate_batch_cost_usd(
            requests=primary_requests,
            model=self._primary_model,
            configuration=self._configuration,
        )
        enforce_budget(
            estimated_cost_usd=estimated_primary_cost,
            already_spent_usd=0.0,
            configuration=self._configuration,
        )
        run_id = self._run_id(candidate_tuple)
        run_directory = (
            corpus_root / self._configuration.run_directory_name / run_id
        )
        state_path = run_directory / "run-state.json"
        if state_path.exists():
            return run_directory, load_run_state(state_path)

        run_directory.mkdir(parents=True, exist_ok=False)
        _write_jsonl(
            run_directory / "candidates.jsonl",
            tuple(item.to_dict() for item in candidate_tuple),
        )
        primary_parts = partition_batch_requests(
            requests=primary_requests,
            configuration=self._configuration,
        )
        _write_request_parts(run_directory, "primary", primary_parts)
        _write_json(
            run_directory / "source-manifest.json",
            {
                "sources": {
                    filename: str(path) for filename, path in sorted(source_paths.items())
                }
            },
        )
        timestamp = _now()
        state = SpeakerReviewRunState(
            schema_version=self._configuration.schema_version,
            run_id=run_id,
            status=SpeakerReviewRunStatus.PREPARED,
            created_at=timestamp,
            updated_at=timestamp,
            candidate_count=len(candidate_tuple),
            primary_model=self._primary_model,
            adjudication_model=self._adjudication_model,
            prompt_version=self._configuration.prompt_version,
            maximum_cost_usd=self._configuration.maximum_run_cost_usd,
            estimated_primary_cost_usd=estimated_primary_cost,
            actual_primary_cost_usd=0.0,
            actual_adjudication_cost_usd=0.0,
            final_review_model=self._final_review_model,
            primary_part_count=len(primary_parts),
        )
        save_run_state(run_directory, state)
        return run_directory, state

    def submit_primary(
        self,
        run_directory: Path,
        state: SpeakerReviewRunState,
    ) -> SpeakerReviewRunState:
        if state.status is not SpeakerReviewRunStatus.PREPARED:
            if state.primary_batch_ids or state.primary_batch_id is not None:
                return state
            raise RuntimeError(
                SpeakerReviewErrorMessages.RUN_STATE_CONFLICT.format(
                    status=state.status.value
                )
            )
        submission = self._submit_part(
            run_directory=run_directory,
            state=state,
            stage="primary",
            part_index=0,
        )
        updated = replace(
            state,
            status=SpeakerReviewRunStatus.PRIMARY_SUBMITTED,
            updated_at=_now(),
            primary_batch_id=submission.batch_id,
            primary_input_file_id=submission.input_file_id,
            primary_batch_ids=(submission.batch_id,),
            primary_input_file_ids=(submission.input_file_id,),
        )
        save_run_state(run_directory, updated)
        return updated

    def advance(
        self,
        run_directory: Path,
        state: SpeakerReviewRunState,
    ) -> SpeakerReviewRunState:
        if state.status is SpeakerReviewRunStatus.PRIMARY_SUBMITTED:
            return self._advance_primary(run_directory, state)
        if state.status is SpeakerReviewRunStatus.ADJUDICATION_SUBMITTED:
            return self._advance_adjudication(run_directory, state)
        if state.status is SpeakerReviewRunStatus.FINAL_REVIEW_SUBMITTED:
            return self._advance_final_review(run_directory, state)
        return state

    def _advance_primary(
        self,
        run_directory: Path,
        state: SpeakerReviewRunState,
    ) -> SpeakerReviewRunState:
        batch_ids = state.primary_batch_ids or (
            (state.primary_batch_id,) if state.primary_batch_id is not None else ()
        )
        part_index = state.primary_completed_part_count
        if part_index >= len(batch_ids):
            raise RuntimeError(
                SpeakerReviewErrorMessages.ACTIVE_BATCH_ID_MISSING.format(
                    stage="Primary"
                )
            )
        snapshot = self._required_snapshot(batch_ids[part_index])
        if snapshot.status in self._configuration.terminal_batch_failure_statuses:
            return self._fail_batch(run_directory, state, snapshot)
        if snapshot.status != self._configuration.successful_batch_status:
            return state

        self._download_completed_batch(
            run_directory,
            _part_stage_name("primary", part_index),
            snapshot,
        )
        completed_part_count = part_index + 1
        if completed_part_count < state.primary_part_count:
            submission = self._submit_part(
                run_directory=run_directory,
                state=state,
                stage="primary",
                part_index=completed_part_count,
            )
            updated = replace(
                state,
                updated_at=_now(),
                primary_batch_id=submission.batch_id,
                primary_input_file_id=submission.input_file_id,
                primary_completed_part_count=completed_part_count,
                primary_batch_ids=(*batch_ids, submission.batch_id),
                primary_input_file_ids=(
                    *state.primary_input_file_ids,
                    submission.input_file_id,
                ),
            )
            save_run_state(run_directory, updated)
            return updated

        output_text = _combined_stage_output(
            run_directory,
            "primary",
            state.primary_part_count,
        )
        candidates = _load_candidates(run_directory)
        candidates_by_id = {item.candidate_id: item for item in candidates}
        primary_verdicts, parse_errors = parse_batch_results(
            output_jsonl=output_text,
            candidates=candidates_by_id,
            configuration=self._configuration,
        )
        _write_jsonl(
            run_directory / "primary-verdicts.jsonl",
            tuple(
                verdict.to_dict()
                for items in primary_verdicts.values()
                for verdict in items
            ),
        )
        _write_json(run_directory / "primary-parse-errors.json", list(parse_errors))
        primary_decisions = decide_primary_consensus(
            candidates=candidates,
            verdicts=primary_verdicts,
            configuration=self._configuration,
        )
        _write_jsonl(
            run_directory / "primary-decisions.jsonl",
            tuple(item.to_dict() for item in primary_decisions),
        )
        primary_cost = actual_batch_output_cost_usd(
            output_jsonl=output_text,
            configured_model=self._primary_model,
            configuration=self._configuration,
        )
        residual_ids = {
            item.candidate_id
            for item in primary_decisions
            if item.disposition is SpeakerReviewDisposition.ADJUDICATION_REQUIRED
        }
        if not residual_ids:
            completed = replace(
                state,
                actual_primary_cost_usd=primary_cost,
                accepted_by_consensus=len(primary_decisions),
                primary_completed_part_count=completed_part_count,
                updated_at=_now(),
            )
            return self._finalize(run_directory, completed, primary_decisions)

        residual = tuple(
            item for item in candidates if item.candidate_id in residual_ids
        )
        adjudication_requests = build_adjudication_batch_requests(
            candidates=residual,
            primary_verdicts=primary_verdicts,
            model=self._adjudication_model,
            reasoning_effort=self._adjudication_reasoning_effort,
            configuration=self._configuration,
        )
        estimated_adjudication_cost = estimate_batch_cost_usd(
            requests=adjudication_requests,
            model=self._adjudication_model,
            configuration=self._configuration,
        )
        enforce_budget(
            estimated_cost_usd=estimated_adjudication_cost,
            already_spent_usd=primary_cost,
            configuration=self._configuration,
        )
        adjudication_parts = partition_batch_requests(
            requests=adjudication_requests,
            configuration=self._configuration,
        )
        _write_request_parts(run_directory, "adjudication", adjudication_parts)
        submission = self._submit_part(
            run_directory=run_directory,
            state=state,
            stage="adjudication",
            part_index=0,
        )
        updated = replace(
            state,
            status=SpeakerReviewRunStatus.ADJUDICATION_SUBMITTED,
            updated_at=_now(),
            actual_primary_cost_usd=primary_cost,
            adjudication_batch_id=submission.batch_id,
            adjudication_input_file_id=submission.input_file_id,
            primary_completed_part_count=completed_part_count,
            adjudication_part_count=len(adjudication_parts),
            adjudication_batch_ids=(submission.batch_id,),
            adjudication_input_file_ids=(submission.input_file_id,),
            accepted_by_consensus=len(candidates) - len(residual),
        )
        save_run_state(run_directory, updated)
        return updated

    def _advance_adjudication(
        self,
        run_directory: Path,
        state: SpeakerReviewRunState,
    ) -> SpeakerReviewRunState:
        batch_ids = state.adjudication_batch_ids or (
            (state.adjudication_batch_id,)
            if state.adjudication_batch_id is not None
            else ()
        )
        part_index = state.adjudication_completed_part_count
        if part_index >= len(batch_ids):
            raise RuntimeError(
                SpeakerReviewErrorMessages.ACTIVE_BATCH_ID_MISSING.format(
                    stage="Adjudication"
                )
            )
        snapshot = self._required_snapshot(batch_ids[part_index])
        if snapshot.status in self._configuration.terminal_batch_failure_statuses:
            return self._fail_batch(run_directory, state, snapshot)
        if snapshot.status != self._configuration.successful_batch_status:
            return state

        self._download_completed_batch(
            run_directory,
            _part_stage_name("adjudication", part_index),
            snapshot,
        )
        completed_part_count = part_index + 1
        if completed_part_count < state.adjudication_part_count:
            submission = self._submit_part(
                run_directory=run_directory,
                state=state,
                stage="adjudication",
                part_index=completed_part_count,
            )
            updated = replace(
                state,
                updated_at=_now(),
                adjudication_batch_id=submission.batch_id,
                adjudication_input_file_id=submission.input_file_id,
                adjudication_completed_part_count=completed_part_count,
                adjudication_batch_ids=(*batch_ids, submission.batch_id),
                adjudication_input_file_ids=(
                    *state.adjudication_input_file_ids,
                    submission.input_file_id,
                ),
            )
            save_run_state(run_directory, updated)
            return updated

        output_text = _combined_stage_output(
            run_directory,
            "adjudication",
            state.adjudication_part_count,
        )
        candidates = _load_candidates(run_directory)
        candidates_by_id = {item.candidate_id: item for item in candidates}
        primary_text = _combined_stage_output(
            run_directory,
            "primary",
            state.primary_part_count,
        )
        primary_verdicts, _ = parse_batch_results(
            output_jsonl=primary_text,
            candidates=candidates_by_id,
            configuration=self._configuration,
        )
        primary_decisions = decide_primary_consensus(
            candidates=candidates,
            verdicts=primary_verdicts,
            configuration=self._configuration,
        )
        adjudication_verdicts, parse_errors = parse_batch_results(
            output_jsonl=output_text,
            candidates=candidates_by_id,
            configuration=self._configuration,
        )
        _write_jsonl(
            run_directory / "adjudication-verdicts.jsonl",
            tuple(
                verdict.to_dict()
                for items in adjudication_verdicts.values()
                for verdict in items
            ),
        )
        _write_json(
            run_directory / "adjudication-parse-errors.json",
            list(parse_errors),
        )
        final_decisions = apply_adjudication(
            primary_decisions=primary_decisions,
            adjudication_verdicts=adjudication_verdicts,
            configuration=self._configuration,
        )
        _write_jsonl(
            run_directory / "final-decisions.jsonl",
            tuple(item.to_dict() for item in final_decisions),
        )
        adjudication_cost = actual_batch_output_cost_usd(
            output_jsonl=output_text,
            configured_model=self._adjudication_model,
            configuration=self._configuration,
        )
        updated = replace(
            state,
            actual_adjudication_cost_usd=adjudication_cost,
            adjudication_completed_part_count=completed_part_count,
            updated_at=_now(),
            accepted_by_adjudication=sum(
                item.disposition is SpeakerReviewDisposition.ADJUDICATION_ACCEPTED
                for item in final_decisions
            ),
            needs_human=sum(
                item.disposition is SpeakerReviewDisposition.NEEDS_HUMAN
                for item in final_decisions
            ),
        )
        if updated.needs_human:
            return self.submit_final_review(
                run_directory,
                updated,
                decisions=final_decisions,
            )
        return self._finalize(run_directory, updated, final_decisions)

    def submit_final_review(
        self,
        run_directory: Path,
        state: SpeakerReviewRunState,
        *,
        decisions: tuple[SpeakerReviewDecision, ...] | None = None,
    ) -> SpeakerReviewRunState:
        if state.final_review_batch_ids or state.final_review_batch_id is not None:
            return state
        if state.status not in {
            SpeakerReviewRunStatus.ADJUDICATION_SUBMITTED,
            SpeakerReviewRunStatus.NEEDS_HUMAN,
        }:
            raise RuntimeError(
                SpeakerReviewErrorMessages.RUN_STATE_CONFLICT.format(
                    status=state.status.value
                )
            )
        final_decisions = decisions or _load_decisions(
            run_directory / "final-decisions.jsonl"
        )
        unresolved_ids = {
            item.candidate_id
            for item in final_decisions
            if item.disposition is SpeakerReviewDisposition.NEEDS_HUMAN
        }
        if not unresolved_ids:
            return self._finalize(run_directory, state, final_decisions)
        candidates = tuple(
            item
            for item in _load_candidates(run_directory)
            if item.candidate_id in unresolved_ids
        )
        decisions_by_id = {item.candidate_id: item for item in final_decisions}
        requests = build_final_review_batch_requests(
            candidates=candidates,
            decisions=decisions_by_id,
            model=self._final_review_model,
            reasoning_effort=self._final_review_reasoning_effort,
            configuration=self._configuration,
        )
        estimated_cost = estimate_batch_cost_usd(
            requests=requests,
            model=self._final_review_model,
            configuration=self._configuration,
        )
        enforce_budget(
            estimated_cost_usd=estimated_cost,
            already_spent_usd=state.actual_total_cost_usd,
            configuration=self._configuration,
        )
        parts = partition_batch_requests(
            requests=requests,
            configuration=self._configuration,
        )
        _write_request_parts(run_directory, "final-review", parts)
        submission = self._submit_part(
            run_directory=run_directory,
            state=state,
            stage="final-review",
            part_index=0,
        )
        updated = replace(
            state,
            status=SpeakerReviewRunStatus.FINAL_REVIEW_SUBMITTED,
            updated_at=_now(),
            final_review_model=self._final_review_model,
            final_review_part_count=len(parts),
            final_review_batch_id=submission.batch_id,
            final_review_input_file_id=submission.input_file_id,
            final_review_batch_ids=(submission.batch_id,),
            final_review_input_file_ids=(submission.input_file_id,),
        )
        save_run_state(run_directory, updated)
        return updated

    def retry_incomplete_final_review(
        self,
        run_directory: Path,
        state: SpeakerReviewRunState,
    ) -> SpeakerReviewRunState:
        """Resume only final-review requests that never produced a valid verdict."""
        if state.status is not SpeakerReviewRunStatus.NEEDS_HUMAN:
            raise RuntimeError(
                SpeakerReviewErrorMessages.RUN_STATE_CONFLICT.format(
                    status=state.status.value
                )
            )
        if not state.final_review_part_count:
            raise RuntimeError(
                SpeakerReviewErrorMessages.RUN_STATE_CONFLICT.format(
                    status=state.status.value
                )
            )
        candidates = _load_candidates(run_directory)
        output_text = _combined_stage_output(
            run_directory,
            "final-review",
            state.final_review_part_count,
        )
        verdicts, _ = parse_batch_results(
            output_jsonl=output_text,
            candidates={item.candidate_id: item for item in candidates},
            configuration=self._configuration,
        )
        final_cost = actual_batch_output_cost_usd(
            output_jsonl=output_text,
            configured_model=self._final_review_model,
            configuration=self._configuration,
        )
        retried = self._submit_incomplete_final_review_retry(
            run_directory=run_directory,
            state=state,
            verdicts=verdicts,
            final_cost=final_cost,
            completed_part_count=state.final_review_completed_part_count,
        )
        return retried or state

    def reconcile_completed_costs(
        self,
        run_directory: Path,
        state: SpeakerReviewRunState,
    ) -> SpeakerReviewRunState:
        """Reprice completed outputs from raw API usage without changing decisions."""
        costs = {
            "actual_primary_cost_usd": self._completed_stage_cost(
                run_directory=run_directory,
                stage="primary",
                part_count=state.primary_part_count,
                completed_part_count=state.primary_completed_part_count,
                model=state.primary_model,
                fallback=state.actual_primary_cost_usd,
            ),
            "actual_adjudication_cost_usd": self._completed_stage_cost(
                run_directory=run_directory,
                stage="adjudication",
                part_count=state.adjudication_part_count,
                completed_part_count=state.adjudication_completed_part_count,
                model=state.adjudication_model,
                fallback=state.actual_adjudication_cost_usd,
            ),
            "actual_final_review_cost_usd": self._completed_stage_cost(
                run_directory=run_directory,
                stage="final-review",
                part_count=state.final_review_part_count,
                completed_part_count=state.final_review_completed_part_count,
                model=state.final_review_model,
                fallback=state.actual_final_review_cost_usd,
            ),
        }
        updated = replace(state, updated_at=_now(), **costs)
        save_run_state(run_directory, updated)
        return updated

    def _completed_stage_cost(
        self,
        *,
        run_directory: Path,
        stage: str,
        part_count: int,
        completed_part_count: int,
        model: str,
        fallback: float,
    ) -> float:
        if not part_count or completed_part_count < part_count or not model:
            return fallback
        return actual_batch_output_cost_usd(
            output_jsonl=_combined_stage_output(
                run_directory,
                stage,
                part_count,
            ),
            configured_model=model,
            configuration=self._configuration,
        )

    def _advance_final_review(
        self,
        run_directory: Path,
        state: SpeakerReviewRunState,
    ) -> SpeakerReviewRunState:
        part_index = state.final_review_completed_part_count
        if part_index >= len(state.final_review_batch_ids):
            raise RuntimeError(
                SpeakerReviewErrorMessages.ACTIVE_BATCH_ID_MISSING.format(
                    stage="Final"
                )
            )
        snapshot = self._required_snapshot(state.final_review_batch_ids[part_index])
        if snapshot.status in self._configuration.terminal_batch_failure_statuses:
            return self._fail_batch(run_directory, state, snapshot)
        if snapshot.status != self._configuration.successful_batch_status:
            return state

        self._download_completed_batch(
            run_directory,
            _part_stage_name("final-review", part_index),
            snapshot,
        )
        completed_part_count = part_index + 1
        if completed_part_count < state.final_review_part_count:
            submission = self._submit_part(
                run_directory=run_directory,
                state=state,
                stage="final-review",
                part_index=completed_part_count,
            )
            updated = replace(
                state,
                updated_at=_now(),
                final_review_batch_id=submission.batch_id,
                final_review_input_file_id=submission.input_file_id,
                final_review_completed_part_count=completed_part_count,
                final_review_batch_ids=(
                    *state.final_review_batch_ids,
                    submission.batch_id,
                ),
                final_review_input_file_ids=(
                    *state.final_review_input_file_ids,
                    submission.input_file_id,
                ),
            )
            save_run_state(run_directory, updated)
            return updated

        candidates = _load_candidates(run_directory)
        candidates_by_id = {item.candidate_id: item for item in candidates}
        output_text = _combined_stage_output(
            run_directory,
            "final-review",
            state.final_review_part_count,
        )
        verdicts, parse_errors = parse_batch_results(
            output_jsonl=output_text,
            candidates=candidates_by_id,
            configuration=self._configuration,
        )
        retry_suffix = (
            f"-retry-{state.final_review_retry_count}"
            if state.final_review_retry_count
            else ""
        )
        _write_jsonl(
            run_directory / f"final-review-verdicts{retry_suffix}.jsonl",
            tuple(
                verdict.to_dict()
                for items in verdicts.values()
                for verdict in items
            ),
        )
        _write_json(
            run_directory / f"final-review-parse-errors{retry_suffix}.json",
            list(parse_errors),
        )
        final_cost = actual_batch_output_cost_usd(
            output_jsonl=output_text,
            configured_model=self._final_review_model,
            configuration=self._configuration,
        )
        retried = self._submit_incomplete_final_review_retry(
            run_directory=run_directory,
            state=state,
            verdicts=verdicts,
            final_cost=final_cost,
            completed_part_count=completed_part_count,
        )
        if retried is not None:
            return retried
        prior_decisions = _load_decisions(run_directory / "final-decisions.jsonl")
        decisions = apply_final_review(
            decisions=prior_decisions,
            final_verdicts=verdicts,
            configuration=self._configuration,
        )
        _write_jsonl(
            run_directory / f"post-final-decisions{retry_suffix}.jsonl",
            tuple(item.to_dict() for item in decisions),
        )
        updated = replace(
            state,
            actual_final_review_cost_usd=final_cost,
            final_review_completed_part_count=completed_part_count,
            accepted_by_final_review=sum(
                item.disposition is SpeakerReviewDisposition.FINAL_REVIEW_ACCEPTED
                for item in decisions
            ),
            needs_human=sum(
                item.disposition is SpeakerReviewDisposition.NEEDS_HUMAN
                for item in decisions
            ),
            updated_at=_now(),
        )
        return self._finalize(run_directory, updated, decisions)

    def _submit_incomplete_final_review_retry(
        self,
        *,
        run_directory: Path,
        state: SpeakerReviewRunState,
        verdicts: dict[str, tuple[SpeakerReviewVerdict, ...]],
        final_cost: float,
        completed_part_count: int,
    ) -> SpeakerReviewRunState | None:
        if (
            state.final_review_retry_count
            >= self._configuration.final_review_max_retry_rounds
        ):
            return None
        prior_decisions = _load_decisions(run_directory / "final-decisions.jsonl")
        missing_ids = {
            item.candidate_id
            for item in prior_decisions
            if item.disposition is SpeakerReviewDisposition.NEEDS_HUMAN
            and not verdicts.get(item.candidate_id)
        }
        if not missing_ids:
            return None
        candidates = tuple(
            item
            for item in _load_candidates(run_directory)
            if item.candidate_id in missing_ids
        )
        decisions_by_id = {item.candidate_id: item for item in prior_decisions}
        retry_round = state.final_review_retry_count + 1
        requests = build_final_review_batch_requests(
            candidates=candidates,
            decisions=decisions_by_id,
            model=self._final_review_model,
            reasoning_effort=self._final_review_reasoning_effort,
            configuration=self._configuration,
            pass_id=self._configuration.final_review_retry_pass_id_template.format(
                round_number=retry_round
            ),
            max_output_tokens=(
                self._configuration.final_review_retry_max_output_tokens
            ),
        )
        estimated_cost = estimate_batch_cost_usd(
            requests=requests,
            model=self._final_review_model,
            configuration=self._configuration,
        )
        enforce_budget(
            estimated_cost_usd=estimated_cost,
            already_spent_usd=(
                state.actual_primary_cost_usd
                + state.actual_adjudication_cost_usd
                + final_cost
            ),
            configuration=self._configuration,
        )
        parts = partition_batch_requests(
            requests=requests,
            configuration=self._configuration,
        )
        first_part_index = state.final_review_part_count
        _write_request_parts(
            run_directory,
            "final-review",
            parts,
            start_index=first_part_index,
        )
        submission = self._submit_part(
            run_directory=run_directory,
            state=state,
            stage="final-review",
            part_index=first_part_index,
        )
        batch_ids = state.final_review_batch_ids or (
            (state.final_review_batch_id,)
            if state.final_review_batch_id is not None
            else ()
        )
        input_file_ids = state.final_review_input_file_ids or (
            (state.final_review_input_file_id,)
            if state.final_review_input_file_id is not None
            else ()
        )
        updated = replace(
            state,
            status=SpeakerReviewRunStatus.FINAL_REVIEW_SUBMITTED,
            updated_at=_now(),
            actual_final_review_cost_usd=final_cost,
            final_review_part_count=state.final_review_part_count + len(parts),
            final_review_completed_part_count=completed_part_count,
            final_review_batch_id=submission.batch_id,
            final_review_input_file_id=submission.input_file_id,
            final_review_batch_ids=(*batch_ids, submission.batch_id),
            final_review_input_file_ids=(
                *input_file_ids,
                submission.input_file_id,
            ),
            final_review_retry_count=retry_round,
        )
        save_run_state(run_directory, updated)
        return updated

    def _finalize(
        self,
        run_directory: Path,
        state: SpeakerReviewRunState,
        decisions: tuple[SpeakerReviewDecision, ...],
    ) -> SpeakerReviewRunState:
        candidates = _load_candidates(run_directory)
        source_manifest = json.loads(
            (run_directory / "source-manifest.json").read_text(encoding="utf-8")
        )
        source_paths = {
            filename: Path(path)
            for filename, path in source_manifest["sources"].items()
        }
        reviewer_models = [state.primary_model]
        if state.adjudication_batch_ids or state.adjudication_batch_id is not None:
            reviewer_models.append(state.adjudication_model)
        if state.final_review_batch_ids or state.final_review_batch_id is not None:
            reviewer_models.append(state.final_review_model)
        records = write_reviewed_outputs(
            run_directory=run_directory,
            source_paths=source_paths,
            candidates=candidates,
            decisions=decisions,
            reviewer_models=tuple(reviewer_models),
            prompt_version=state.prompt_version,
            actual_cost_usd=state.actual_total_cost_usd,
            configuration=self._configuration,
            human_queue_filename=(
                f"remaining-human-review-queue-retry-"
                f"{state.final_review_retry_count}.json"
                if state.final_review_retry_count
                else None
            ),
        )
        final_status = (
            SpeakerReviewRunStatus.COMPLETED
            if records
            else SpeakerReviewRunStatus.NEEDS_HUMAN
        )
        updated = replace(state, status=final_status, updated_at=_now())
        save_run_state(run_directory, updated)
        return updated

    def _submit_part(
        self,
        *,
        run_directory: Path,
        state: SpeakerReviewRunState,
        stage: str,
        part_index: int,
    ) -> BatchSubmission:
        return self._gateway.submit(
            _request_part_path(run_directory, stage, part_index),
            self._configuration.batch_completion_window,
            {
                "cinegraph_run_id": state.run_id,
                "stage": f"speaker-review-{stage}",
                "part": str(part_index + 1),
                "prompt_version": state.prompt_version,
            },
        )

    def _required_snapshot(self, batch_id: str | None) -> BatchSnapshot:
        if batch_id is None:
            raise RuntimeError("Review state does not contain a batch ID.")
        return self._gateway.retrieve(batch_id)

    def _download_completed_batch(
        self,
        run_directory: Path,
        stage: str,
        snapshot: BatchSnapshot,
    ) -> str:
        if snapshot.output_file_id is None:
            raise RuntimeError("Completed Batch does not contain an output file ID.")
        output_text = self._gateway.download_file(snapshot.output_file_id)
        _write_text_if_new_or_unchanged(
            run_directory / f"{stage}-output.jsonl",
            output_text,
        )
        if snapshot.error_file_id is not None:
            _write_text_if_new_or_unchanged(
                run_directory / f"{stage}-api-errors.jsonl",
                self._gateway.download_file(snapshot.error_file_id),
            )
        return output_text

    def _fail_batch(
        self,
        run_directory: Path,
        state: SpeakerReviewRunState,
        snapshot: BatchSnapshot,
    ) -> SpeakerReviewRunState:
        if snapshot.error_file_id is not None:
            _write_text_if_new_or_unchanged(
                run_directory / "terminal-api-errors.jsonl",
                self._gateway.download_file(snapshot.error_file_id),
            )
        updated = replace(
            state,
            status=SpeakerReviewRunStatus.FAILED,
            updated_at=_now(),
        )
        save_run_state(run_directory, updated)
        raise RuntimeError(
            SpeakerReviewErrorMessages.BATCH_TERMINAL_FAILURE.format(
                batch_id=snapshot.batch_id,
                status=snapshot.status,
            )
        )

    def _run_id(self, candidates: tuple[SpeakerReviewCandidate, ...]) -> str:
        fingerprint = {
            "schema_version": self._configuration.schema_version,
            "prompt_version": self._configuration.prompt_version,
            "primary_model": self._primary_model,
            "adjudication_model": self._adjudication_model,
            "final_review_model": self._final_review_model,
            "primary_reasoning_effort": self._primary_reasoning_effort,
            "adjudication_reasoning_effort": self._adjudication_reasoning_effort,
            "final_review_reasoning_effort": self._final_review_reasoning_effort,
            "consensus_minimum_confidence": (
                self._configuration.consensus_minimum_confidence
            ),
            "adjudication_minimum_confidence": (
                self._configuration.adjudication_minimum_confidence
            ),
            "final_review_minimum_confidence": (
                self._configuration.final_review_minimum_confidence
            ),
            "final_review_retry_max_output_tokens": (
                self._configuration.final_review_retry_max_output_tokens
            ),
            "final_review_max_retry_rounds": (
                self._configuration.final_review_max_retry_rounds
            ),
            "maximum_enqueued_input_tokens_per_batch": (
                self._configuration.maximum_enqueued_input_tokens_per_batch
            ),
            "candidates": [item.to_dict() for item in candidates],
        }
        digest = sha256(
            json.dumps(
                fingerprint,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()[:16]
        return f"speaker-review-{digest}"


def load_run_state(path: Path) -> SpeakerReviewRunState:
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload.pop("actual_total_cost_usd", None)
    payload["status"] = SpeakerReviewRunStatus(payload["status"])
    for field_name in (
        "primary_batch_ids",
        "primary_input_file_ids",
        "adjudication_batch_ids",
        "adjudication_input_file_ids",
        "final_review_batch_ids",
        "final_review_input_file_ids",
    ):
        if field_name in payload:
            payload[field_name] = tuple(payload[field_name])
    return SpeakerReviewRunState(**payload)


def save_run_state(run_directory: Path, state: SpeakerReviewRunState) -> None:
    _write_json_atomic(run_directory / "run-state.json", state.to_dict())


def _load_candidates(run_directory: Path) -> tuple[SpeakerReviewCandidate, ...]:
    return tuple(
        candidate_from_dict(json.loads(line))
        for line in (run_directory / "candidates.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    )


def _load_decisions(path: Path) -> tuple[SpeakerReviewDecision, ...]:
    return tuple(
        _decision_from_dict(json.loads(line))
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    )


def _decision_from_dict(payload: dict[str, object]) -> SpeakerReviewDecision:
    primary_payload = payload.get("primary_verdicts")
    if not isinstance(primary_payload, list):
        raise TypeError("Decision primary_verdicts must be a list.")
    adjudication_payload = payload.get("adjudication_verdict")
    final_payload = payload.get("final_review_verdict")
    return SpeakerReviewDecision(
        candidate_id=str(payload["candidate_id"]),
        disposition=SpeakerReviewDisposition(str(payload["disposition"])),
        speaker=str(payload["speaker"]) if payload.get("speaker") is not None else None,
        reason=str(payload["reason"]),
        primary_verdicts=tuple(
            _verdict_from_dict(item)
            for item in primary_payload
            if isinstance(item, dict)
        ),
        adjudication_verdict=(
            _verdict_from_dict(adjudication_payload)
            if isinstance(adjudication_payload, dict)
            else None
        ),
        final_review_verdict=(
            _verdict_from_dict(final_payload)
            if isinstance(final_payload, dict)
            else None
        ),
    )


def _verdict_from_dict(payload: dict[str, object]) -> SpeakerReviewVerdict:
    evidence_ids = payload.get("evidence_ids")
    if not isinstance(evidence_ids, list):
        raise TypeError("Verdict evidence_ids must be a list.")
    return SpeakerReviewVerdict(
        candidate_id=str(payload["candidate_id"]),
        pass_id=str(payload["pass_id"]),
        action=SpeakerReviewAction(str(payload["action"])),
        speaker=str(payload["speaker"]),
        confidence=float(payload["confidence"]),
        evidence_ids=tuple(str(item) for item in evidence_ids),
        rationale=str(payload["rationale"]),
        model=str(payload["model"]),
        response_id=str(payload["response_id"]),
        input_tokens=int(payload["input_tokens"]),
        output_tokens=int(payload["output_tokens"]),
    )


def _write_jsonl(path: Path, items: tuple[dict[str, object], ...]) -> None:
    content = "".join(
        json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n" for item in items
    )
    _write_text_if_new_or_unchanged(path, content)


def _write_request_parts(
    run_directory: Path,
    stage: str,
    parts: tuple[tuple[dict[str, object], ...], ...],
    *,
    start_index: int = 0,
) -> None:
    for part_index, requests in enumerate(parts, start=start_index):
        _write_jsonl(
            _request_part_path(run_directory, stage, part_index),
            requests,
        )


def _request_part_path(
    run_directory: Path,
    stage: str,
    part_index: int,
) -> Path:
    return run_directory / f"{_part_stage_name(stage, part_index)}-requests.jsonl"


def _part_stage_name(stage: str, part_index: int) -> str:
    return f"{stage}-part-{part_index + 1:04d}"


def _combined_stage_output(
    run_directory: Path,
    stage: str,
    part_count: int,
) -> str:
    contents = [
        (
            run_directory
            / f"{_part_stage_name(stage, part_index)}-output.jsonl"
        ).read_text(encoding="utf-8").rstrip("\n")
        for part_index in range(part_count)
    ]
    return "\n".join(contents) + "\n"


def _write_json(path: Path, payload: object) -> None:
    _write_text_if_new_or_unchanged(
        path,
        json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
    )


def _write_json_atomic(path: Path, payload: object) -> None:
    temporary_path = path.with_suffix(path.suffix + ".tmp")
    temporary_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary_path.replace(path)


def _write_text_if_new_or_unchanged(path: Path, content: str) -> None:
    if path.exists() and path.read_text(encoding="utf-8") != content:
        raise FileExistsError(f"Refusing to overwrite different run artifact: {path}")
    if not path.exists():
        path.write_text(content, encoding="utf-8")


def _now() -> str:
    return datetime.now(UTC).isoformat()
