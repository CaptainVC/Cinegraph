from __future__ import annotations

import json
import os
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path

from cinegraph.common.error_messages import SpeakerReviewErrorMessages
from cinegraph.config import SpeakerReviewConfiguration
from cinegraph.domain.enums.enum import (
    SpeakerReviewDisposition,
    SpeakerReviewRunStatus,
)
from cinegraph.domain.models.transcript import (
    HumanSpeakerReviewResolution,
    SpeakerReviewCandidate,
    SpeakerReviewDecision,
)
from cinegraph.ingestion.speaker_review.reviewed_output import (
    ReviewedOutputRecord,
    write_reviewed_outputs,
)
from cinegraph.ingestion.speaker_review.workflow import (
    SpeakerReviewRunState,
    load_candidates,
    load_decisions,
    load_run_state,
    save_run_state,
)


@dataclass(frozen=True, slots=True)
class HumanReviewWorkbenchResult:
    path: Path
    candidate_count: int
    queue_sha256: str


@dataclass(frozen=True, slots=True)
class HumanReviewApplicationResult:
    state: SpeakerReviewRunState
    records: tuple[ReviewedOutputRecord, ...]
    resolution_count: int


class HumanSpeakerReviewWorkflow:
    """Prepare and apply complete, auditable human speaker resolutions."""

    def __init__(self, configuration: SpeakerReviewConfiguration) -> None:
        self._configuration = configuration

    def prepare_workbench(self, run_directory: Path) -> HumanReviewWorkbenchResult:
        state = load_run_state(run_directory / "run-state.json")
        self._require_human_review_state(state)
        queue_path, queue_payload, queue_hash = self._load_current_queue(
            run_directory,
            state,
        )
        workbench_path = (
            run_directory / self._configuration.human_review_workbench_filename
        )
        content = _render_workbench(
            run_id=state.run_id,
            queue_sha256=queue_hash,
            queue_payload=queue_payload,
            schema_version=self._configuration.human_resolution_schema_version,
            resolution_filename=(self._configuration.human_review_resolution_filename),
            queue_filename=queue_path.name,
        )
        _write_if_new_or_unchanged(workbench_path, content)
        return HumanReviewWorkbenchResult(
            path=workbench_path,
            candidate_count=len(queue_payload),
            queue_sha256=queue_hash,
        )

    def apply_resolution(
        self,
        *,
        run_directory: Path,
        resolution_path: Path,
    ) -> HumanReviewApplicationResult:
        state = load_run_state(run_directory / "run-state.json")
        if state.status is SpeakerReviewRunStatus.COMPLETED:
            return self._load_completed_result(run_directory, state, resolution_path)
        self._require_human_review_state(state)
        _, queue_payload, queue_hash = self._load_current_queue(run_directory, state)
        resolution_payload = _load_json_object(resolution_path)
        reviewer, reviewed_at, resolutions = self._validate_resolution(
            state=state,
            queue_payload=queue_payload,
            queue_sha256=queue_hash,
            resolution_payload=resolution_payload,
        )
        prior_decisions = load_decisions(
            self._current_decisions_path(run_directory, state)
        )
        updated_decisions = _apply_resolutions(
            prior_decisions=prior_decisions,
            resolutions=resolutions,
        )
        canonical_resolution = _canonical_resolution_payload(
            schema_version=self._configuration.human_resolution_schema_version,
            run_id=state.run_id,
            queue_sha256=queue_hash,
            reviewer=reviewer,
            reviewed_at=reviewed_at,
            resolutions=resolutions,
        )
        canonical_resolution_text = _json_text(canonical_resolution)
        canonical_resolution_path = (
            run_directory / self._configuration.human_review_resolution_filename
        )
        _write_if_new_or_unchanged(
            canonical_resolution_path,
            canonical_resolution_text,
        )
        _write_jsonl_if_new_or_unchanged(
            run_directory / self._configuration.post_human_decisions_filename,
            tuple(item.to_dict() for item in updated_decisions),
        )
        resolution_hash = _sha256(canonical_resolution_text)
        ledger = {
            "schema_version": self._configuration.human_resolution_schema_version,
            "run_id": state.run_id,
            "queue_sha256": queue_hash,
            "resolution_sha256": resolution_hash,
            "reviewer": reviewer,
            "reviewed_at": reviewed_at.isoformat(),
            "resolution_count": len(resolutions),
        }
        _write_if_new_or_unchanged(
            run_directory / self._configuration.human_review_ledger_filename,
            _json_text(ledger),
        )
        records = self._write_promoted_outputs(
            run_directory=run_directory,
            state=state,
            decisions=updated_decisions,
            reviewer=reviewer,
            reviewed_at=reviewed_at,
        )
        if not records:
            raise RuntimeError(
                SpeakerReviewErrorMessages.HUMAN_REVIEW_DECISION_SET_MISMATCH
            )
        updated_state = replace(
            state,
            schema_version=self._configuration.schema_version,
            status=SpeakerReviewRunStatus.COMPLETED,
            updated_at=datetime.now(UTC).isoformat(),
            accepted_by_human=len(resolutions),
            needs_human=0,
        )
        save_run_state(run_directory, updated_state)
        return HumanReviewApplicationResult(
            state=updated_state,
            records=records,
            resolution_count=len(resolutions),
        )

    def _validate_resolution(
        self,
        *,
        state: SpeakerReviewRunState,
        queue_payload: list[dict[str, object]],
        queue_sha256: str,
        resolution_payload: dict[str, object],
    ) -> tuple[
        str,
        datetime,
        tuple[HumanSpeakerReviewResolution, ...],
    ]:
        if (
            resolution_payload.get("schema_version")
            != self._configuration.human_resolution_schema_version
        ):
            raise ValueError(SpeakerReviewErrorMessages.HUMAN_REVIEW_SCHEMA_MISMATCH)
        if resolution_payload.get("run_id") != state.run_id:
            raise ValueError(SpeakerReviewErrorMessages.HUMAN_REVIEW_RUN_ID_MISMATCH)
        if resolution_payload.get("queue_sha256") != queue_sha256:
            raise ValueError(
                SpeakerReviewErrorMessages.HUMAN_REVIEW_QUEUE_HASH_MISMATCH
            )
        reviewer = resolution_payload.get("reviewer")
        if (
            not isinstance(reviewer, str)
            or not reviewer
            or reviewer.strip() != reviewer
        ):
            raise ValueError(SpeakerReviewErrorMessages.HUMAN_REVIEW_REVIEWER_REQUIRED)
        reviewed_at = _parse_reviewed_at(resolution_payload.get("reviewed_at"))
        raw_decisions = resolution_payload.get("decisions")
        if not isinstance(raw_decisions, list):
            raise TypeError(
                SpeakerReviewErrorMessages.HUMAN_REVIEW_RESOLUTION_MALFORMED
            )
        candidates = _queue_candidates(queue_payload)
        raw_by_id: dict[str, dict[str, object]] = {}
        for raw_decision in raw_decisions:
            if not isinstance(raw_decision, dict):
                raise TypeError(
                    SpeakerReviewErrorMessages.HUMAN_REVIEW_RESOLUTION_MALFORMED
                )
            candidate_id = raw_decision.get("candidate_id")
            if not isinstance(candidate_id, str) or candidate_id in raw_by_id:
                raise ValueError(
                    SpeakerReviewErrorMessages.HUMAN_REVIEW_DECISION_SET_MISMATCH
                )
            raw_by_id[candidate_id] = raw_decision
        if set(raw_by_id) != set(candidates):
            raise ValueError(
                SpeakerReviewErrorMessages.HUMAN_REVIEW_DECISION_SET_MISMATCH
            )
        resolutions: list[HumanSpeakerReviewResolution] = []
        for candidate_id in sorted(candidates):
            raw_decision = raw_by_id[candidate_id]
            speaker = raw_decision.get("speaker")
            if (
                not isinstance(speaker, str)
                or speaker not in candidates[candidate_id].allowed_speakers
            ):
                raise ValueError(
                    SpeakerReviewErrorMessages.HUMAN_REVIEW_SPEAKER_NOT_ALLOWED
                )
            rationale = raw_decision.get("rationale")
            if (
                not isinstance(rationale, str)
                or not rationale
                or rationale.strip() != rationale
            ):
                raise ValueError(
                    SpeakerReviewErrorMessages.HUMAN_REVIEW_RATIONALE_REQUIRED
                )
            resolutions.append(
                HumanSpeakerReviewResolution(
                    candidate_id=candidate_id,
                    speaker=speaker,
                    reviewer=reviewer,
                    reviewed_at=reviewed_at,
                    rationale=rationale,
                )
            )
        return reviewer, reviewed_at, tuple(resolutions)

    def _load_current_queue(
        self,
        run_directory: Path,
        state: SpeakerReviewRunState,
    ) -> tuple[Path, list[dict[str, object]], str]:
        queue_path = self._current_queue_path(run_directory, state)
        if not queue_path.exists():
            raise FileNotFoundError(
                SpeakerReviewErrorMessages.HUMAN_REVIEW_QUEUE_MISSING
            )
        content = queue_path.read_text(encoding="utf-8")
        try:
            payload = json.loads(content)
        except json.JSONDecodeError as error:
            raise ValueError(
                SpeakerReviewErrorMessages.HUMAN_REVIEW_RESOLUTION_MALFORMED
            ) from error
        if not isinstance(payload, list):
            raise TypeError(
                SpeakerReviewErrorMessages.HUMAN_REVIEW_RESOLUTION_MALFORMED
            )
        if not payload:
            raise ValueError(SpeakerReviewErrorMessages.HUMAN_REVIEW_QUEUE_EMPTY)
        if not all(isinstance(item, dict) for item in payload):
            raise TypeError(
                SpeakerReviewErrorMessages.HUMAN_REVIEW_RESOLUTION_MALFORMED
            )
        _queue_candidates(payload)
        return queue_path, payload, _sha256(content)

    def _current_queue_path(
        self,
        run_directory: Path,
        state: SpeakerReviewRunState,
    ) -> Path:
        if state.final_review_retry_count:
            filename = self._configuration.retry_human_queue_filename_template.format(
                retry_count=state.final_review_retry_count
            )
        elif (
            run_directory / self._configuration.remaining_human_queue_filename
        ).exists():
            filename = self._configuration.remaining_human_queue_filename
        else:
            filename = self._configuration.initial_human_queue_filename
        return run_directory / filename

    def _current_decisions_path(
        self,
        run_directory: Path,
        state: SpeakerReviewRunState,
    ) -> Path:
        if state.final_review_retry_count:
            filename = (
                self._configuration.retry_post_final_decisions_filename_template.format(
                    retry_count=state.final_review_retry_count
                )
            )
        elif state.final_review_part_count:
            filename = self._configuration.post_final_decisions_filename
        else:
            filename = self._configuration.final_decisions_filename
        return run_directory / filename

    def _write_promoted_outputs(
        self,
        *,
        run_directory: Path,
        state: SpeakerReviewRunState,
        decisions: tuple[SpeakerReviewDecision, ...],
        reviewer: str,
        reviewed_at: datetime,
    ) -> tuple[ReviewedOutputRecord, ...]:
        source_manifest = _load_json_object(run_directory / "source-manifest.json")
        raw_sources = source_manifest.get("sources")
        if not isinstance(raw_sources, dict):
            raise TypeError(
                SpeakerReviewErrorMessages.HUMAN_REVIEW_RESOLUTION_MALFORMED
            )
        source_paths = {
            str(filename): Path(str(path)) for filename, path in raw_sources.items()
        }
        reviewers = [state.primary_model]
        if state.adjudication_part_count:
            reviewers.append(state.adjudication_model)
        if state.final_review_part_count:
            reviewers.append(state.final_review_model)
        reviewers.append(reviewer)
        return write_reviewed_outputs(
            run_directory=run_directory,
            source_paths=source_paths,
            candidates=load_candidates(run_directory),
            decisions=decisions,
            reviewer_models=tuple(reviewers),
            prompt_version=state.prompt_version,
            actual_cost_usd=state.actual_total_cost_usd,
            configuration=self._configuration,
            reviewed_at=reviewed_at.isoformat(),
        )

    def _load_completed_result(
        self,
        run_directory: Path,
        state: SpeakerReviewRunState,
        resolution_path: Path,
    ) -> HumanReviewApplicationResult:
        canonical_path = (
            run_directory / self._configuration.human_review_resolution_filename
        )
        if not canonical_path.exists():
            raise RuntimeError(
                SpeakerReviewErrorMessages.RUN_STATE_CONFLICT.format(
                    status=state.status.value
                )
            )
        if resolution_path.resolve() != canonical_path.resolve():
            supplied = _comparable_resolution_text(_load_json_object(resolution_path))
            canonical = _comparable_resolution_text(_load_json_object(canonical_path))
            if supplied != canonical:
                raise FileExistsError(
                    SpeakerReviewErrorMessages.REVIEWED_OUTPUT_CONFLICT.format(
                        path=canonical_path
                    )
                )
        ledger = _load_json_object(
            run_directory / self._configuration.human_review_ledger_filename
        )
        return HumanReviewApplicationResult(
            state=state,
            records=(),
            resolution_count=int(ledger["resolution_count"]),
        )

    @staticmethod
    def _require_human_review_state(state: SpeakerReviewRunState) -> None:
        if state.status is not SpeakerReviewRunStatus.NEEDS_HUMAN:
            raise RuntimeError(
                SpeakerReviewErrorMessages.RUN_STATE_CONFLICT.format(
                    status=state.status.value
                )
            )


