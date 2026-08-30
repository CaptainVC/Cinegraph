from __future__ import annotations

import base64
import stat
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from scripts import bootstrap_dev_host
from scripts.bootstrap_dev_host import BootstrapError, ExpectedPath
from scripts.dev_host_contract import (
    DEPLOY_GID,
    DEPLOY_PASSWORD_FIELD,
    DEPLOY_UID,
    DISPATCH_PATH,
    HELPER_PATH,
    REPOSITORY_URL,
    SUDOERS_CONTENT,
    authorized_key_entry,
    known_hosts_line,
    validate_host,
    validate_public_key_line,
)


def _public_key(comment: str = "cinegraph-dev") -> str:
    blob = base64.b64encode(b"\x00\x00\x00\x0bssh-ed25519" + b"\x00" * 32).decode("ascii")
    return f"ssh-ed25519 {blob} {comment}"


def test_public_key_and_forced_entry_are_canonical() -> None:
    public_key = _public_key()

    assert validate_public_key_line(public_key) == public_key
    assert authorized_key_entry(public_key) == (
        f'restrict,command="{DISPATCH_PATH.as_posix()}" {public_key}\n'
    )


@pytest.mark.parametrize(
    "value",
    [
        "ssh-rsa AAAA rejected",
        " ssh-ed25519 AAAA",
        "ssh-ed25519 !!!!",
        "ssh-ed25519 AAAA\nssh-ed25519 BBBB",
    ],
)
def test_public_key_contract_rejects_noncanonical_input(value: str) -> None:
    with pytest.raises(ValueError):
        validate_public_key_line(value)


@pytest.mark.parametrize("host", ["dev.example.com", "203.0.113.7"])
def test_host_and_known_hosts_evidence_are_port_22_canonical(host: str) -> None:
    public_key = _public_key("host")

    assert validate_host(host) == host
    assert known_hosts_line(host, public_key).startswith(f"{host} ssh-ed25519 ")


@pytest.mark.parametrize("host", ["host:22", "[host]:22", "-host", "host..example", "host name"])
def test_host_contract_rejects_noncanonical_or_non_port_22_forms(host: str) -> None:
    with pytest.raises(ValueError):
        validate_host(host)


def test_read_single_public_key_rejects_multiple_lines_and_symlink(tmp_path: Path) -> None:
    key_file = tmp_path / "deploy.pub"
    key_file.write_text(f"{_public_key()}\n{_public_key('second')}\n", encoding="utf-8")
    with pytest.raises(BootstrapError, match="exactly one"):
        bootstrap_dev_host.read_single_public_key(key_file)

    target = tmp_path / "target.pub"
    target.write_text(_public_key() + "\n", encoding="utf-8")
    link = tmp_path / "link.pub"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("symlink creation is unavailable")
    with pytest.raises(BootstrapError, match="non-symlink"):
        bootstrap_dev_host.read_single_public_key(link)


def test_existing_different_managed_file_fails_without_overwrite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "helper"
    path.write_bytes(b"unexpected")
    expected = ExpectedPath(path, "file", 0, 0, 0o755)
    monkeypatch.setattr(bootstrap_dev_host, "_verify_path", lambda _: None)

    with pytest.raises(BootstrapError, match="differs"):
        bootstrap_dev_host._ensure_exact_file(expected, b"reviewed", apply=True)

    assert path.read_bytes() == b"unexpected"


def test_check_mode_never_creates_missing_managed_file(tmp_path: Path) -> None:
    expected = ExpectedPath(tmp_path / "missing", "file", 0, 0, 0o600)

    with pytest.raises(BootstrapError, match="missing"):
        bootstrap_dev_host._ensure_exact_file(expected, b"reviewed", apply=False)
    assert not expected.path.exists()


def test_required_path_rejects_wrong_mode(tmp_path: Path) -> None:
    path = tmp_path / "managed"
    path.mkdir()
    path.chmod(0o755)
    metadata = path.stat()

    with pytest.raises(BootstrapError, match="mode"):
        bootstrap_dev_host._verify_path(
            ExpectedPath(path, "directory", metadata.st_uid, metadata.st_gid, 0o700)
        )


