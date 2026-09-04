from __future__ import annotations

import base64
import subprocess
from pathlib import Path

import pytest
from scripts import bootstrap_corpus_host
from scripts import private_corpus_host_contract as contract
from scripts.bootstrap_dev_host import BootstrapError


def _public_key(marker: bytes = b"x") -> str:
    blob = b"\x00\x00\x00\x0bssh-ed25519" + b"\x00\x00\x00 " + marker * 32
    return "ssh-ed25519 " + base64.b64encode(blob).decode("ascii")


def test_corpus_host_contract_is_distinct_and_root_private() -> None:
    assert contract.CORPUS_USER == "cinegraph-corpus"
    assert contract.CORPUS_UID == 20002
    assert contract.CORPUS_GID == 20002
    assert contract.MINIMUM_PYTHON_VERSION == (3, 12)
    assert contract.CORPUS_USER != "cinegraph-deploy"
    assert "cinegraph-corpus-dispatch" in str(contract.CORPUS_DISPATCH_PATH)
    assert "cinegraph-deploy-dispatch" not in str(contract.CORPUS_DISPATCH_PATH)
    assert contract.RECEIVE_COMMAND == "receive-v1"
    assert contract.TRANSACTIONS_ROOT.parent == contract.DEV_PRIVATE_CORPUS_ROOT
    assert contract.OBJECTS_ROOT.parent == contract.DEV_PRIVATE_CORPUS_ROOT
    assert contract.QUARANTINE_ROOT.parent == contract.DEV_PRIVATE_CORPUS_ROOT
    assert "cinegraph-deploy-dev" not in contract.CORPUS_SUDOERS_CONTENT
    assert contract.CORPUS_HELPER_PATH.as_posix() in contract.CORPUS_SUDOERS_CONTENT
    assert contract.PROCESS_HELPER_PATH.as_posix() in contract.CORPUS_SUDOERS_CONTENT
    assert contract.PROCESS_COMMAND == "process-v1"
    assert contract.PROCESSING_ROOT.parent == contract.DEV_PRIVATE_CORPUS_ROOT
    assert contract.PROCESSING_RECEIPTS_ROOT.parent == contract.PROCESSING_ROOT
    assert contract.PROCESSING_LOCK.parent == contract.DEV_PRIVATE_CORPUS_ROOT
    assert contract.PROCESSOR_REQUIRED_COMMANDS == ("docker",)
    assert '""' in contract.CORPUS_SUDOERS_CONTENT


def test_corpus_authorization_forces_only_static_dispatcher() -> None:
    entry = contract.corpus_authorized_key_entry(_public_key())
    assert entry.startswith(
        f'restrict,command="{contract.CORPUS_DISPATCH_PATH.as_posix()}" ssh-ed25519 '
    )
    assert "receive-v1" not in entry
    assert "cinegraph-deploy" not in entry
    assert entry.endswith("\n")


def test_public_key_fingerprint_is_stable_and_distinguishes_keys() -> None:
    first = bootstrap_corpus_host._fingerprint_line(_public_key(b"x"))
    second = bootstrap_corpus_host._fingerprint_line(_public_key(b"y"))
    assert first.startswith("SHA256:")
    assert len(first) == 50
    assert first != second


def _mock_bootstrap_boundary(
    monkeypatch: pytest.MonkeyPatch, *, corpus_key: str, deploy_key: str
) -> list[tuple[str, object]]:
    calls: list[tuple[str, object]] = []
    monkeypatch.setattr(bootstrap_corpus_host, "_validate_platform_and_tools", lambda: None)
    monkeypatch.setattr(
        bootstrap_corpus_host.bootstrap_dev_host,
        "_verify_bootstrap_checkout",
        lambda: "a" * 40,
    )
    monkeypatch.setattr(bootstrap_corpus_host, "_public_key", lambda _: corpus_key)
    monkeypatch.setattr(bootstrap_corpus_host, "_installed_deploy_public_key", lambda: deploy_key)
    fingerprints = {
        corpus_key: "SHA256:" + "A" * 43,
        deploy_key: "SHA256:" + "B" * 43,
    }
    monkeypatch.setattr(bootstrap_corpus_host, "_fingerprint_line", lambda key: fingerprints[key])
    monkeypatch.setattr(bootstrap_corpus_host, "_account_exists", lambda: True)
    monkeypatch.setattr(
        bootstrap_corpus_host,
        "_verify_account",
        lambda: calls.append(("account", True)),
    )
    monkeypatch.setattr(bootstrap_corpus_host, "DIRECTORY_CONTRACT", ())
    monkeypatch.setattr(
        bootstrap_corpus_host,
        "_ensure_host_files",
        lambda key, **options: calls.append((key, options)),
    )
    return calls