def _apply_resolutions(
    *,
    prior_decisions: tuple[SpeakerReviewDecision, ...],
    resolutions: tuple[HumanSpeakerReviewResolution, ...],
) -> tuple[SpeakerReviewDecision, ...]:
    resolution_by_id = {item.candidate_id: item for item in resolutions}
    unresolved_ids = {
        item.candidate_id
        for item in prior_decisions
        if item.disposition is SpeakerReviewDisposition.NEEDS_HUMAN
    }
    if unresolved_ids != set(resolution_by_id):
        raise ValueError(SpeakerReviewErrorMessages.HUMAN_REVIEW_DECISION_SET_MISMATCH)
    return tuple(
        replace(
            decision,
            disposition=SpeakerReviewDisposition.HUMAN_REVIEW_ACCEPTED,
            speaker=resolution_by_id[decision.candidate_id].speaker,
            reason="Human reviewer resolved the remaining ambiguity.",
            human_review_resolution=resolution_by_id[decision.candidate_id],
        )
        if decision.candidate_id in resolution_by_id
        else decision
        for decision in prior_decisions
    )


def _queue_candidates(
    queue_payload: list[dict[str, object]],
) -> dict[str, SpeakerReviewCandidate]:
    from cinegraph.ingestion.speaker_review.candidates import candidate_from_dict

    candidates: dict[str, SpeakerReviewCandidate] = {}
    for item in queue_payload:
        raw_candidate = item.get("candidate")
        raw_decision = item.get("decision")
        if not isinstance(raw_candidate, dict) or not isinstance(raw_decision, dict):
            raise TypeError(
                SpeakerReviewErrorMessages.HUMAN_REVIEW_RESOLUTION_MALFORMED
            )
        candidate = candidate_from_dict(raw_candidate)
        if (
            raw_decision.get("candidate_id") != candidate.candidate_id
            or raw_decision.get("disposition")
            != SpeakerReviewDisposition.NEEDS_HUMAN.value
        ):
            raise ValueError(
                SpeakerReviewErrorMessages.HUMAN_REVIEW_DECISION_SET_MISMATCH
            )
        if candidate.candidate_id in candidates:
            raise ValueError(
                SpeakerReviewErrorMessages.HUMAN_REVIEW_DECISION_SET_MISMATCH
            )
        candidates[candidate.candidate_id] = candidate
    return candidates