def test_bootstrap_checkout_requires_clean_exact_live_main(monkeypatch: pytest.MonkeyPatch) -> None:
    head = "a" * 40
    monkeypatch.setattr(bootstrap_dev_host, "_verify_root_controlled_checkout_tree", lambda: None)

    def command_result(command: list[str]) -> SimpleNamespace:
        rendered = " ".join(command)
        if rendered.endswith(" remote"):
            stdout = "origin\n"
        elif "config --local --get remote.origin.url" in rendered:
            stdout = REPOSITORY_URL + "\n"
        elif "status --porcelain=v1 --untracked-files=all" in rendered:
            stdout = ""
        elif "rev-parse --verify HEAD" in rendered:
            stdout = head + "\n"
        elif "ls-remote --exit-code" in rendered:
            stdout = f"{head}\trefs/heads/main\n"
        else:  # pragma: no cover - assertion guard for this command contract
            raise AssertionError(command)
        return SimpleNamespace(stdout=stdout, returncode=0)

    monkeypatch.setattr(bootstrap_dev_host, "_require_success", command_result)

    assert bootstrap_dev_host._verify_bootstrap_checkout() == head


def test_account_creation_uses_nonlocking_invalid_password_hash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commands: list[list[str]] = []

    def missing_group(_name: str) -> None:
        raise KeyError

    monkeypatch.setitem(sys.modules, "grp", SimpleNamespace(getgrnam=missing_group))
    monkeypatch.setattr(
        bootstrap_dev_host,
        "_require_success",
        lambda command: commands.append(command) or SimpleNamespace(stdout="", returncode=0),
    )

    bootstrap_dev_host._create_account()

    rendered = [" ".join(command) for command in commands]
    assert any(f"groupadd --gid {DEPLOY_GID} cinegraph-deploy" in item for item in rendered)
    useradd = next(item for item in rendered if item.startswith("useradd "))
    assert f"--uid {DEPLOY_UID}" in useradd
    assert f"--password {DEPLOY_PASSWORD_FIELD}" in useradd
    assert "--system" not in useradd
    assert "--no-create-home" in useradd


def test_ssh_authorization_contract_is_root_managed() -> None:
    directories = {item.path: item for item in bootstrap_dev_host.DIRECTORY_CONTRACT}
    files = {item.path: item for item in bootstrap_dev_host.FILE_CONTRACT}

    assert directories[bootstrap_dev_host.DEPLOY_HOME].uid == 0
    assert directories[bootstrap_dev_host.DEPLOY_HOME / ".ssh"].uid == 0
    sudoers_directory = directories[Path("/etc/sudoers.d")]
    assert (sudoers_directory.uid, sudoers_directory.gid, sudoers_directory.mode) == (0, 0, 0o750)
    assert sudoers_directory.accepted_modes == frozenset({0o750, 0o755})
    authorized_keys = files[bootstrap_dev_host.AUTHORIZED_KEYS]
    assert (authorized_keys.uid, authorized_keys.gid, authorized_keys.mode) == (0, 0, 0o644)


@pytest.mark.parametrize("mode", [0o750, 0o755])
def test_sudoers_directory_accepts_supported_root_owned_modes(
    monkeypatch: pytest.MonkeyPatch, mode: int
) -> None:
    expected = next(
        item for item in bootstrap_dev_host.DIRECTORY_CONTRACT if item.path == Path("/etc/sudoers.d")
    )
    metadata = SimpleNamespace(st_mode=stat.S_IFDIR | mode, st_uid=0, st_gid=0)
    monkeypatch.setattr(bootstrap_dev_host, "_path_metadata", lambda _: metadata)

    bootstrap_dev_host._verify_path(expected)


@pytest.mark.parametrize("mode", [0o770, 0o777])
def test_sudoers_directory_rejects_writable_modes(
    monkeypatch: pytest.MonkeyPatch, mode: int
) -> None:
    expected = next(
        item for item in bootstrap_dev_host.DIRECTORY_CONTRACT if item.path == Path("/etc/sudoers.d")
    )
    metadata = SimpleNamespace(st_mode=stat.S_IFDIR | mode, st_uid=0, st_gid=0)
    monkeypatch.setattr(bootstrap_dev_host, "_path_metadata", lambda _: metadata)

    with pytest.raises(BootstrapError, match="mode"):
        bootstrap_dev_host._verify_path(expected)


