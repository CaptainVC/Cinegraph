from dataclasses import replace
from pathlib import Path

import pytest
from scripts import private_speaker_review_fourth_adjudication_host_contract as host
from scripts import private_speaker_review_fourth_adjudication_submission_client as client
from scripts import private_speaker_review_fourth_adjudication_submission_contract as contract
from scripts import run_private_speaker_review_fourth_adjudication as root
from scripts import submit_fourth_private_speaker_review_adjudication_workspace as worker

from cinegraph.domain.enums.enum import SpeakerReviewRunStatus
from cinegraph.ingestion.speaker_review.workflow import SpeakerReviewRunState


def _state(*, count: int = 3) -> SpeakerReviewRunState:
    return SpeakerReviewRunState(
        schema_version=1,
        run_id="speaker-review-" + "a" * 16,
        status=SpeakerReviewRunStatus.ADJUDICATION_PART_COMPLETED,
        created_at="2026-01-01T00:00:00Z",
        updated_at="2026-01-01T00:00:01Z",
        candidate_count=2,
        primary_model="gpt",
        adjudication_model="gpt",
        prompt_version="v1",
        maximum_cost_usd=5.0,
        estimated_primary_cost_usd=1.0,
        actual_primary_cost_usd=1.0,
        actual_adjudication_cost_usd=0.0,
        adjudication_part_count=4,
        adjudication_completed_part_count=count,
        adjudication_batch_ids=tuple(f"batch-{i}" for i in range(count)),
        adjudication_input_file_ids=tuple(f"file-{i}" for i in range(count)),
        adjudication_batch_id=f"batch-{count - 1}",
        adjudication_input_file_id=f"file-{count - 1}",
    )


def test_contract_and_host_are_distinct_and_bound() -> None:
    assert contract.COMMAND == "speaker-review-submit-fourth-adjudication-v1"
    assert contract.OPERATION == "submit_fourth_adjudication"
    assert host.REVIEW_FOURTH_ADJUDICATION_COMMAND == contract.COMMAND
    assert host.REVIEW_FOURTH_ADJUDICATION_RECEIPTS_ROOT.name == "fourth-adjudication-submission-receipts"
    assert host.SUDOERS_CONTENT.count("NOPASSWD:") == 12


def test_client_uses_fourth_command_and_dispatcher_is_fail_closed(tmp_path: Path) -> None:
    args = client.ssh_arguments(
        ssh="ssh",
        identity=tmp_path / "identity",
        known_hosts=tmp_path / "known_hosts",
        host="review.example",
    )
    assert args[-1] == contract.COMMAND
    dispatcher = Path("deploy/remote/review-dispatch.sh").read_text(encoding="utf-8")
    assert "speaker-review-submit-fourth-adjudication-v1)" in dispatcher
    assert "sudo -n /usr/local/sbin/cinegraph-submit-fourth-private-speaker-review-adjudication" in dispatcher


def test_root_accepts_only_phase74_completed_count() -> None:
    root._validate_state_shape(_state())
    with pytest.raises(root.FourthAdjudicationSubmissionError):
        root._validate_state_shape(_state(count=2))


def test_compose_worker_has_only_egress_and_secret_mount() -> None:
    service = Path("deploy/compose.yaml").read_text(encoding="utf-8").split(
        "\n  corpus-speaker-review-submit-fourth-adjudication:", 1
    )[1].split("\n  corpus-speaker-review-observe-next-adjudication:", 1)[0]
    assert "profiles: [corpus-speaker-review-submit-fourth-adjudication]" in service
    assert "      - egress" in service and "      - backend" not in service
    assert "source: openai_api_key" in service and "mode: 0400" in service
    assert 'command: ["python", "scripts/submit_fourth_private_speaker_review_adjudication_workspace.py"]' in service


@pytest.mark.parametrize(
    "journal_name",
    [
        ".adjudication-part-0004-submission-intent.json",
        ".adjudication-part-0004-submission-completed.json",
    ],
)
def test_worker_completed_checkpoint_rejects_part_four_journal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    journal_name: str,
) -> None:
    state = replace(_state(), adjudication_part_count=3)
    environment = {
        contract.ENV_ARCHIVE_SHA256: "a" * 64,
        contract.ENV_AUTHORIZATION_ID: "123e4567-e89b-42d3-a456-426614174000",
        contract.ENV_MAXIMUM_AUTHORIZED_COST_MICROUSD: "5000000",
        contract.ENV_RUN_ID: state.run_id,
    }
    monkeypatch.setattr(worker, "_run_directory", lambda *_: tmp_path)
    monkeypatch.setattr(worker, "_inventory", lambda *_: ({journal_name: b"{}\n"}, state))
    monkeypatch.setattr(worker, "_digest_bindings", lambda *_: None)
    monkeypatch.setattr(worker, "_validate_checkpoint_shape", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(worker, "_validate_completed_parts", lambda *_: None)
    monkeypatch.setattr(
        worker,
        "read_stable_openai_secret",
        lambda *_: pytest.fail("secret must not be read"),
    )

    with pytest.raises(
        worker.FourthAdjudicationSubmissionWorkerError,
        match="ambiguous",
    ):
        worker.submit_fourth_adjudication(environment=environment, review_root=tmp_path)