def _parse_reviewed_at(value: object) -> datetime:
    if not isinstance(value, str):
        raise TypeError(SpeakerReviewErrorMessages.HUMAN_REVIEW_TIMESTAMP_INVALID)
    try:
        reviewed_at = datetime.fromisoformat(value)
    except ValueError as error:
        raise ValueError(
            SpeakerReviewErrorMessages.HUMAN_REVIEW_TIMESTAMP_INVALID
        ) from error
    if reviewed_at.tzinfo is None or reviewed_at.utcoffset() is None:
        raise ValueError(SpeakerReviewErrorMessages.HUMAN_REVIEW_TIMESTAMP_INVALID)
    return reviewed_at


def _canonical_resolution_payload(
    *,
    schema_version: int,
    run_id: str,
    queue_sha256: str,
    reviewer: str,
    reviewed_at: datetime,
    resolutions: tuple[HumanSpeakerReviewResolution, ...],
) -> dict[str, object]:
    return {
        "schema_version": schema_version,
        "run_id": run_id,
        "queue_sha256": queue_sha256,
        "reviewer": reviewer,
        "reviewed_at": reviewed_at.isoformat(),
        "decisions": [
            {
                "candidate_id": item.candidate_id,
                "speaker": item.speaker,
                "rationale": item.rationale,
            }
            for item in sorted(resolutions, key=lambda item: item.candidate_id)
        ],
    }