def test_bootstrap_requires_distinct_corpus_and_deployment_fingerprints(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    key = _public_key()
    _mock_bootstrap_boundary(monkeypatch, corpus_key=key, deploy_key=key)
    monkeypatch.setattr(
        bootstrap_corpus_host,
        "_fingerprint_line",
        lambda _: "SHA256:" + "A" * 43,
    )
    with pytest.raises(BootstrapError, match="identities"):
        bootstrap_corpus_host.bootstrap(
            public_key_file=tmp_path / "public",
            expected_key_fingerprint="SHA256:" + "A" * 43,
            expected_deploy_key_fingerprint="SHA256:" + "A" * 43,
            check=True,
        )


def test_bootstrap_check_is_separate_and_nonmutating(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    corpus_key = _public_key(b"x")
    deploy_key = _public_key(b"y")
    calls = _mock_bootstrap_boundary(monkeypatch, corpus_key=corpus_key, deploy_key=deploy_key)
    result = bootstrap_corpus_host.bootstrap(
        public_key_file=tmp_path / "public",
        expected_key_fingerprint="SHA256:" + "A" * 43,
        expected_deploy_key_fingerprint="SHA256:" + "B" * 43,
        check=True,
    )
    assert result == {
        "bootstrap_sha": "a" * 40,
        "mode": "check",
        "status": "corpus-transfer-ready",
    }
    assert calls == [
        ("account", True),
        (corpus_key, {"apply": False, "refresh_corpus_code": False}),
    ]


def test_bootstrap_refresh_and_check_are_mutually_exclusive(
    tmp_path: Path,
) -> None:
    with pytest.raises(BootstrapError, match="combined"):
        bootstrap_corpus_host.bootstrap(
            public_key_file=tmp_path / "public",
            expected_key_fingerprint="SHA256:" + "A" * 43,
            expected_deploy_key_fingerprint="SHA256:" + "B" * 43,
            check=True,
            refresh_corpus_code=True,
        )


def test_bootstrap_contract_manages_only_corpus_paths() -> None:
    paths = {item.path for item in bootstrap_corpus_host.FILE_CONTRACT}
    assert contract.CORPUS_DISPATCH_PATH in paths
    assert contract.CORPUS_HELPER_PATH in paths
    assert contract.PROCESS_HELPER_PATH in paths
    assert contract.CORPUS_SUDOERS_PATH in paths
    assert contract.CORPUS_AUTHORIZED_KEYS in paths
    assert Path("/usr/local/libexec/cinegraph-deploy-dispatch") not in paths
    assert Path("/usr/local/sbin/cinegraph-deploy-dev") not in paths
    assert Path("/etc/cinegraph/dev.env") not in paths


def test_bootstrap_cli_has_no_private_key_or_remote_mutation_options() -> None:
    result = subprocess.run(
        ["uv", "run", "python", "scripts/bootstrap_corpus_host.py", "--help"],
        capture_output=True,
        check=False,
        text=True,
    )
    assert result.returncode == 0
    assert "--public-key-file" in result.stdout
    assert "--expected-deploy-key-fingerprint" in result.stdout
    assert "--refresh-corpus-code" in result.stdout
    assert "--private-key" not in result.stdout
    assert "--host" not in result.stdout


def test_existing_deployment_boundary_was_not_given_a_corpus_command() -> None:
    dispatcher = Path("deploy/remote/deploy-dispatch.sh").read_text(encoding="utf-8")
    bootstrap = Path("scripts/bootstrap_dev_host.py").read_text(encoding="utf-8")
    assert "receive-v1" not in dispatcher
    assert "cinegraph-corpus" not in dispatcher
    assert "receive-v1" not in bootstrap
    assert "cinegraph-corpus" not in bootstrap


def test_refresh_upgrades_transfer_only_boundary_in_fail_closed_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dispatch = tmp_path / "dispatch"
    receive_helper = tmp_path / "receive"
    process_helper = tmp_path / "process"
    sudoers = tmp_path / "sudoers"
    authorized_keys = tmp_path / "authorized_keys"
    managed = {
        dispatch: b"new-dispatch",
        receive_helper: b"new-receive",
        process_helper: b"new-process",
        sudoers: contract.CORPUS_SUDOERS_CONTENT.encode("utf-8"),
        authorized_keys: b"reviewed-key",
    }
    for path, content in (
        (dispatch, b"old-dispatch"),
        (receive_helper, b"old-receive"),
        (sudoers, contract.LEGACY_TRANSFER_ONLY_SUDOERS_CONTENT.encode("utf-8")),
        (authorized_keys, b"reviewed-key"),
    ):
        path.write_bytes(content)

    monkeypatch.setattr(bootstrap_corpus_host, "CORPUS_DISPATCH_PATH", dispatch)
    monkeypatch.setattr(bootstrap_corpus_host, "CORPUS_HELPER_PATH", receive_helper)
    monkeypatch.setattr(bootstrap_corpus_host, "PROCESS_HELPER_PATH", process_helper)
    monkeypatch.setattr(bootstrap_corpus_host, "CORPUS_SUDOERS_PATH", sudoers)
    monkeypatch.setattr(bootstrap_corpus_host, "CORPUS_AUTHORIZED_KEYS", authorized_keys)
    monkeypatch.setattr(bootstrap_corpus_host, "_managed_content", lambda _key: managed)
    monkeypatch.setattr(
        bootstrap_corpus_host,
        "FILE_CONTRACT",
        tuple(
            bootstrap_corpus_host.ExpectedPath(path, "file", 0, 0, 0o755)
            for path in managed
        ),
    )
    monkeypatch.setattr(bootstrap_corpus_host.bootstrap_dev_host, "_verify_path", lambda _: None)
    monkeypatch.setattr(bootstrap_corpus_host, "_validate_sudoers_candidate", lambda _: None)
    monkeypatch.setattr(
        bootstrap_corpus_host.bootstrap_dev_host,
        "_require_success",
        lambda _command: None,
    )
    mutations: list[tuple[str, Path]] = []

    def replace(expected: bootstrap_corpus_host.ExpectedPath, content: bytes) -> None:
        mutations.append(("replace", expected.path))
        expected.path.write_bytes(content)

    def ensure(
        expected: bootstrap_corpus_host.ExpectedPath, content: bytes, *, apply: bool
    ) -> None:
        if apply:
            mutations.append(("install", expected.path))
            expected.path.write_bytes(content)
        assert expected.path.read_bytes() == content

    monkeypatch.setattr(bootstrap_corpus_host.bootstrap_dev_host, "_replace_exact_file", replace)
    monkeypatch.setattr(bootstrap_corpus_host.bootstrap_dev_host, "_ensure_exact_file", ensure)

    bootstrap_corpus_host._ensure_host_files(
        "unused", apply=True, refresh_corpus_code=True
    )

    assert mutations == [
        ("replace", receive_helper),
        ("install", process_helper),
        ("replace", sudoers),
        ("replace", dispatch),
    ]


def test_refresh_preflights_existing_boundary_before_creating_processing_directories(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    corpus_key = _public_key(b"x")
    deploy_key = _public_key(b"y")
    _mock_bootstrap_boundary(monkeypatch, corpus_key=corpus_key, deploy_key=deploy_key)
    processing = tmp_path / "processing"
    monkeypatch.setattr(
        bootstrap_corpus_host,
        "DIRECTORY_CONTRACT",
        (bootstrap_corpus_host.ExpectedPath(processing, "directory", 0, 0, 0o700),),
    )
    monkeypatch.setattr(
        bootstrap_corpus_host,
        "_preflight_refresh_host_files",
        lambda _key: (_ for _ in ()).throw(BootstrapError("preflight rejected")),
    )

    with pytest.raises(BootstrapError, match="preflight rejected"):
        bootstrap_corpus_host.bootstrap(
            public_key_file=tmp_path / "public",
            expected_key_fingerprint="SHA256:" + "A" * 43,
            expected_deploy_key_fingerprint="SHA256:" + "B" * 43,
            check=False,
            refresh_corpus_code=True,
        )

    assert not processing.exists()
