"""Fail-closed validation for the single-host Cinegraph Compose runtime.

The validator deliberately never prints values from the environment file. It
checks the operator-owned contract before Docker is allowed to render or start
the stack. It is stdlib-only so it can be run during host provisioning.
"""

from __future__ import annotations

import argparse
import os
import platform
import re
import shutil
import socket
import stat
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Final
from urllib.parse import unquote, urlsplit

REQUIRED_VALUES = (
    "OPENAI_API_KEY",
    "POSTGRES_USER",
    "POSTGRES_PASSWORD",
    "POSTGRES_DB",
    "CINEGRAPH_DATABASE_URL",
    "CINEGRAPH_QDRANT_URL",
    "CINEGRAPH_STACK_NAME",
    "CINEGRAPH_QDRANT_COLLECTION_NAME",
    "CINEGRAPH_PUBLISHED_PORT",
    "CINEGRAPH_IDENTITY_DATABASE_PATH",
    "CINEGRAPH_IMAGE",
    "CINEGRAPH_IMAGE_DIGEST",
    "CINEGRAPH_RELEASE_SHA",
    "CINEGRAPH_API_HOST",
    "CINEGRAPH_API_PORT",
)
REQUIRED_ENVIRONMENT_VALUES = (
    "CINEGRAPH_ENVIRONMENT",
    "CINEGRAPH_ENV_FILE",
    "CINEGRAPH_QDRANT_MODE",
)
PLACEHOLDER_MARKERS = ("REPLACE_", "CHANGE_ME", "YOUR_", "<", ">")
DEFAULT_PUBLISHED_PORT = 18_000
MIN_FREE_DISK_BYTES = 20 * 1024**3
MIN_MEMORY_BYTES = 4 * 1024**3
EXPECTED_IMAGE_NAME: Final = "ghcr.io/captainvc/cinegraph"
IMAGE_DIGEST_PATTERN: Final = r"^sha256:[0-9a-f]{64}$"
RELEASE_SHA_PATTERN: Final = r"^[0-9a-f]{40}$"
# Intentional container-wide bind; the host publishes it on loopback only.
CONTAINER_API_HOST: Final = "0.0.0.0"  # nosec B104


@dataclass(frozen=True, slots=True)
class EnvironmentContract:
    stack_name: str
    collection_name: str
    published_port: int
    database_user: str
    database_name: str
    identity_database_path: str


ENVIRONMENT_CONTRACTS: Final = {
    "development": EnvironmentContract(
        "cinegraph-dev", "transcript_segments_development", 18_000,
        "cinegraph_dev", "cinegraph_dev", "/app/knowledge/cinegraph-development.sqlite3",
    ),
    "production": EnvironmentContract(
        "cinegraph-prod", "transcript_segments_production", 18_001,
        "cinegraph_prod", "cinegraph_prod", "/app/knowledge/cinegraph-production.sqlite3",
    ),
}


@dataclass(frozen=True, slots=True)
class ValidationIssue:
    code: str
    message: str


def parse_env_file(path: Path) -> dict[str, str]:
    """Parse the simple KEY=VALUE contract used by deployment env files."""

    values: dict[str, str] = {}
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ValueError(f"invalid env syntax at line {line_number}")
        key, value = line.split("=", 1)
        key = key.strip()
        if not key or not key.replace("_", "").isalnum() or not key[0].isalpha():
            raise ValueError(f"invalid env key at line {line_number}")
        if key in values:
            raise ValueError(f"duplicate env key at line {line_number}")
        values[key] = value
    return values


def _secret_file_issue(path: Path) -> ValidationIssue | None:
    if os.name == "nt":
        return None
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode & 0o077:
        return ValidationIssue("env-permissions", "env file must not be readable by group or other")
    return None


