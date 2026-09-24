from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from scripts import run_private_speaker_review_final_review as root


def _rooted_directory(path: Path) -> None:
    path.chmod(0o700)
    root_stat = path.stat()
    root.ROOT_UID = root_stat.st_uid  # type: ignore[misc]
    root.ROOT_GID = root_stat.st_gid  # type: ignore[misc]
    root.RECEIPTS_ROOT = path  # type: ignore[misc]


def _fallback_state(run_id: str) -> dict[str, object]:
    return {
        "schema_version": root.SPEAKER_REVIEW_SCHEMA_VERSION,
        "run_id": run_id,
        "status": "final_review_prepared",
        "created_at": "2026-09-23T00:00:00+00:00",
        "updated_at": "2026-09-23T00:01:00+00:00",
        "candidate_count": 2,
        "primary_model": root.SPEAKER_PRIMARY_REVIEW_MODEL,
        "adjudication_model": root.SPEAKER_ADJUDICATION_MODEL,
        "prompt_version": root.SPEAKER_REVIEW_PROMPT_VERSION,
        "maximum_cost_usd": root.MAXIMUM_RUN_COST_USD,
        "estimated_primary_cost_usd": 0.1,
        "actual_primary_cost_usd": 0.1,
        "actual_adjudication_cost_usd": 0.2,
        "final_review_model": root.SPEAKER_FINAL_REVIEW_MODEL,
        "actual_final_review_cost_usd": 0.0,
        "primary_batch_id": "batch-primary",
        "primary_input_file_id": "file-primary",
        "adjudication_batch_id": "batch-adjudication",
        "adjudication_input_file_id": "file-adjudication",
        "primary_part_count": 1,
        "primary_completed_part_count": 1,
        "primary_batch_ids": ["batch-primary"],
        "primary_input_file_ids": ["file-primary"],
        "adjudication_part_count": 1,
        "adjudication_completed_part_count": 1,
        "adjudication_batch_ids": ["batch-adjudication"],
        "adjudication_input_file_ids": ["file-adjudication"],
        "final_review_part_count": 1,
        "final_review_completed_part_count": 0,
        "final_review_batch_ids": [],
        "final_review_input_file_ids": [],
        "final_review_batch_id": None,
        "final_review_input_file_id": None,
        "final_review_retry_count": 0,
        "accepted_by_consensus": 1,
        "accepted_by_adjudication": 0,
        "accepted_by_final_review": 0,
        "accepted_by_human": 0,
        "needs_human": 1,
        "actual_total_cost_usd": 0.3,
    }


def _state_bytes(value: dict[str, object]) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()


def test_isolated_startup_is_provider_free() -> None:
    completed = subprocess.run(
        [sys.executable, "-I", "-S", "-B", "scripts/run_private_speaker_review_final_review.py"],
        cwd=Path(__file__).parents[3],
        capture_output=True,
        check=False,
        timeout=30,
    )
    assert completed.returncode == 2
    assert completed.stdout == b""
    assert b"ModuleNotFoundError" not in completed.stderr


def test_decode_rejects_duplicate_and_nonfinite_values() -> None:
    with pytest.raises(root.FinalReviewSubmissionError):
        root._decode(b'{"a":1,"a":2}\n')
    with pytest.raises(root.FinalReviewSubmissionError):
        root._decode(b'{"a":NaN}\n')


def test_isolated_state_validation_enforces_runtime_shape_and_bounds(tmp_path: Path) -> None:
    run = tmp_path / "speaker-review-0123456789abcdef"
    state = _fallback_state(run.name)
    assert root._state_from_raw(_state_bytes(state), run).run_id == run.name

    for changed in (
        {"final_review_part_count": root.MAXIMUM_REVIEW_PART_COUNT + 1},
        {"primary_model": "unexpected-model"},
        {"needs_human": 2},
        {"actual_total_cost_usd": 6.0},
    ):
        with pytest.raises(ValueError):
            root._state_from_raw(_state_bytes({**state, **changed}), run)


def test_write_once_preserves_standalone_pending_and_repairs_linked_pair(tmp_path: Path) -> None:
    _rooted_directory(tmp_path)
    target = tmp_path / "record.json"
    pending = tmp_path / ".record.json.pending"
    pending.write_bytes(b'{"attacker":true}\n')
    pending.chmod(0o600)
    with pytest.raises(root.FinalReviewSubmissionError):
        root._write_once(target, {"safe": True})
    assert not target.exists()
    assert pending.read_bytes() == b'{"attacker":true}\n'

    pending.unlink()
    encoded = root._canonical({"safe": True})
    pending.write_bytes(encoded)
    pending.chmod(0o600)
    os.link(pending, target)
    assert root._write_once(target, {"safe": True}) == root._sha(encoded)
    assert target.exists() and not pending.exists()


