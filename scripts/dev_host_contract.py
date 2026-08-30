"""Central, non-secret contract for the Cinegraph Dev deployment host."""

from __future__ import annotations

import base64
import binascii
import re
from pathlib import Path
from typing import Final

DEPLOY_USER: Final = "cinegraph-deploy"
DEPLOY_GROUP: Final = "cinegraph-deploy"
# A stable high-range identity avoids typical distribution-managed system IDs while
# keeping ownership verification deterministic across bootstrap and check runs.
DEPLOY_UID: Final = 20001
DEPLOY_GID: Final = 20001
DEPLOY_HOME: Final = Path("/home/cinegraph-deploy")
DEPLOY_SHELL: Final = "/bin/bash"
# OpenSSH rejects Linux accounts whose shadow field starts with "!" before it
# considers public keys. This invalid hash disables password authentication while
# leaving the account accessible to its single forced Ed25519 key.
DEPLOY_PASSWORD_FIELD: Final = "*NP*"

DEPLOY_ROOT: Final = Path("/opt/cinegraph")
RELEASES_ROOT: Final = DEPLOY_ROOT / "releases"
SHARED_ROOT: Final = DEPLOY_ROOT / "shared"
CONFIG_ROOT: Final = Path("/etc/cinegraph")
DEV_ENV_FILE: Final = CONFIG_ROOT / "dev.env"
DISPATCH_PATH: Final = Path("/usr/local/libexec/cinegraph-deploy-dispatch")
HELPER_PATH: Final = Path("/usr/local/sbin/cinegraph-deploy-dev")
SUDOERS_PATH: Final = Path("/etc/sudoers.d/cinegraph-deploy")
SAFE_PATH: Final = "/usr/sbin:/usr/bin"
REPOSITORY_URL: Final = "https://github.com/CaptainVC/Cinegraph.git"

HOST_PATTERN: Final = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?$")
FINGERPRINT_PATTERN: Final = re.compile(r"^SHA256:[A-Za-z0-9+/]{43}$")
PUBLIC_KEY_PATTERN: Final = re.compile(
    r"^ssh-ed25519 (?P<blob>[A-Za-z0-9+/]+={0,2})(?: (?P<comment>[^\r\n]+))?$"
)

REQUIRED_COMMANDS: Final = (
    "awk",
    "curl",
    "docker",
    "find",
    "flock",
    "getent",
    "git",
    "groupadd",
    "id",
    "install",
    "mktemp",
    "python3",
    "sed",
    "ssh-keygen",
    "stat",
    "sudo",
    "useradd",
    "visudo",
)

SUDOERS_CONTENT: Final = (
    f"Defaults:cinegraph-deploy env_reset,secure_path={SAFE_PATH}\n"
    'cinegraph-deploy ALL=(root) NOPASSWD: /usr/local/sbin/cinegraph-deploy-dev ""\n'
)


def validate_host(host: str) -> str:
    if not HOST_PATTERN.fullmatch(host) or ".." in host:
        raise ValueError("host must be a canonical port-22 DNS name or IPv4 address")
    return host


def validate_fingerprint(fingerprint: str) -> str:
    if not FINGERPRINT_PATTERN.fullmatch(fingerprint):
        raise ValueError("fingerprint must be one canonical SHA256 OpenSSH fingerprint")
    return fingerprint


def validate_public_key_line(line: str) -> str:
    if line != line.strip() or "\n" in line or "\r" in line:
        raise ValueError("public key must be one canonical line")
    match = PUBLIC_KEY_PATTERN.fullmatch(line)
    if match is None:
        raise ValueError("only one canonical ssh-ed25519 public key is accepted")
    try:
        decoded = base64.b64decode(match.group("blob"), validate=True)
    except (binascii.Error, ValueError) as error:
        raise ValueError("public key payload is not valid base64") from error
    if not decoded.startswith(b"\x00\x00\x00\x0bssh-ed25519"):
        raise ValueError("public key payload is not an Ed25519 SSH key")
    return line


def authorized_key_entry(public_key: str) -> str:
    validate_public_key_line(public_key)
    return f'restrict,command="{DISPATCH_PATH.as_posix()}" {public_key}\n'


def known_hosts_line(host: str, host_public_key: str) -> str:
    validate_host(host)
    validate_public_key_line(host_public_key)
    key_type, key_blob, *_ = host_public_key.split(" ", 2)
    return f"{host} {key_type} {key_blob}"
