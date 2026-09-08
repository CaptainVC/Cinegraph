from __future__ import annotations

import base64
from pathlib import Path

import pytest
from scripts import bootstrap_review_host
from scripts import private_speaker_review_observation_host_contract as observation_contract
from scripts import private_speaker_review_submission_host_contract as contract
from scripts.bootstrap_dev_host import BootstrapError


def test_review_identity_is_dedicated_and_cannot_use_corpus_or_deploy_grants() -> None:
    assert contract.REVIEW_USER == "cinegraph-review"
    assert contract.REVIEW_GROUP == "cinegraph-review"
    assert (contract.REVIEW_UID, contract.REVIEW_GID) == (20003, 20003)
    assert contract.REVIEW_COMMAND == "speaker-review-submit-primary-v1"
    assert observation_contract.REVIEW_OBSERVATION_COMMAND == "speaker-review-observe-primary-v1"
    assert contract.REVIEW_USER != contract.CORPUS_USER
    assert "cinegraph-corpus" not in contract.SUDOERS_CONTENT
    assert "cinegraph-deploy" not in contract.SUDOERS_CONTENT
    assert contract.REVIEW_HELPER_PATH.as_posix() in contract.SUDOERS_CONTENT
    assert (
        observation_contract.REVIEW_OBSERVATION_HELPER_PATH.as_posix() in contract.SUDOERS_CONTENT
    )
    assert contract.REVIEW_AUTHORIZATION_ROOT.parent == contract.SPEAKER_REVIEW_ROOT
    assert contract.REVIEW_SUBMISSION_RECEIPTS_ROOT.parent == contract.SPEAKER_REVIEW_ROOT
    assert contract.MINIMUM_PYTHON_VERSION >= (3, 12)


def test_review_authorized_key_is_one_forced_command() -> None:
    blob = b"\x00\x00\x00\x0bssh-ed25519" + b"\x00\x00\x00 " + b"x" * 32
    public_key = "ssh-ed25519 " + base64.b64encode(blob).decode("ascii")
    entry = contract.authorized_key_entry(public_key)
    assert entry == (
        f'restrict,command="{contract.REVIEW_DISPATCH_PATH.as_posix()}" {public_key}\n'
    )
    assert contract.REVIEW_COMMAND not in entry


def test_review_bootstrap_contract_covers_dedicated_paths_and_has_no_broad_sudo() -> None:
    from scripts import bootstrap_review_host

    directories = {item.path for item in bootstrap_review_host.DIRECTORY_CONTRACT}
    files = {item.path for item in bootstrap_review_host.FILE_CONTRACT}
    assert contract.REVIEW_AUTHORIZATION_ROOT in directories
    assert contract.REVIEW_SUBMISSION_RECEIPTS_ROOT in directories
    assert observation_contract.REVIEW_OBSERVATION_RECEIPTS_ROOT in directories
    assert contract.REVIEW_DISPATCH_PATH in files
    assert contract.REVIEW_HELPER_PATH in files
    assert contract.REVIEW_OBSERVATION_HELPER_PATH in files
    assert contract.REVIEW_SUDOERS_PATH in files
    assert contract.REVIEW_AUTHORIZED_KEYS in files
    assert "NOPASSWD: ALL" not in contract.SUDOERS_CONTENT
    assert "bash -c" not in contract.SUDOERS_CONTENT