def test_container_name_mismatch_is_not_removed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(root, "_container_identity_is_exact", lambda *args: False)
    monkeypatch.setattr(root.subprocess, "run", lambda args, **kwargs: calls.append(list(args)))
    request = {"run_id": "speaker-review-0123456789abcdef"}
    root._cleanup_worker(request, tmp_path, {})
    assert calls == []


def test_secret_mount_source_must_match_the_root_environment_secret(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    secret = tmp_path / "compose-secret"
    secret.write_bytes(b"sk-test-value")
    secret.chmod(0o600)
    metadata = secret.stat()
    monkeypatch.setattr(root, "ROOT_UID", metadata.st_uid)
    monkeypatch.setattr(root, "ROOT_GID", metadata.st_gid)
    monkeypatch.setattr(
        root,
        "_environment_values",
        lambda: {"OPENAI_API_KEY": "sk-test-value"},
    )

    assert root._secret_source_is_exact({"Source": secret.as_posix()})
    monkeypatch.setattr(
        root,
        "_environment_values",
        lambda: {"OPENAI_API_KEY": "sk-different-value"},
    )
    assert not root._secret_source_is_exact({"Source": secret.as_posix()})


def test_receipt_payload_contains_intent_and_complete_post_bindings() -> None:
    contents = {root.STATE_NAME: b"state"}
    intent = {"run_id": "speaker-review-0123456789abcdef", "status": "intent"}
    result = {"status": "submitted"}
    receipt = root._receipt_payload(intent, intent_sha="a" * 64, result=result, contents=contents)
    assert receipt["intent_sha256"] == "a" * 64
    assert set(receipt["post_digests"]) == {"state", "artifacts", "journals", "outputs", "derived"}
    assert set(receipt["post_hashes"]) == {"artifacts", "journals", "outputs", "derived"}


def test_isolated_cost_estimate_covers_every_final_review_part(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = root._State(
        {
            "final_review_model": root.SPEAKER_FINAL_REVIEW_MODEL,
            "final_review_part_count": 2,
            "prompt_version": "speaker-review-v1",
        }
    )
    contents = {
        "final-review-part-0001-requests.jsonl": b'{"body":{"max_output_tokens":100}}\n',
        "final-review-part-0002-requests.jsonl": b'{"body":{"max_output_tokens":200}}\n',
    }
    monkeypatch.setattr(root, "worker", None)

    combined = root._estimate_final_review_cost(contents, state)
    first_only = root._estimate_final_review_cost(
        {"final-review-part-0001-requests.jsonl": contents["final-review-part-0001-requests.jsonl"]},
        root._State(
            {
                "final_review_model": root.SPEAKER_FINAL_REVIEW_MODEL,
                "final_review_part_count": 1,
            }
        ),
    )

    assert combined > first_only > 0
    assert root._runtime_parameters(state)["batch_endpoint"] == root.BATCH_ENDPOINT


def test_application_journals_are_canonical_and_bound_to_submitted_state() -> None:
    run_id = "speaker-review-0123456789abcdef"
    request_bytes = b'{"body":{"max_output_tokens":100}}\n'
    request = {"run_id": run_id}
    state = root._State(
        {
            "status": "final_review_submitted",
            "run_id": run_id,
            "prompt_version": "speaker-review-v1",
            "final_review_model": root.SPEAKER_FINAL_REVIEW_MODEL,
            "final_review_batch_id": "batch-private",
            "final_review_input_file_id": "file-private",
            "final_review_batch_ids": ["batch-private"],
            "final_review_input_file_ids": ["file-private"],
        }
    )
    binding = {
        "schema_version": 1,
        "request_sha256": root._sha(request_bytes),
        "run_id": run_id,
        "stage": "final-review",
        "part": 1,
        "prompt_version": "speaker-review-v1",
        "batch_endpoint": root.BATCH_ENDPOINT,
        "completion_window": root.BATCH_COMPLETION_WINDOW,
    }
    intent = root._canonical({"binding": binding, "status": "intent"})
    completed = root._canonical(
        {
            "batch_id": "batch-private",
            "binding": binding,
            "input_file_id": "file-private",
            "status": "submitted",
        }
    )
    contents = {
        "final-review-part-0001-requests.jsonl": request_bytes,
        root._FINAL_INTENT_NAME: intent,
        root._FINAL_COMPLETED_NAME: completed,
    }

    assert root._validate_application_journals(
        contents, request, state, root._sha(request_bytes)
    ) == (True, True)

    contents[root._FINAL_COMPLETED_NAME] = completed.replace(b"batch-private", b"batch-tamper")
    with pytest.raises(root.FinalReviewSubmissionError):
        root._validate_application_journals(contents, request, state, root._sha(request_bytes))


def test_transition_accepts_exact_completed_journal_recovery_and_rejects_cost_change() -> None:
    before_payload = {
        "status": "final_review_prepared",
        "updated_at": "before",
        "actual_final_review_cost_usd": 0.0,
        "actual_total_cost_usd": 0.3,
        "final_review_batch_id": None,
        "final_review_input_file_id": None,
        "final_review_batch_ids": [],
        "final_review_input_file_ids": [],
        "final_review_completed_part_count": 0,
    }
    after_payload = {
        **before_payload,
        "status": "final_review_submitted",
        "updated_at": "after",
        "final_review_batch_id": "batch",
        "final_review_input_file_id": "file",
        "final_review_batch_ids": ["batch"],
        "final_review_input_file_ids": ["file"],
    }
    before = {
        root.STATE_NAME: b"before",
        "immutable": b"value",
        root._FINAL_INTENT_NAME: b"intent",
        root._FINAL_COMPLETED_NAME: b"completed",
    }
    after = {**before, root.STATE_NAME: b"after"}

    root._validate_transition(
        before,
        after,
        root._State(before_payload),
        root._State(after_payload),
    )

    after_payload["actual_final_review_cost_usd"] = 0.01
    with pytest.raises(root.FinalReviewSubmissionError, match="immutable state"):
        root._validate_transition(
            before,
            after,
            root._State(before_payload),
            root._State(after_payload),
        )


def test_phase78_predecessor_accepts_distinct_authorization_and_authenticates_post_image(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    run_id = "speaker-review-0123456789abcdef"
    request = {"archive_sha256": "a" * 64, "run_id": run_id}
    contents = {
        root.STATE_NAME: b"state",
        "candidates.jsonl": b"candidates",
        "source-manifest.json": b"manifest",
        "primary-part-0001-requests.jsonl": b"primary request",
        "primary-part-0001-output.jsonl": b"primary output",
        ".primary-part-0001-submission-intent.json": b"primary intent",
        ".primary-part-0001-submission-completed.json": b"primary completed",
        "adjudication-part-0001-requests.jsonl": b"adjudication request",
        "adjudication-part-0001-output.jsonl": b"adjudication output",
        ".adjudication-part-0001-submission-intent.json": b"adjudication intent",
        ".adjudication-part-0001-submission-completed.json": b"adjudication completed",
        "primary-verdicts.jsonl": b"primary verdicts",
        "primary-parse-errors.json": b"primary errors",
        "primary-decisions.jsonl": b"primary decisions",
        "adjudication-verdicts.jsonl": b"adjudication verdicts",
        "adjudication-parse-errors.json": b"adjudication errors",
        "final-decisions.jsonl": b"final decisions",
        "final-review-part-0001-requests.jsonl": b"final request",
    }
    state = root._State(
        {
            "status": "final_review_prepared",
            "candidate_count": 2,
            "accepted_by_consensus": 1,
            "accepted_by_adjudication": 0,
            "needs_human": 1,
            "actual_primary_cost_usd": 0.1,
            "actual_adjudication_cost_usd": 0.2,
            "primary_part_count": 1,
            "primary_completed_part_count": 1,
            "adjudication_part_count": 1,
            "adjudication_completed_part_count": 1,
            "final_review_part_count": 1,
        }
    )
    digests = root._phase78_digests(contents)
    pre_hashes = {name: {} for name in ("artifacts", "requests", "journals", "outputs", "derived")}
    phase78_request = root.phase78_contract.validate_request(
        {
            "archive_sha256": "a" * 64,
            "authorization_id": "123e4567-e89b-42d3-a456-426614174111",
            "maximum_authorized_cost_microusd": 5_000_000,
            "operation": root.phase78_contract.OPERATION,
            "purpose": root.phase78_contract.PURPOSE,
            "run_id": run_id,
            "schema_version": root.phase78_contract.PROTOCOL_VERSION,
            "season_number": root.phase78_contract.SEASON_NUMBER,
        }
    )
    authorization = root.phase78_contract.canonical_json(phase78_request)
    intent = {
        "archive_sha256": "a" * 64,
        "authorization_id": "123e4567-e89b-42d3-a456-426614174111",
        "authorization_sha256": root._sha(authorization),
        "configuration_sha256": "c" * 64,
        "fourth_observation_intent_sha256": "d" * 64,
        "fourth_observation_receipt_sha256": "e" * 64,
        "image_reference": "image@sha256:" + "f" * 64,
        "maximum_authorized_cost_microusd": 5_000_000,
        "operation": root.phase78_contract.OPERATION,
        "pre_digests": {name: "1" * 64 for name in digests},
        "pre_directory_identities": {"children": {}, "root": [1, 2, 3, 4, 5]},
        "pre_hashes": pre_hashes,
        "pre_state_binding_sha256": "2" * 64,
        "pre_state_sha256": "3" * 64,
        "preparation_receipt_sha256": "4" * 64,
        "purpose": root.phase78_contract.PURPOSE,
        "release_sha": "5" * 40,
        "request_sha256": root._sha(authorization),
        "run_id": run_id,
        "schema_version": root.phase78_contract.PROTOCOL_VERSION,
        "season_number": root.phase78_contract.SEASON_NUMBER,
        "source_manifest_sha256": root._sha(contents["source-manifest.json"]),
        "status": "intent",
    }
    aggregate = {
        "accepted_by_consensus": 1,
        "accepted_by_adjudication": 0,
        "actual_adjudication_cost_microusd": 200_000,
        "actual_primary_cost_microusd": 100_000,
        "adjudication_completed_part_count": 1,
        "adjudication_part_count": 1,
        "candidate_count": 2,
        "final_review_part_count": 1,
        "maximum_authorized_cost_microusd": 5_000_000,
        "needs_human": 1,
        "operation": root.phase78_contract.OPERATION,
        "primary_completed_part_count": 1,
        "primary_part_count": 1,
        "purpose": root.phase78_contract.PURPOSE,
        "run_status": "final_review_prepared",
        "season_number": root.phase78_contract.SEASON_NUMBER,
        "status": "final_review_prepared",
    }
    intent_sha = root._sha(root._canonical(intent))
    groups = root._phase78_groups(contents)
    claim = {
        "archive_sha256": "a" * 64,
        "authorization_id": intent["authorization_id"],
        "authorization_sha256": root._sha(authorization),
        "maximum_authorized_cost_microusd": 5_000_000,
        "operation": root.phase78_contract.OPERATION,
        "request_sha256": root._sha(authorization),
        "run_id": run_id,
        "schema_version": root.phase78_contract.PROTOCOL_VERSION,
        "status": "claimed",
    }
    claim_sha = root._sha(root._canonical(claim))
    receipt = {
        "aggregate": aggregate,
        "archive_sha256": "a" * 64,
        "authorization_claim_sha256": claim_sha,
        "authorization_id": intent["authorization_id"],
        "intent_sha256": intent_sha,
        "operation": root.phase78_contract.OPERATION,
        "post_counts": {name: len(group) for name, group in groups.items()},
        "post_digests": digests,
        "purpose": root.phase78_contract.PURPOSE,
        "run_id": run_id,
        "schema_version": root.phase78_contract.PROTOCOL_VERSION,
        "season_number": root.phase78_contract.SEASON_NUMBER,
        "status": "receipt",
    }

    def record(path: Path) -> tuple[dict[str, object], str]:
        if path.name.startswith("authorization-"):
            return claim, claim_sha
        if path.name.endswith(".intent.json"):
            return intent, intent_sha
        return receipt, "8" * 64

    monkeypatch.setattr(root, "_record", record)
    monkeypatch.setattr(root, "_stable", lambda *args, **kwargs: authorization)

    assert root._validate_phase78_predecessor(
        request,
        tmp_path / run_id,
        state,
        contents,
    ) == (intent_sha, "8" * 64)

    receipt["post_digests"] = {**digests, "state": "9" * 64}
    with pytest.raises(root.FinalReviewSubmissionError, match="predecessor invalid"):
        root._validate_phase78_predecessor(request, tmp_path / run_id, state, contents)

    receipt["post_digests"] = digests
    claim["authorization_sha256"] = "b" * 64
    with pytest.raises(root.FinalReviewSubmissionError, match="predecessor invalid"):
        root._validate_phase78_predecessor(request, tmp_path / run_id, state, contents)