def validate_env_file(path: Path, expected_environment: str | None = None) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    if not path.is_file():
        return [ValidationIssue("env-missing", "env file does not exist")]
    permission_issue = _secret_file_issue(path)
    if permission_issue is not None:
        issues.append(permission_issue)
    try:
        values = parse_env_file(path)
    except (OSError, UnicodeError, ValueError) as error:
        return [*issues, ValidationIssue("env-invalid", str(error))]
    for key in (*REQUIRED_ENVIRONMENT_VALUES, *REQUIRED_VALUES):
        if not values.get(key, "").strip():
            issues.append(ValidationIssue("env-required", f"required setting {key} is missing"))
    environment = values.get("CINEGRAPH_ENVIRONMENT", "")
    if expected_environment is not None and environment != expected_environment:
        issues.append(
            ValidationIssue("environment-mismatch", f"env file is not configured for {expected_environment}")
        )
    if environment not in {"development", "production"}:
        issues.append(ValidationIssue("environment-invalid", "CINEGRAPH_ENVIRONMENT must be development or production"))
    contract = ENVIRONMENT_CONTRACTS.get(environment)
    if contract is not None:
        expected = {
            "CINEGRAPH_STACK_NAME": contract.stack_name,
            "CINEGRAPH_QDRANT_COLLECTION_NAME": contract.collection_name,
            "CINEGRAPH_IDENTITY_DATABASE_PATH": contract.identity_database_path,
            "POSTGRES_USER": contract.database_user,
            "POSTGRES_DB": contract.database_name,
        }
        for key, expected_value in expected.items():
            if values.get(key) != expected_value:
                issues.append(ValidationIssue("environment-isolation", f"{key} does not match the {environment} contract"))
        try:
            configured_port = int(values.get("CINEGRAPH_PUBLISHED_PORT", ""))
        except ValueError:
            configured_port = -1
        if configured_port != contract.published_port:
            issues.append(ValidationIssue("environment-isolation", f"published port does not match the {environment} contract"))
    # The API must bind all container interfaces so the published host port can reach it.
    if values.get("CINEGRAPH_API_HOST") != CONTAINER_API_HOST:
        issues.append(ValidationIssue("api-host", "CINEGRAPH_API_HOST must be 0.0.0.0 inside the container"))
    try:
        api_port = int(values.get("CINEGRAPH_API_PORT", ""))
    except ValueError:
        api_port = -1
    if api_port != 8000:
        issues.append(ValidationIssue("api-port", "CINEGRAPH_API_PORT must be 8000 inside the container"))
    if values.get("CINEGRAPH_IMAGE") != EXPECTED_IMAGE_NAME:
        issues.append(ValidationIssue("image-name", "CINEGRAPH_IMAGE must be the approved GHCR image name"))
    if not re.fullmatch(IMAGE_DIGEST_PATTERN, values.get("CINEGRAPH_IMAGE_DIGEST", "")):
        issues.append(
            ValidationIssue(
                "image-digest",
                "CINEGRAPH_IMAGE_DIGEST must be sha256 followed by 64 lowercase hexadecimal characters",
            )
        )
    if not re.fullmatch(RELEASE_SHA_PATTERN, values.get("CINEGRAPH_RELEASE_SHA", "")):
        issues.append(
            ValidationIssue(
                "release-sha",
                "CINEGRAPH_RELEASE_SHA must be a 40-character lowercase Git SHA",
            )
        )
    if values.get("CINEGRAPH_QDRANT_MODE") != "remote":
        issues.append(ValidationIssue("qdrant-mode", "Compose runtime requires remote Qdrant mode"))
    database_url = values.get("CINEGRAPH_DATABASE_URL", "")
    if environment == "production" and not database_url.startswith("postgresql+psycopg://"):
        issues.append(ValidationIssue("database-dialect", "production requires a postgresql+psycopg database URL"))
    qdrant_url = values.get("CINEGRAPH_QDRANT_URL", "")
    try:
        parsed_qdrant = urlsplit(qdrant_url)
        qdrant_url_is_valid = (
            parsed_qdrant.scheme in {"http", "https"}
            and bool(parsed_qdrant.netloc)
            and bool(parsed_qdrant.hostname)
        )
    except ValueError:
        qdrant_url_is_valid = False
    if not qdrant_url_is_valid:
        issues.append(ValidationIssue("qdrant-url", "Qdrant URL must be an absolute HTTP(S) URL"))
    for key, value in values.items():
        if any(marker in value for marker in PLACEHOLDER_MARKERS):
            issues.append(ValidationIssue("secret-placeholder", f"setting {key} still contains a placeholder"))
    try:
        parsed_database = urlsplit(database_url)
        database_matches_compose = (
            parsed_database.scheme == "postgresql+psycopg"
            and parsed_database.hostname == "postgres"
            and parsed_database.username == values.get("POSTGRES_USER")
            and unquote(parsed_database.password or "") == values.get("POSTGRES_PASSWORD")
            and parsed_database.path.removeprefix("/") == values.get("POSTGRES_DB")
        )
    except ValueError:
        database_matches_compose = False
    if not database_matches_compose:
        issues.append(
            ValidationIssue(
                "database-credentials",
                "database URL must match the Compose postgres user, password, database, and host",
            )
        )
    configured_path = values.get("CINEGRAPH_ENV_FILE", "")
    if configured_path and Path(configured_path).resolve() != path.resolve():
        issues.append(ValidationIssue("env-self-reference", "CINEGRAPH_ENV_FILE must resolve to the validated file"))
    return issues