def test_check_mode_does_not_create_missing_account(monkeypatch: pytest.MonkeyPatch) -> None:
    created: list[bool] = []
    monkeypatch.setattr(bootstrap_dev_host, "_validate_platform_and_tools", lambda: None)
    monkeypatch.setattr(bootstrap_dev_host, "_verify_bootstrap_checkout", lambda: "a" * 40)
    monkeypatch.setattr(bootstrap_dev_host, "read_single_public_key", lambda _: _public_key())
    monkeypatch.setattr(bootstrap_dev_host, "fingerprint", lambda _: "SHA256:" + "A" * 43)
    monkeypatch.setattr(bootstrap_dev_host, "_account_exists", lambda: False)
    monkeypatch.setattr(bootstrap_dev_host, "_create_account", lambda: created.append(True))

    with pytest.raises(BootstrapError, match="account is missing"):
        bootstrap_dev_host.bootstrap(
            public_key_file=Path("unused"),
            expected_fingerprint="SHA256:" + "A" * 43,
            host="dev.example.com",
            check=True,
        )
    assert created == []


def test_account_contract_rejects_privileged_supplementary_group(monkeypatch: pytest.MonkeyPatch) -> None:
    account = SimpleNamespace(
        pw_uid=DEPLOY_UID,
        pw_gid=DEPLOY_GID,
        pw_dir="/home/cinegraph-deploy",
        pw_shell="/bin/bash",
    )
    group = SimpleNamespace(gr_gid=DEPLOY_GID)
    monkeypatch.setitem(sys.modules, "pwd", SimpleNamespace(getpwnam=lambda _name: account))
    monkeypatch.setitem(sys.modules, "grp", SimpleNamespace(getgrnam=lambda _name: group))

    def command_result(command: list[str]) -> SimpleNamespace:
        if command[1] == "-Gn":
            return SimpleNamespace(stdout="cinegraph-deploy docker\n", returncode=0)
        return SimpleNamespace(
            stdout=f"cinegraph-deploy:{DEPLOY_PASSWORD_FIELD}:1:0:99999:7:::\n",
            returncode=0,
        )

    monkeypatch.setattr(bootstrap_dev_host, "_require_success", command_result)

    with pytest.raises(BootstrapError, match="privileged"):
        bootstrap_dev_host._verify_account()