def test_review_dispatch_and_helper_are_fixed_and_fail_closed() -> None:
    dispatch = Path("deploy/remote/review-dispatch.sh").read_text(encoding="utf-8")
    helper = Path("deploy/remote/submit-private-speaker-review.sh").read_text(encoding="utf-8")
    observation_helper = Path("deploy/remote/observe-private-speaker-review.sh").read_text(
        encoding="utf-8"
    )
    assert "[[ $# -eq 0 ]]" in dispatch
    assert '[[ "$(id -un)" == "cinegraph-review" ]]' in dispatch
    assert "speaker-review-submit-primary-v1)" in dispatch
    assert "speaker-review-observe-primary-v1)" in dispatch
    assert "sudo -n /usr/local/sbin/cinegraph-submit-private-speaker-review" in dispatch
    assert "sudo -n /usr/local/sbin/cinegraph-observe-private-speaker-review" in dispatch
    assert "eval" not in dispatch
    assert "bash -c" not in dispatch
    assert "scp" not in dispatch.lower()
    assert "sftp" not in dispatch.lower()
    assert '[[ "${SUDO_USER-}" == "cinegraph-review" ]]' in helper
    assert 'readonly AUTHORIZATION_ROOT="$SPEAKER_REVIEW_ROOT/authorization"' in helper
    assert 'check_root_path "$AUTHORIZATION_ROOT" directory 700' in helper
    assert (
        helper.index('exec 8>"$TRANSFER_LOCK"')
        < helper.index('exec 9>"$DEPLOYMENT_LOCK"')
        < helper.index('exec 7>"$SPEAKER_REVIEW_LOCK"')
    )
    assert 'python3 -I -S -B "$processor"' in helper
    assert 'timeout --signal=TERM --kill-after="${KILL_AFTER_SECONDS}s"' in helper
    assert "OPENAI_API_KEY" not in helper
    assert "eval" not in helper
    assert "bash -c" not in helper
    assert "--profile corpus-speaker-review-submit-primary" in helper
    assert 'docker rm --force "$CONTAINER_NAME"' in helper
    assert "--profile corpus-speaker-review-observe-primary" in observation_helper
    assert 'docker rm --force "$CONTAINER_NAME"' in observation_helper
    assert "readonly OBSERVATION_RECEIPTS_ROOT=" in observation_helper
    assert 'exec 8>"$TRANSFER_LOCK"' in observation_helper
    assert (
        observation_helper.index('exec 8>"$TRANSFER_LOCK"')
        < observation_helper.index('exec 9>"$DEPLOYMENT_LOCK"')
        < observation_helper.index('exec 7>"$SPEAKER_REVIEW_LOCK"')
    )
    assert "OPENAI_API_KEY" not in observation_helper
    assert "eval" not in observation_helper
    assert "bash -c" not in observation_helper
    for trusted_path in (
        "scripts/run_private_speaker_review_submission.py",
        "scripts/run_private_speaker_review.py",
        "scripts/private_speaker_review_contract.py",
        "scripts/private_corpus_host_contract.py",
        "scripts/receive_private_corpus.py",
        "scripts/run_private_corpus_processing.py",
        "scripts/submit_private_speaker_review_workspace.py",
        "scripts/private_speaker_review_submission_contract.py",
        "scripts/private_speaker_review_submission_host_contract.py",
        "deploy/compose.yaml",
    ):
        assert trusted_path in helper
    for trusted_path in (
        "scripts/run_private_speaker_review_observation.py",
        "scripts/observe_private_speaker_review_workspace.py",
        "scripts/private_speaker_review_observation_contract.py",
        "scripts/private_speaker_review_observation_host_contract.py",
        "scripts/run_private_speaker_review_submission.py",
        "scripts/private_speaker_review_submission_contract.py",
        "deploy/compose.yaml",
    ):
        assert trusted_path in observation_helper
    assert "com.docker.compose.service" in observation_helper
    assert "com.docker.compose.oneoff" in observation_helper
    assert "com.docker.compose.project.config_files" in observation_helper
    assert "timeout --signal=TERM" in observation_helper


def test_compose_primary_submission_is_egress_only_and_secret_file_based() -> None:
    compose = Path("deploy/compose.yaml").read_text(encoding="utf-8")
    service = compose.split("  corpus-speaker-review-submit-primary:", 1)[1].split(
        "\n  postgres:", 1
    )[0]
    assert "profiles: [corpus-speaker-review-submit-primary]" in service
    assert 'user: "10002:10002"' in service
    assert "      - egress" in service
    assert "      - backend" not in service
    assert "network_mode:" not in service
    assert "    volumes:" not in service
    assert "OPENAI_API_KEY:" not in service
    assert "    secrets:" in service
    assert "source: openai_api_key" in service
    assert "read_only: true" in service
    assert "no-new-privileges:true" in service
    assert "      - ALL" in service
    assert 'restart: "no"' in service
    assert 'command: ["python", "scripts/submit_private_speaker_review_workspace.py"]' in service
    assert "CINEGRAPH_DATABASE_URL" not in service
    assert "CINEGRAPH_QDRANT_URL" not in service
    assert "CINEGRAPH_IDENTITY_DATABASE_PATH" not in service
    assert "CINEGRAPH_SPEAKER_REVIEW_RUNS_ROOT: /review-workspace/review-runs" in service
    assert "secrets:\n  openai_api_key:\n    environment: OPENAI_API_KEY" in compose