def validate_tools() -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    if shutil.which("docker") is None:
        issues.append(ValidationIssue("docker-missing", "docker executable was not found"))
    if shutil.which("docker") is not None:
        result = subprocess.run(
            ["docker", "compose", "version"], capture_output=True, text=True, check=False
        )
        if result.returncode != 0:
            issues.append(ValidationIssue("compose-missing", "docker compose plugin is unavailable"))
        daemon = subprocess.run(["docker", "info"], capture_output=True, check=False)
        if daemon.returncode != 0:
            issues.append(ValidationIssue("docker-daemon", "docker daemon is not available"))
    return issues


def validate_host(env_file: Path, allow_active_port: bool = False) -> list[ValidationIssue]:
    """Check host assumptions that Compose cannot express portably."""

    issues: list[ValidationIssue] = []
    if platform.system() != "Linux":
        issues.append(ValidationIssue("os-unsupported", "VPS runtime requires a Linux host"))
    try:
        free_bytes = shutil.disk_usage(env_file.parent).free
        if free_bytes < MIN_FREE_DISK_BYTES:
            issues.append(ValidationIssue("disk-space", "host has less than the minimum free disk space"))
    except OSError:
        issues.append(ValidationIssue("disk-space", "host disk usage could not be checked"))
    if hasattr(os, "sysconf"):
        try:
            memory_bytes = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
            if memory_bytes < MIN_MEMORY_BYTES:
                issues.append(ValidationIssue("memory", "host has less than the minimum memory"))
        except (OSError, ValueError):
            issues.append(ValidationIssue("memory", "host memory could not be checked"))
    try:
        values = parse_env_file(env_file)
        port = int(values.get("CINEGRAPH_PUBLISHED_PORT", str(DEFAULT_PUBLISHED_PORT)))
        if not 1 <= port <= 65_535:
            raise ValueError
    except (OSError, ValueError):
        issues.append(ValidationIssue("port-invalid", "published API port must be between 1 and 65535"))
        return issues
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind(("127.0.0.1", port))
        except OSError:
            if allow_active_port:
                return issues
            issues.append(ValidationIssue("port-in-use", "published API loopback port is unavailable"))
    return issues


def validate_compose(compose_file: Path, env_file: Path) -> list[ValidationIssue]:
    if not compose_file.is_file():
        return [ValidationIssue("compose-missing", "compose file does not exist")]
    if shutil.which("docker") is None:
        return [ValidationIssue("docker-missing", "docker executable was not found")]
    environment = os.environ.copy()
    environment["CINEGRAPH_ENV_FILE"] = str(env_file)
    result = subprocess.run(
        ["docker", "compose", "--env-file", str(env_file), "-f", str(compose_file), "config", "--quiet"],
        capture_output=True,
        text=True,
        check=False,
        env=environment,
    )
    if result.returncode:
        return [ValidationIssue("compose-invalid", "docker compose could not render the stack")]
    return []


def validate_runtime(
    env_file: Path,
    compose_file: Path,
    expected_environment: str | None = None,
    check_tools: bool = True,
    check_compose: bool = True,
    check_host: bool = True,
    allow_active_port: bool = False,
) -> list[ValidationIssue]:
    issues = validate_env_file(env_file, expected_environment)
    if check_host and not issues:
        issues.extend(validate_host(env_file, allow_active_port))
    if check_tools:
        issues.extend(validate_tools())
    if check_compose and not issues:
        issues.extend(validate_compose(compose_file, env_file))
    return issues


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--compose-file", type=Path, default=Path("deploy/compose.yaml"))
    parser.add_argument("--environment", choices=("development", "production"))
    parser.add_argument("--skip-tools", action="store_true")
    parser.add_argument("--skip-compose", action="store_true")
    parser.add_argument(
        "--allow-active-port",
        action="store_true",
        help="allow the current deployment to keep its loopback port during an upgrade",
    )
    args = parser.parse_args()
    issues = validate_runtime(
        args.env_file,
        args.compose_file,
        args.environment,
        check_tools=not args.skip_tools,
        check_compose=not args.skip_compose,
        allow_active_port=args.allow_active_port,
    )
    if issues:
        for issue in issues:
            print(f"ERROR [{issue.code}] {issue.message}", file=sys.stderr)
        return 1
    print("Cinegraph VPS runtime preflight passed (secret values omitted).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