@pytest.mark.parametrize("shadow_value", ["!", "!!", "*", "locked"])
def test_account_contract_rejects_locked_or_unexpected_shadow_values(
    shadow_value: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    account = SimpleNamespace(
        pw_uid=DEPLOY_UID,
        pw_gid=DEPLOY_GID,
        pw_dir="/home/cinegraph-deploy",
        pw_shell="/bin/bash",
    )
    group = SimpleNamespace(gr_gid=DEPLOY_GID)
    monkeypatch.setitem(sys.modules, "pwd", SimpleNamespace(getpwnam=lambda _name: account))
    monkeypatch.setitem(sys.modules, "grp", SimpleNamespace(getgrnam=lambda _name: group))

    def command_result(command: list[str]) -> SimpleNamespace:
        stdout = (
            "cinegraph-deploy\n"
            if command[1] == "-Gn"
            else f"cinegraph-deploy:{shadow_value}:1:0:99999:7:::\n"
        )
        return SimpleNamespace(stdout=stdout, returncode=0)

    monkeypatch.setattr(bootstrap_dev_host, "_require_success", command_result)

    with pytest.raises(BootstrapError, match="password field"):
        bootstrap_dev_host._verify_account()


def test_bootstrap_apply_is_idempotent_at_orchestration_boundary(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(bootstrap_dev_host, "_validate_platform_and_tools", lambda: None)
    monkeypatch.setattr(bootstrap_dev_host, "_verify_bootstrap_checkout", lambda: "a" * 40)
    monkeypatch.setattr(bootstrap_dev_host, "read_single_public_key", lambda _: _public_key())
    monkeypatch.setattr(
        bootstrap_dev_host,
        "fingerprint",
        lambda _: "SHA256:" + "A" * 43,
    )
    monkeypatch.setattr(bootstrap_dev_host, "_account_exists", lambda: True)
    monkeypatch.setattr(bootstrap_dev_host, "_verify_account", lambda: calls.append("account"))
    monkeypatch.setattr(bootstrap_dev_host, "DIRECTORY_CONTRACT", ())
    monkeypatch.setattr(
        bootstrap_dev_host,
        "_ensure_host_files",
        lambda _key, *, apply: calls.append(f"files:{apply}"),
    )
    monkeypatch.setattr(
        bootstrap_dev_host,
        "_host_evidence",
        lambda host, mode: {
            "host": host,
            "mode": mode,
            "status": "bootstrap-applied",
        },
    )

    for _ in range(2):
        evidence = bootstrap_dev_host.bootstrap(
            public_key_file=Path("unused"),
            expected_fingerprint="SHA256:" + "A" * 43,
            host="dev.example.com",
            check=False,
        )
        assert evidence["status"] == "bootstrap-applied"

    assert calls == ["account", "files:True", "account", "files:True"]


def test_check_mode_runs_existing_fail_closed_runtime_validator(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(bootstrap_dev_host, "_validate_platform_and_tools", lambda: None)
    monkeypatch.setattr(bootstrap_dev_host, "_verify_bootstrap_checkout", lambda: "a" * 40)
    monkeypatch.setattr(bootstrap_dev_host, "read_single_public_key", lambda _: _public_key())
    monkeypatch.setattr(bootstrap_dev_host, "fingerprint", lambda _: "SHA256:" + "A" * 43)
    monkeypatch.setattr(bootstrap_dev_host, "_account_exists", lambda: True)
    monkeypatch.setattr(bootstrap_dev_host, "_verify_account", lambda: None)
    monkeypatch.setattr(bootstrap_dev_host, "DIRECTORY_CONTRACT", ())
    monkeypatch.setattr(bootstrap_dev_host, "_ensure_host_files", lambda _key, *, apply: None)
    monkeypatch.setattr(bootstrap_dev_host, "_verify_runtime_contract", lambda: calls.append("runtime"))
    monkeypatch.setattr(
        bootstrap_dev_host,
        "_host_evidence",
        lambda host, mode: {"host": host, "mode": mode, "status": "activation-ready"},
    )

    evidence = bootstrap_dev_host.bootstrap(
        public_key_file=Path("unused"),
        expected_fingerprint="SHA256:" + "A" * 43,
        host="dev.example.com",
        check=True,
    )

    assert evidence["mode"] == "check"
    assert calls == ["runtime"]


def test_safe_host_evidence_contains_only_public_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bootstrap_dev_host, "read_single_public_key", lambda _: _public_key("host"))
    monkeypatch.setattr(bootstrap_dev_host, "fingerprint", lambda _: "SHA256:" + "B" * 43)
    monkeypatch.setattr(
        bootstrap_dev_host,
        "_require_success",
        lambda _command: SimpleNamespace(stdout="amd64\n", returncode=0),
    )

    evidence = bootstrap_dev_host._host_evidence("dev.example.com", "check")
    rendered = str(evidence)

    assert evidence["status"] == "activation-ready"
    assert evidence["known_hosts"].startswith("dev.example.com ssh-ed25519 ")
    assert "OPENAI_API_KEY" not in rendered
    assert "POSTGRES_PASSWORD" not in rendered
    assert "private" not in rendered.lower()


def test_sudoers_contract_allows_only_no_argument_root_helper() -> None:
    assert f'{HELPER_PATH.as_posix()} ""' in SUDOERS_CONTENT
    assert f"{HELPER_PATH.as_posix()} *" not in SUDOERS_CONTENT
    assert "ALL=(ALL)" not in SUDOERS_CONTENT


def test_bootstrap_source_does_not_accept_private_material_or_broaden_privilege() -> None:
    text = Path("scripts/bootstrap_dev_host.py").read_text(encoding="utf-8")

    assert "--public-key-file" in text
    assert "--expected-key-fingerprint" in text
    assert "private-key" not in text
    assert '"docker"' in text
    assert "usermod" not in text
    assert "rm -rf" not in text
    assert "ssh-keyscan" not in text
    assert "prod.env" not in text