def _comparable_resolution_text(payload: dict[str, object]) -> str:
    normalized = dict(payload)
    raw_decisions = normalized.get("decisions")
    if isinstance(raw_decisions, list) and all(
        isinstance(item, dict) for item in raw_decisions
    ):
        normalized["decisions"] = sorted(
            raw_decisions,
            key=lambda item: str(item.get("candidate_id", "")),
        )
    return _json_text(normalized)


def _load_json_object(path: Path) -> dict[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(
            SpeakerReviewErrorMessages.HUMAN_REVIEW_RESOLUTION_MALFORMED
        ) from error
    if not isinstance(payload, dict):
        raise TypeError(SpeakerReviewErrorMessages.HUMAN_REVIEW_RESOLUTION_MALFORMED)
    return payload


def _write_jsonl_if_new_or_unchanged(
    path: Path,
    items: tuple[dict[str, object], ...],
) -> None:
    content = "".join(
        json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n" for item in items
    )
    _write_if_new_or_unchanged(path, content)


def _write_if_new_or_unchanged(path: Path, content: str) -> None:
    if path.exists() and path.read_text(encoding="utf-8") != content:
        raise FileExistsError(
            SpeakerReviewErrorMessages.REVIEWED_OUTPUT_CONFLICT.format(path=path)
        )
    if not path.exists():
        path.write_text(content, encoding="utf-8")
    if os.name != "nt":
        path.chmod(0o600)


def _json_text(payload: object) -> str:
    return json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n"


def _sha256(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def _render_workbench(
    *,
    run_id: str,
    queue_sha256: str,
    queue_payload: list[dict[str, object]],
    schema_version: int,
    resolution_filename: str,
    queue_filename: str,
) -> str:
    payload = {
        "schema_version": schema_version,
        "run_id": run_id,
        "queue_sha256": queue_sha256,
        "resolution_filename": resolution_filename,
        "queue_filename": queue_filename,
        "items": queue_payload,
    }
    embedded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    embedded = (
        embedded.replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )
    return _WORKBENCH_TEMPLATE.replace("__CINEGRAPH_REVIEW_PAYLOAD__", embedded)


_WORKBENCH_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; object-src 'none'; base-uri 'none'; form-action 'none'">
  <title>CineGraph Private Speaker Review</title>
  <style>
    :root { color-scheme: dark; font-family: Inter, ui-sans-serif, system-ui, sans-serif; }
    * { box-sizing: border-box; }
    body { margin: 0; background: #080b12; color: #eef2ff; }
    main { width: min(1120px, calc(100% - 32px)); margin: 28px auto 80px; }
    header, .card { background: #111827; border: 1px solid #263244; border-radius: 18px; box-shadow: 0 18px 60px #0007; }
    header { padding: 24px; margin-bottom: 18px; }
    h1, h2, p { margin-top: 0; }
    h1 { margin-bottom: 8px; font-size: clamp(1.55rem, 4vw, 2.25rem); }
    .muted { color: #9ca9bd; }
    .privacy { color: #9ee7c4; font-weight: 650; }
    .toolbar { display: grid; grid-template-columns: 1fr auto; gap: 14px; align-items: end; margin-top: 20px; }
    label { display: block; color: #c8d2e2; font-size: .88rem; font-weight: 700; }
    input, textarea { width: 100%; margin-top: 7px; border: 1px solid #34435a; border-radius: 10px; background: #0b1220; color: #f8fafc; padding: 11px 12px; font: inherit; }
    textarea { min-height: 88px; resize: vertical; }
    .progress { min-width: 180px; text-align: right; }
    progress { width: 100%; height: 12px; accent-color: #63e6be; }
    .card { padding: clamp(18px, 4vw, 30px); }
    .meta { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 18px; }
    .pill { background: #1b2638; color: #c9d6e8; border-radius: 999px; padding: 6px 10px; font-size: .78rem; }
    .dialogue { font-size: clamp(1.2rem, 3vw, 1.65rem); line-height: 1.45; padding: 20px; margin: 16px 0; border-left: 4px solid #63e6be; background: #0b1220; border-radius: 0 12px 12px 0; }
    .speaker-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 10px; margin: 12px 0 22px; }
    button { border: 1px solid #42526a; border-radius: 11px; padding: 11px 14px; background: #172235; color: #eef2ff; font: inherit; font-weight: 750; cursor: pointer; }
    button:hover { border-color: #63e6be; }
    button.selected { background: #176b57; border-color: #63e6be; }
    button.primary { background: #6d5dfc; border-color: #9b91ff; }
    a.download { display: inline-block; border-radius: 11px; padding: 11px 14px; background: #176b57; color: #fff; font-weight: 750; text-decoration: none; }
    .export-actions { display: flex; flex-wrap: wrap; align-items: center; gap: 10px; margin-top: 10px; }
    button:disabled { opacity: .45; cursor: not-allowed; }
    .columns { display: grid; grid-template-columns: 1.15fr .85fr; gap: 18px; }
    .panel { background: #0c1321; border: 1px solid #26344a; border-radius: 12px; padding: 16px; }
    .evidence { border-top: 1px solid #26344a; padding: 12px 0; }
    .evidence:first-of-type { border-top: 0; padding-top: 0; }
    .evidence p { margin-bottom: 5px; line-height: 1.4; }
    .opinion { margin: 0 0 10px; padding: 10px; border-radius: 9px; background: #111b2b; }
    .actions { display: flex; flex-wrap: wrap; justify-content: space-between; gap: 10px; margin-top: 20px; }
    .actions div { display: flex; flex-wrap: wrap; gap: 10px; }
    .error { min-height: 24px; margin-top: 12px; color: #ff9b9b; font-weight: 700; }
    @media (max-width: 760px) { .columns, .toolbar { grid-template-columns: 1fr; } .progress { text-align: left; } }
  </style>
</head>
<body>
<main>
  <header>
    <h1>CineGraph Private Speaker Review</h1>
    <p class="privacy">Offline workbench: no network requests, external scripts, fonts, or analytics.</p>
    <p class="muted" id="run-meta"></p>
    <div class="toolbar">
      <label>Reviewer identity
        <input id="reviewer" autocomplete="name" placeholder="Your name or stable reviewer ID">
      </label>
      <div class="progress"><strong id="progress-text"></strong><progress id="progress" max="1" value="0"></progress></div>
    </div>
  </header>
  <section class="card">
    <div class="meta" id="meta"></div>
    <h2 id="proposed"></h2>
    <div class="dialogue" id="dialogue"></div>
    <label>Select the verified speaker</label>
    <div class="speaker-grid" id="speakers"></div>
    <div class="columns">
      <section class="panel"><h3>Supplied evidence</h3><div id="evidence"></div></section>
      <section class="panel"><h3>Automated review trail</h3><div id="opinions"></div></section>
    </div>
    <label style="margin-top:18px">Human rationale
      <textarea id="rationale" placeholder="Briefly state why this speaker is correct using the displayed context."></textarea>
    </label>
    <div class="error" id="error"></div>
    <div class="actions">
      <div><button id="previous">Previous</button><button id="next">Next</button><button id="unresolved">Next unresolved</button></div>
      <button class="primary" id="export">Prepare completed resolution</button>
    </div>
    <section class="panel" id="export-panel" style="margin-top:18px" hidden>
      <label>Validated resolution preview
        <textarea id="resolution-preview" readonly></textarea>
      </label>
      <div class="export-actions">
        <a class="download" id="download" download>Download human-review-resolution.json</a>
        <button id="copy-resolution">Copy resolution JSON</button>
        <span class="privacy" id="export-status" role="status"></span>
      </div>
    </section>
  </section>
</main>
<script>
const DATA=__CINEGRAPH_REVIEW_PAYLOAD__;
const key=`cinegraph-review:${DATA.run_id}:${DATA.queue_sha256}`;
let saved={reviewer:"",decisions:{}};
try { saved=JSON.parse(localStorage.getItem(key))||saved; } catch (_) {}
let index=0;
const el=id=>document.getElementById(id);
const text=(node,value)=>{node.textContent=value??"";};
const escSummary=v=>typeof v==="number"?v.toFixed(2):"n/a";
function persist(){saved.reviewer=el("reviewer").value;try{localStorage.setItem(key,JSON.stringify(saved));}catch(_){}}
function current(){return DATA.items[index];}
function trail(decision){
  const items=[...(decision.primary_verdicts||[])];
  if(decision.adjudication_verdict)items.push(decision.adjudication_verdict);
  if(decision.final_review_verdict)items.push(decision.final_review_verdict);
  return items;
}
function render(){
  const item=current(),candidate=item.candidate,decision=item.decision;
  const existing=saved.decisions[candidate.candidate_id]||{};
  el("reviewer").value=saved.reviewer||"";
  text(el("run-meta"),`Run ${DATA.run_id} · Queue ${DATA.queue_filename} · ${DATA.items.length} decisions`);
  el("meta").replaceChildren(...[
    `Case ${index+1} of ${DATA.items.length}`,
    `S${String(candidate.episode.season).padStart(2,"0")}E${String(candidate.episode.episode).padStart(2,"0")}`,
    `Cue ${candidate.cue_number}`,
    candidate.candidate_id
  ].map(value=>{const span=document.createElement("span");span.className="pill";text(span,value);return span;}));
  text(el("proposed"),`Automated candidate: ${candidate.proposed_speaker}`);
  text(el("dialogue"),candidate.dialogue_text);
  el("speakers").replaceChildren(...candidate.allowed_speakers.map((speaker,position)=>{
    const button=document.createElement("button");
    text(button,`${position+1}. ${speaker}`);
    if(existing.speaker===speaker)button.classList.add("selected");
    button.onclick=()=>{saved.decisions[candidate.candidate_id]={...existing,speaker,rationale:el("rationale").value};persist();render();};
    return button;
  }));
  el("evidence").replaceChildren(...candidate.evidence.map(entry=>{
    const div=document.createElement("div");div.className="evidence";
    const heading=document.createElement("p");const strong=document.createElement("strong");
    text(strong,`${entry.speaker} · ${entry.source} · score ${escSummary(entry.similarity_score)}`);heading.append(strong);
    const body=document.createElement("p");text(body,entry.text);div.append(heading,body);return div;
  }));
  const opinions=trail(decision);
  el("opinions").replaceChildren(...opinions.map(opinion=>{
    const div=document.createElement("div");div.className="opinion";
    const title=document.createElement("strong");text(title,`${opinion.pass_id}: ${opinion.action} → ${opinion.speaker} (${escSummary(opinion.confidence)})`);
    const body=document.createElement("p");body.className="muted";body.style.margin="6px 0 0";text(body,opinion.rationale);div.append(title,body);return div;
  }));
  el("rationale").value=existing.rationale||"";
  el("previous").disabled=index===0;el("next").disabled=index===DATA.items.length-1;
  updateProgress();
  text(el("error"),"");
}
function capture(){const id=current().candidate.candidate_id;const prior=saved.decisions[id]||{};saved.decisions[id]={...prior,rationale:el("rationale").value.trim()};persist();}
function isComplete(item){const value=saved.decisions[item.candidate.candidate_id];return item.candidate.allowed_speakers.includes(value?.speaker)&&Boolean(value?.rationale?.trim());}
function updateProgress(){const complete=DATA.items.filter(isComplete).length;text(el("progress-text"),`${complete} / ${DATA.items.length} complete`);el("progress").max=DATA.items.length;el("progress").value=complete;}
function move(delta){capture();index=Math.max(0,Math.min(DATA.items.length-1,index+delta));render();}
el("reviewer").addEventListener("input",persist);el("rationale").addEventListener("input",()=>{capture();updateProgress();});
el("previous").onclick=()=>move(-1);el("next").onclick=()=>move(1);
el("unresolved").onclick=()=>{capture();const found=DATA.items.findIndex((item,i)=>i>index&&!isComplete(item));index=found>=0?found:DATA.items.findIndex(item=>!isComplete(item));if(index<0)index=0;render();};
el("export").onclick=()=>{
  capture();const reviewer=el("reviewer").value.trim();
  if(!reviewer){text(el("error"),"Enter a reviewer identity before exporting.");return;}
  const decisions=[];
  for(const item of DATA.items){const id=item.candidate.candidate_id,value=saved.decisions[id];if(!isComplete(item)){text(el("error"),`Complete an allowlisted speaker and rationale for ${id}.`);return;}decisions.push({candidate_id:id,speaker:value.speaker,rationale:value.rationale.trim()});}
  const output={schema_version:DATA.schema_version,run_id:DATA.run_id,queue_sha256:DATA.queue_sha256,reviewer,reviewed_at:new Date().toISOString(),decisions};
  const serialized=JSON.stringify(output,null,2)+"\\n";el("resolution-preview").value=serialized;el("download").href="data:application/json;charset=utf-8,"+encodeURIComponent(serialized);el("download").download=DATA.resolution_filename;el("export-panel").hidden=false;text(el("export-status"),"");text(el("error"),"");
};
el("copy-resolution").onclick=async()=>{const preview=el("resolution-preview");let copied=false;try{await navigator.clipboard.writeText(preview.value);copied=true;}catch(_){preview.focus();preview.select();copied=document.execCommand("copy");}text(el("export-status"),copied?"Copied to clipboard.":"Copy unavailable; select the preview manually.");};
document.addEventListener("keydown",event=>{if(event.target.matches("input,textarea"))return;const number=Number(event.key);const speakers=current().candidate.allowed_speakers;if(number>=1&&number<=speakers.length){const id=current().candidate.candidate_id;saved.decisions[id]={...(saved.decisions[id]||{}),speaker:speakers[number-1]};persist();render();}else if(event.key==="ArrowRight")move(1);else if(event.key==="ArrowLeft")move(-1);});
render();
</script>
</body>
</html>
"""
