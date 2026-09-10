from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest
from scripts import private_speaker_review_next_primary_host_contract as host
from scripts import private_speaker_review_next_primary_submission_contract as contract
from scripts import run_private_speaker_review_next_primary as coordinator


def test_host_contract_is_separate_and_finite() -> None:
    assert host.REVIEW_NEXT_PRIMARY_COMMAND == contract.COMMAND
    assert host.REVIEW_NEXT_PRIMARY_HELPER_PATH.as_posix().endswith(
        "cinegraph-submit-next-private-speaker-review"
    )
    assert host.REVIEW_NEXT_PRIMARY_RECEIPTS_ROOT.name == "next-primary-receipts"
    assert host.REVIEW_NEXT_PRIMARY_HELPER_PATH.as_posix() in host.SUDOERS_CONTENT
    assert "NOPASSWD: ALL" not in host.SUDOERS_CONTENT


def test_dispatcher_and_helper_are_exact_and_fail_closed() -> None:
    dispatcher = Path("deploy/remote/review-dispatch.sh").read_text(encoding="utf-8")
    helper = Path("deploy/remote/submit-next-private-speaker-review.sh").read_text(encoding="utf-8")
    assert "speaker-review-submit-next-primary-v1)" in dispatcher
    assert "sudo -n /usr/local/sbin/cinegraph-submit-next-private-speaker-review" in dispatcher
    assert "python3 -I -S -B" in helper
    assert "flock -n 8" in helper and "flock -w 10 9" in helper and "flock -n 7" in helper
    assert "${OPENAI_API_KEY" not in helper
    assert "--env OPENAI_API_KEY" not in helper
    assert "eval" not in helper and "bash -c" not in helper
    assert "origin/main" in helper
    assert '[[ "${lines[6]}" == "cinegraph-dev" ]]' in helper
    assert '[[ "${lines[7]}" == "10002:10002" ]]' in helper
    assert '[[ "${lines[16]}" == "cinegraph-dev_egress," ]]' in helper
    assert "OPENAI_API_KEY=" in helper
    assert (
        "^/opt/cinegraph/shared/private-corpus/dev/review-runs/sha256-[0-9a-f]{64}/review-runs$"
    ) in helper
    assert "$release_dir/shared/private-corpus/dev/review-runs" not in helper


def test_worker_arguments_bind_exact_request_hash() -> None:
    request = {
        "archive_sha256": "a" * 64,
        "authorization_id": "00000000-0000-4000-8000-000000000000",
        "maximum_authorized_cost_microusd": 5_000_000,
        "operation": contract.OPERATION,
        "purpose": contract.PURPOSE,
        "run_id": "speaker-review-0123456789abcdef",
        "schema_version": 1,
        "season_number": 2,
        "_expected_request_sha256": "b" * 64,
        "_expected_pre_artifact_set_sha256": "c" * 64,
        "_expected_pre_journal_set_sha256": "d" * 64,
        "_expected_pre_run_state_sha256": "e" * 64,
    }
    arguments = coordinator._worker_args(request, Path("/private/review-runs"))
    assert f"{contract.ENV_EXPECTED_REQUEST_SHA256}=b{'b' * 63}" in arguments
    assert f"{contract.ENV_EXPECTED_PRE_ARTIFACT_SET_SHA256}=c{'c' * 63}" in arguments
    assert f"{contract.ENV_EXPECTED_PRE_JOURNAL_SET_SHA256}=d{'d' * 63}" in arguments
    assert f"{contract.ENV_EXPECTED_PRE_RUN_STATE_SHA256}=e{'e' * 63}" in arguments
    assert arguments[-1] == host.REVIEW_NEXT_PRIMARY_COMPOSE_SERVICE


def test_write_once_is_idempotent_but_rejects_tampered_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    receipts = tmp_path / "next-primary-receipts"
    receipts.mkdir()
    receipts.chmod(0o700)
    monkeypatch.setattr(coordinator, "NEXT_RECEIPTS_ROOT", receipts)
    monkeypatch.setattr(coordinator, "ROOT_UID", getattr(os, "getuid", lambda: 0)())
    monkeypatch.setattr(coordinator, "ROOT_GID", getattr(os, "getgid", lambda: 0)())
    target = receipts / "run.json"
    payload = {"status": "intent", "request_sha256": hashlib.sha256(b"x").hexdigest()}
    coordinator._write_once(target, payload)
    coordinator._write_once(target, payload)
    target.write_bytes(json.dumps({"status": "intent"}, separators=(",", ":")).encode() + b"\n")
    with pytest.raises(coordinator.NextPrimaryProcessingError):
        coordinator._write_once(target, payload)
