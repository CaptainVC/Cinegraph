from __future__ import annotations

import json
import os
import stat
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path

from cinegraph.common.error_messages import SpeakerReviewErrorMessages
from cinegraph.common.private_corpus_policy import ALIGNED_DIRECTORY_NAME
from cinegraph.config import SpeakerReviewConfiguration
from cinegraph.config.speaker_review_filesystem import (
    CANDIDATES_FILENAME,
    PRIVATE_ARTIFACT_MAX_BYTES,
    PRIVATE_SOURCE_MAX_BYTES,
    RUN_STATE_FILENAME,
    SOURCE_MANIFEST_FILENAME,
)
from cinegraph.config.speaker_review_submission import (
    SUBMISSION_KINDS,
    SUBMISSION_RECORD_MAX_BYTES,
    SUBMISSION_REQUEST_MAX_BYTES,
    SUBMISSION_SCHEMA_VERSION,
    SUBMISSION_STAGES,
    submission_filename,
)
from cinegraph.domain.enums.enum import (
    SpeakerReviewAction,
    SpeakerReviewDisposition,
    SpeakerReviewRunStatus,
)
from cinegraph.domain.models.transcript import (
    HumanSpeakerReviewResolution,
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
from cinegraph.ingestion.speaker_review.private_io import (
    PrivateFileSnapshot,
    SpeakerReviewArtifactConflictError,
    SpeakerReviewFilesystemError,
    canonical_corpus_root,
    canonical_relative_locator,
    canonical_run_directory,
    create_run_directory,
    private_artifact_path,
    replace_private_file,
    resolve_relative_directory,
    stable_file_snapshot,
    stable_relative_file_snapshot,
    write_private_file_once,
)
from cinegraph.ingestion.speaker_review.reviewed_output import write_reviewed_outputs
from cinegraph.ingestion.speaker_review.source_manifest import (
    load_source_texts,
    source_manifest_payload,
    speaker_review_filesystem_configuration,
    validate_run_directory,
)
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
    accepted_by_human: int = 0
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
        self._filesystem_configuration = speaker_review_filesystem_configuration(
            configuration
        )

    def load(
        self,
        run_directory: Path,
    ) -> tuple[Path, SpeakerReviewRunState]:
        return load_validated_run_state(run_directory, self._configuration)

    def prepare(
        self,
        *,
        corpus_root: Path,
        seasons: tuple[int, ...],
    ) -> tuple[Path, SpeakerReviewRunState]:
        root = canonical_corpus_root(
            corpus_root,
            configuration=self._filesystem_configuration,
        )
        source_snapshots: dict[str, PrivateFileSnapshot] = {}
        seen_source_names: set[str] = set()
        candidates: list[SpeakerReviewCandidate] = []
        for season in seasons:
            source_pdf_locator = canonical_relative_locator(
                self._configuration.script_pdf_filename_template.format(
                    season=season
                ),
                configuration=self._filesystem_configuration,
            ).as_posix()
            source_pdf = stable_relative_file_snapshot(
                root,
                source_pdf_locator,
                max_bytes=PRIVATE_SOURCE_MAX_BYTES,
                configuration=self._filesystem_configuration,
            )
            discovered_directories = tuple(
                root.glob(
                    self._configuration.season_directory_glob_template.format(
                        season=season
                    )
                )
            )
            season_directories: list[Path] = []
            for directory in discovered_directories:
                try:
                    locator = directory.relative_to(root).as_posix()
                except ValueError:
                    raise SpeakerReviewFilesystemError(
                        SpeakerReviewErrorMessages.SPEAKER_REVIEW_CORPUS_PATH_INVALID
                    ) from None
                season_directories.append(
                    resolve_relative_directory(
                        root,
                        locator,
                        configuration=self._filesystem_configuration,
                    )
                )
            if len(season_directories) != 1:
                raise ValueError(
                    f"Expected one corpus directory for season {season}, found "
                    f"{len(season_directories)}."
                )
            season_locator = season_directories[0].relative_to(root).as_posix()
            aligned_directory = resolve_relative_directory(
                root,
                f"{season_locator}/{ALIGNED_DIRECTORY_NAME}",
                configuration=self._filesystem_configuration,
            )
            aligned_sources: list[tuple[Path, bytes]] = []
            for path in sorted(
                aligned_directory.glob(self._configuration.aligned_subtitle_glob)
            ):
                locator = path.relative_to(root).as_posix()
                snapshot = stable_relative_file_snapshot(
                    root,
                    locator,
                    max_bytes=PRIVATE_SOURCE_MAX_BYTES,
                    configuration=self._filesystem_configuration,
                )
                folded_name = path.name.casefold()
                if folded_name in seen_source_names:
                    raise ValueError(
                        f"Duplicate aligned subtitle filename: {path.name}"
                    )
                seen_source_names.add(folded_name)
                source_snapshots[path.name] = snapshot
                aligned_sources.append((Path(locator), snapshot.content))
            candidates.extend(
                build_speaker_review_candidates(
                    source_pdf_name=Path(source_pdf_locator).name,
                    source_pdf_content=source_pdf.content,
                    aligned_subtitles=tuple(aligned_sources),
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
        expected_run_directory = (
            root / self._configuration.run_directory_name / run_id
        )
        if os.path.lexists(expected_run_directory):
            run_directory = canonical_run_directory(
                root,
                run_id,
                configuration=self._filesystem_configuration,
            )
            state = load_run_state(run_directory / RUN_STATE_FILENAME)
            if state.run_id != run_id:
                raise SpeakerReviewFilesystemError(
                    SpeakerReviewErrorMessages.SPEAKER_REVIEW_RUN_DIRECTORY_INVALID
                )
            load_source_texts(
                run_directory,
                run_id,
                self._configuration,
            )
            return run_directory, state

        run_directory = create_run_directory(
            root,
            run_id,
            configuration=self._filesystem_configuration,
        )
        _write_jsonl(
            run_directory / CANDIDATES_FILENAME,
            tuple(item.to_dict() for item in candidate_tuple),
        )
        primary_parts = partition_batch_requests(
            requests=primary_requests,
            configuration=self._configuration,
        )
        _write_request_parts(run_directory, "primary", primary_parts)
        _write_json(
            run_directory / SOURCE_MANIFEST_FILENAME,
            source_manifest_payload(
                root,
                source_snapshots,
                self._configuration,
            ),
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

    def observe_primary_part(
        self,
        run_directory: Path,
        state: SpeakerReviewRunState,
    ) -> SpeakerReviewRunState:
        """Observe the active primary part without advancing the workflow.

        This deliberately keeps observation separate from :meth:`advance`.
        A completed part is persisted as an explicit intermediate state, so a
        worker can safely retry observation without submitting the next paid
        part or parsing/adjudicating/finalizing the review.
        """

        if state.status in {
            SpeakerReviewRunStatus.PRIMARY_PART_COMPLETED,
            SpeakerReviewRunStatus.FAILED,
        }:
            return state
        if state.status is not SpeakerReviewRunStatus.PRIMARY_SUBMITTED:
            raise RuntimeError(
                SpeakerReviewErrorMessages.PRIMARY_OBSERVATION_RECONCILIATION_REQUIRED
            )

        active_batch_id = self._active_primary_observation_batch_id(state)
        part_index = state.primary_completed_part_count

        # Exactly one provider read for the active part.  In particular, do
        # not call advance(), which may submit subsequent parts or perform
        # verdict parsing and downstream stages.
        snapshot = self._required_snapshot(active_batch_id)
        if snapshot.batch_id != active_batch_id:
            raise RuntimeError(
                SpeakerReviewErrorMessages.PRIMARY_OBSERVATION_RECONCILIATION_REQUIRED
            )
        try:
            if snapshot.status in self._configuration.terminal_batch_failure_statuses:
                return self._persist_failed_batch(run_directory, state, snapshot)
            if snapshot.status != self._configuration.successful_batch_status:
                return state

            self._download_completed_batch(
                run_directory,
                _part_stage_name("primary", part_index),
                snapshot,
            )
            updated = replace(
                state,
                status=SpeakerReviewRunStatus.PRIMARY_PART_COMPLETED,
                primary_completed_part_count=part_index + 1,
                updated_at=_now(),
            )
            save_run_state(run_directory, updated)
            return updated
        except (FileExistsError, SpeakerReviewArtifactConflictError) as error:
            raise RuntimeError(
                SpeakerReviewErrorMessages.PRIMARY_OBSERVATION_RECONCILIATION_REQUIRED
            ) from error

    def _active_primary_observation_batch_id(
        self,
        state: SpeakerReviewRunState,
    ) -> str:
        """Return the sole currently active primary batch, fail-closed.

        Observation must never guess which provider batch is active from a
        corrupted or partially written state file.  A submitted run has one
        recorded batch per completed part plus exactly one active batch, and
        the legacy singleton IDs must agree with the tuple representation.
        """

        part_count = state.primary_part_count
        completed_count = state.primary_completed_part_count
        batch_ids = state.primary_batch_ids
        input_file_ids = state.primary_input_file_ids
        valid_batch_ids = (
            isinstance(batch_ids, tuple)
            and all(
                isinstance(batch_id, str)
                and bool(batch_id)
                and batch_id == batch_id.strip()
                for batch_id in batch_ids
            )
            and len(set(batch_ids)) == len(batch_ids)
        )
        valid_input_file_ids = (
            isinstance(input_file_ids, tuple)
            and all(
                isinstance(file_id, str)
                and bool(file_id)
                and file_id == file_id.strip()
                for file_id in input_file_ids
            )
            and len(set(input_file_ids)) == len(input_file_ids)
        )
        if (
            isinstance(part_count, bool)
            or not isinstance(part_count, int)
            or isinstance(completed_count, bool)
            or not isinstance(completed_count, int)
            or part_count <= completed_count
            or completed_count < 0
            or not valid_batch_ids
            or not valid_input_file_ids
            or len(batch_ids) != completed_count + 1
            or len(input_file_ids) != completed_count + 1
            or not batch_ids
            or state.primary_batch_id != batch_ids[-1]
            or state.primary_input_file_id != input_file_ids[-1]
        ):
            raise RuntimeError(
                SpeakerReviewErrorMessages.PRIMARY_OBSERVATION_RECONCILIATION_REQUIRED
            )
        return batch_ids[completed_count]

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
        candidates = load_candidates(run_directory)
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
        candidates = load_candidates(run_directory)
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
            run_directory / self._configuration.final_decisions_filename,
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
        final_decisions = decisions or load_decisions(
            run_directory / self._configuration.final_decisions_filename
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
            for item in load_candidates(run_directory)
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
        candidates = load_candidates(run_directory)
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

        candidates = load_candidates(run_directory)
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
        prior_decisions = load_decisions(
            run_directory / self._configuration.final_decisions_filename
        )
        decisions = apply_final_review(
            decisions=prior_decisions,
            final_verdicts=verdicts,
            configuration=self._configuration,
        )
        _write_jsonl(
            run_directory
            / (
                self._configuration.retry_post_final_decisions_filename_template.format(
                    retry_count=state.final_review_retry_count
                )
                if state.final_review_retry_count
                else self._configuration.post_final_decisions_filename
            ),
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
        prior_decisions = load_decisions(
            run_directory / self._configuration.final_decisions_filename
        )
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
            for item in load_candidates(run_directory)
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
        candidates = load_candidates(run_directory)
        source_texts = load_source_texts(
            run_directory,
            state.run_id,
            self._configuration,
        )
        reviewer_models = [state.primary_model]
        if state.adjudication_batch_ids or state.adjudication_batch_id is not None:
            reviewer_models.append(state.adjudication_model)
        if state.final_review_batch_ids or state.final_review_batch_id is not None:
            reviewer_models.append(state.final_review_model)
        records = write_reviewed_outputs(
            run_directory=run_directory,
            source_texts=source_texts,
            candidates=candidates,
            decisions=decisions,
            reviewer_models=tuple(reviewer_models),
            prompt_version=state.prompt_version,
            actual_cost_usd=state.actual_total_cost_usd,
            configuration=self._configuration,
            human_queue_filename=(
                self._configuration.retry_human_queue_filename_template.format(
                    retry_count=state.final_review_retry_count
                )
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
        request_path = _request_part_path(run_directory, stage, part_index)
        metadata = {
            "cinegraph_run_id": state.run_id,
            "stage": f"speaker-review-{stage}",
            "part": str(part_index + 1),
            "prompt_version": state.prompt_version,
        }
        request_bytes = _bounded_file_bytes(request_path)
        request_hash = sha256(request_bytes).hexdigest()
        binding = {
            "schema_version": SUBMISSION_SCHEMA_VERSION,
            "request_sha256": request_hash,
            "run_id": state.run_id,
            "stage": stage,
            "part": part_index + 1,
            "prompt_version": state.prompt_version,
            "batch_endpoint": self._configuration.batch_endpoint,
            "completion_window": self._configuration.batch_completion_window,
        }
        intent_path = _submission_path(run_directory, stage, part_index, "intent")
        completed_path = _submission_path(run_directory, stage, part_index, "completed")
        intent = _read_submission_record(intent_path, completed=False)
        completed = _read_submission_record(completed_path, completed=True)
        if completed is not None and intent is None:
            raise RuntimeError(
                SpeakerReviewErrorMessages.BATCH_SUBMISSION_RECONCILIATION_REQUIRED
            )
        if completed is not None:
            if completed["binding"] != binding or intent["binding"] != binding:
                raise RuntimeError(
                    SpeakerReviewErrorMessages.BATCH_SUBMISSION_RECONCILIATION_REQUIRED
                )
            return BatchSubmission(
                str(completed["batch_id"]),
                str(completed["input_file_id"]),
                str(completed["status"]),
            )
        if intent is not None:
            raise RuntimeError(
                SpeakerReviewErrorMessages.BATCH_SUBMISSION_RECONCILIATION_REQUIRED
            )
        _write_submission_record(intent_path, {"binding": binding, "status": "intent"})
        try:
            submission = self._gateway.submit(
                request_path.name,
                request_bytes,
                self._configuration.batch_completion_window,
                metadata,
            )
            if (
                not isinstance(submission, BatchSubmission)
                or not all(
                    isinstance(value, str) and value and value.strip() == value
                    for value in (
                        submission.batch_id,
                        submission.input_file_id,
                        submission.status,
                    )
                )
            ):
                raise ValueError
        except Exception:
            # The intent is deliberately retained: the provider call may have
            # succeeded even when the client observed an exception.
            raise RuntimeError(
                SpeakerReviewErrorMessages.BATCH_SUBMISSION_RECONCILIATION_REQUIRED
            ) from None
        _write_submission_record(
            completed_path,
            {
                "binding": binding,
                "batch_id": submission.batch_id,
                "input_file_id": submission.input_file_id,
                "status": submission.status,
            },
        )
        return submission

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
        self._persist_failed_batch(run_directory, state, snapshot)
        raise RuntimeError(
            SpeakerReviewErrorMessages.BATCH_TERMINAL_FAILURE.format(
                batch_id=snapshot.batch_id,
                status=snapshot.status,
            )
        )

    def _persist_failed_batch(
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
        return updated

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
    payload = json.loads(
        stable_file_snapshot(
            path,
            max_bytes=PRIVATE_ARTIFACT_MAX_BYTES,
        ).content.decode("utf-8")
    )
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


def load_validated_run_state(
    run_directory: Path,
    configuration: SpeakerReviewConfiguration,
) -> tuple[Path, SpeakerReviewRunState]:
    expected_run_id = Path(os.path.abspath(run_directory)).name
    canonical, _ = validate_run_directory(
        run_directory,
        expected_run_id,
        configuration,
    )
    state = load_run_state(canonical / RUN_STATE_FILENAME)
    if state.run_id != expected_run_id:
        raise SpeakerReviewFilesystemError(
            SpeakerReviewErrorMessages.SPEAKER_REVIEW_RUN_DIRECTORY_INVALID
        )
    return canonical, state


def save_run_state(run_directory: Path, state: SpeakerReviewRunState) -> None:
    _write_json_atomic(run_directory / RUN_STATE_FILENAME, state.to_dict())


def load_candidates(run_directory: Path) -> tuple[SpeakerReviewCandidate, ...]:
    return tuple(
        candidate_from_dict(json.loads(line))
        for line in stable_file_snapshot(
            run_directory / CANDIDATES_FILENAME,
            max_bytes=PRIVATE_ARTIFACT_MAX_BYTES,
        )
        .content.decode("utf-8")
        .splitlines()
        if line.strip()
    )


def load_decisions(path: Path) -> tuple[SpeakerReviewDecision, ...]:
    return tuple(
        decision_from_dict(json.loads(line))
        for line in stable_file_snapshot(
            path,
            max_bytes=PRIVATE_ARTIFACT_MAX_BYTES,
        )
        .content.decode("utf-8")
        .splitlines()
        if line.strip()
    )


def decision_from_dict(payload: dict[str, object]) -> SpeakerReviewDecision:
    primary_payload = payload.get("primary_verdicts")
    if not isinstance(primary_payload, list):
        raise TypeError("Decision primary_verdicts must be a list.")
    adjudication_payload = payload.get("adjudication_verdict")
    final_payload = payload.get("final_review_verdict")
    human_payload = payload.get("human_review_resolution")
    return SpeakerReviewDecision(
        candidate_id=str(payload["candidate_id"]),
        disposition=SpeakerReviewDisposition(str(payload["disposition"])),
        speaker=str(payload["speaker"]) if payload.get("speaker") is not None else None,
        reason=str(payload["reason"]),
        primary_verdicts=tuple(
            verdict_from_dict(item)
            for item in primary_payload
            if isinstance(item, dict)
        ),
        adjudication_verdict=(
            verdict_from_dict(adjudication_payload)
            if isinstance(adjudication_payload, dict)
            else None
        ),
        final_review_verdict=(
            verdict_from_dict(final_payload)
            if isinstance(final_payload, dict)
            else None
        ),
        human_review_resolution=(
            HumanSpeakerReviewResolution(
                candidate_id=str(human_payload["candidate_id"]),
                speaker=str(human_payload["speaker"]),
                reviewer=str(human_payload["reviewer"]),
                reviewed_at=datetime.fromisoformat(str(human_payload["reviewed_at"])),
                rationale=str(human_payload["rationale"]),
            )
            if isinstance(human_payload, dict)
            else None
        ),
    )


def verdict_from_dict(payload: dict[str, object]) -> SpeakerReviewVerdict:
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
        stable_file_snapshot(
            run_directory
            / f"{_part_stage_name(stage, part_index)}-output.jsonl",
            max_bytes=PRIVATE_ARTIFACT_MAX_BYTES,
        )
        .content.decode("utf-8")
        .rstrip("\n")
        for part_index in range(part_count)
    ]
    return "\n".join(contents) + "\n"


def _write_json(path: Path, payload: object) -> None:
    _write_text_if_new_or_unchanged(
        path,
        json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
    )


def _write_json_atomic(path: Path, payload: object) -> None:
    replace_private_file(
        path.parent,
        path.name,
        (
            json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True)
            + "\n"
        ).encode("utf-8"),
    )


def _write_text_if_new_or_unchanged(path: Path, content: str) -> None:
    try:
        write_private_file_once(
            path.parent,
            path.name,
            content.encode("utf-8"),
        )
    except SpeakerReviewArtifactConflictError as error:
        raise FileExistsError("Refusing to overwrite a different run artifact.") from error


def _submission_path(
    run_directory: Path, stage: str, part_index: int, kind: str
) -> Path:
    if stage not in SUBMISSION_STAGES or part_index < 0 or kind not in SUBMISSION_KINDS:
        raise RuntimeError(
            SpeakerReviewErrorMessages.BATCH_SUBMISSION_RECONCILIATION_REQUIRED
        )
    return private_artifact_path(
        run_directory,
        submission_filename(stage, part_index + 1, kind),
    )


def _submission_exists(path: Path) -> bool:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return False
    except OSError:
        raise RuntimeError(
            SpeakerReviewErrorMessages.BATCH_SUBMISSION_RECONCILIATION_REQUIRED
        ) from None
    if not _regular_submission_file(metadata):
        raise RuntimeError(
            SpeakerReviewErrorMessages.BATCH_SUBMISSION_RECONCILIATION_REQUIRED
        )
    return True


def _regular_submission_file(metadata: os.stat_result) -> bool:
    return (
        stat.S_ISREG(metadata.st_mode)
        and metadata.st_nlink == 1
        and not (
            getattr(metadata, "st_file_attributes", 0)
            & stat.FILE_ATTRIBUTE_REPARSE_POINT
        )
    )


def _submission_file_identity(
    metadata: os.stat_result,
) -> tuple[int, int, int, int, int]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_nlink,
    )


def _bounded_file_bytes(path: Path) -> bytes:
    try:
        metadata = path.lstat()
        if (
            not _regular_submission_file(metadata)
            or metadata.st_size <= 0
            or metadata.st_size > SUBMISSION_REQUEST_MAX_BYTES
        ):
            raise OSError
        with path.open("rb") as stream:
            opened = os.fstat(stream.fileno())
            if not _regular_submission_file(opened) or _submission_file_identity(
                opened
            ) != _submission_file_identity(metadata):
                raise OSError
            content = stream.read(SUBMISSION_REQUEST_MAX_BYTES + 1)
        after = path.lstat()
        if (
            not _regular_submission_file(after)
            or _submission_file_identity(after)
            != _submission_file_identity(metadata)
            or len(content) != opened.st_size
            or len(content) > SUBMISSION_REQUEST_MAX_BYTES
        ):
            raise OSError
    except OSError:
        raise RuntimeError(
            SpeakerReviewErrorMessages.BATCH_SUBMISSION_RECONCILIATION_REQUIRED
        ) from None
    return content


def _write_submission_record(path: Path, payload: dict[str, object]) -> None:
    encoded = (
        json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode()
    if len(encoded) > SUBMISSION_RECORD_MAX_BYTES or _submission_exists(path):
        raise RuntimeError(
            SpeakerReviewErrorMessages.BATCH_SUBMISSION_RECONCILIATION_REQUIRED
        )
    descriptor = -1
    try:
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(path, flags, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = -1
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        if os.name == "posix":
            directory = os.open(
                path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
            )
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    except OSError:
        raise RuntimeError(
            SpeakerReviewErrorMessages.BATCH_SUBMISSION_RECONCILIATION_REQUIRED
        ) from None
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _read_submission_record(path: Path, *, completed: bool) -> dict[str, object] | None:
    if not _submission_exists(path):
        return None
    descriptor = -1
    try:
        metadata = path.lstat()
        if (
            not _regular_submission_file(metadata)
            or metadata.st_size > SUBMISSION_RECORD_MAX_BYTES
        ):
            raise ValueError
        flags = os.O_RDONLY | (getattr(os, "O_NOFOLLOW", 0))
        descriptor = os.open(path, flags)
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = -1
            opened = os.fstat(stream.fileno())
            if not _regular_submission_file(opened) or _submission_file_identity(
                opened
            ) != _submission_file_identity(metadata):
                raise ValueError
            raw = stream.read(SUBMISSION_RECORD_MAX_BYTES + 1)
        after = path.lstat()
        if not _regular_submission_file(after) or _submission_file_identity(
            after
        ) != _submission_file_identity(metadata):
            raise ValueError
        if len(raw) > SUBMISSION_RECORD_MAX_BYTES or not raw.endswith(b"\n"):
            raise ValueError

        def reject_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
            result: dict[str, object] = {}
            for key, item in pairs:
                if key in result:
                    raise ValueError
                result[key] = item
            return result

        value = json.loads(raw.decode("utf-8"), object_pairs_hook=reject_duplicates)
        required = (
            {"binding", "batch_id", "input_file_id", "status"}
            if completed
            else {"binding", "status"}
        )
        if not isinstance(value, dict) or set(value) != required:
            raise ValueError
        if not completed and value["status"] != "intent":
            raise ValueError
        if completed and not all(
            isinstance(value[key], str)
            and value[key]
            and value[key].strip() == value[key]
            for key in ("batch_id", "input_file_id", "status")
        ):
            raise ValueError
        canonical = (
            json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
            + "\n"
        ).encode()
        if canonical != raw or not isinstance(value["binding"], dict):
            raise ValueError
        binding = value["binding"]
        if (
            set(binding)
            != {
                "schema_version",
                "request_sha256",
                "run_id",
                "stage",
                "part",
                "prompt_version",
                "batch_endpoint",
                "completion_window",
            }
            or type(binding["schema_version"]) is not int
            or binding["schema_version"] != SUBMISSION_SCHEMA_VERSION
            or type(binding["part"]) is not int
            or binding["part"] < 1
            or not all(
                isinstance(binding[key], str) and binding[key]
                for key in (
                    "request_sha256",
                    "run_id",
                    "stage",
                    "prompt_version",
                    "batch_endpoint",
                    "completion_window",
                )
            )
            or binding["stage"] not in SUBMISSION_STAGES
        ):
            raise ValueError
        return value
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        raise RuntimeError(
            SpeakerReviewErrorMessages.BATCH_SUBMISSION_RECONCILIATION_REQUIRED
        ) from None
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _now() -> str:
    return datetime.now(UTC).isoformat()