def test_review_bootstrap_rejects_reuse_of_corpus_or_deploy_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    key = "review-key"
    monkeypatch.setattr(bootstrap_review_host, "_validate_platform_and_tools", lambda: None)
    monkeypatch.setattr(
        bootstrap_review_host.bootstrap_dev_host, "_verify_bootstrap_checkout", lambda: "a" * 40
    )
    monkeypatch.setattr(bootstrap_review_host, "_public_key", lambda _: key)
    monkeypatch.setattr(bootstrap_review_host.bootstrap_corpus_host, "_public_key", lambda _: key)
    monkeypatch.setattr(bootstrap_review_host, "_installed_deploy_public_key", lambda: "deploy-key")
    monkeypatch.setattr(
        bootstrap_review_host, "_fingerprint_line", lambda value: f"SHA256:{value[0] * 43}"
    )
    with pytest.raises(BootstrapError, match="identities"):
        bootstrap_review_host.bootstrap(
            public_key_file=tmp_path / "review.pub",
            expected_key_fingerprint="SHA256:" + "r" * 43,
            corpus_public_key_file=tmp_path / "corpus.pub",
            expected_corpus_key_fingerprint="SHA256:" + "r" * 43,
            expected_deploy_key_fingerprint="SHA256:" + "d" * 43,
            check=True,
        )


def test_review_refresh_replaces_only_reviewed_code_after_preflight(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    managed = {
        bootstrap_review_host.REVIEW_DISPATCH_PATH: b"new-dispatch",
        bootstrap_review_host.REVIEW_HELPER_PATH: b"new-helper",
        bootstrap_review_host.REVIEW_OBSERVATION_HELPER_PATH: b"new-observation-helper",
        bootstrap_review_host.REVIEW_SUDOERS_PATH: b"sudoers",
        bootstrap_review_host.REVIEW_AUTHORIZED_KEYS: b"authorized",
    }
    installed = {
        bootstrap_review_host.REVIEW_DISPATCH_PATH: b"old-dispatch",
        bootstrap_review_host.REVIEW_HELPER_PATH: b"new-helper",
        bootstrap_review_host.REVIEW_SUDOERS_PATH: b"legacy-sudoers",
        bootstrap_review_host.REVIEW_AUTHORIZED_KEYS: b"authorized",
    }
    events: list[tuple[str, Path, bool | None]] = []
    verified: list[Path] = []
    monkeypatch.setattr(
        bootstrap_review_host,
        "_preflight_refresh_host_files",
        lambda _: (managed, installed),
    )
    monkeypatch.setattr(
        bootstrap_review_host.bootstrap_dev_host,
        "_replace_exact_file",
        lambda expected, _content: events.append(("replace", expected.path, None)),
    )
    monkeypatch.setattr(
        bootstrap_review_host.bootstrap_dev_host,
        "_ensure_exact_file",
        lambda expected, _content, apply: (
            events.append(("ensure", expected.path, apply))
            if apply
            else verified.append(expected.path)
        ),
    )
    monkeypatch.setattr(
        bootstrap_review_host.bootstrap_dev_host,
        "_require_success",
        lambda _arguments: None,
    )

    bootstrap_review_host._ensure_host_files("review-key", apply=True, refresh_review_code=True)

    assert events == [
        ("ensure", bootstrap_review_host.REVIEW_OBSERVATION_HELPER_PATH, True),
        ("replace", bootstrap_review_host.REVIEW_SUDOERS_PATH, None),
        ("replace", bootstrap_review_host.REVIEW_DISPATCH_PATH, None),
    ]
    assert verified == list(managed)
