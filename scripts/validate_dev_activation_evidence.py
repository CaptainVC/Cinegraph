"""Validate a sanitized, public-safe Dev activation evidence record."""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Final, cast

SCHEMA_VERSION: Final = 1
ENVIRONMENT: Final = "dev"
SUCCESS: Final = "success"
REPOSITORY_ACTIONS_URL: Final = "https://github.com/CaptainVC/Cinegraph/actions/runs/"
ATTESTATION_SIGNER: Final = "CaptainVC/Cinegraph/.github/workflows/publish-image.yml"
MAIN_SOURCE_REF: Final = "refs/heads/main"

SHA_PATTERN: Final = re.compile(r"^[0-9a-f]{40}$")
DIGEST_PATTERN: Final = re.compile(r"^sha256:[0-9a-f]{64}$")
RUN_URL_PATTERN: Final = re.compile(rf"^{re.escape(REPOSITORY_ACTIONS_URL)}[1-9][0-9]*$")
FINGERPRINT_PATTERN: Final = re.compile(r"^SHA256:[A-Za-z0-9+/]{43}$")
UTC_TIMESTAMP_PATTERN: Final = re.compile(
    r"^[0-9]{4}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12][0-9]|3[01])"
    r"T(?:[01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9]Z$"
)

TOP_LEVEL_FIELDS: Final = frozenset(
    {
        "schema_version",
        "environment",
        "bootstrap_sha",
        "release_sha",
        "image_tag",
        "image_digest",
        "host_key_fingerprint",
        "quality_run",
        "publish_run",
        "deploy_run",
        "attestation",
        "readiness_verified",
        "observed_at",
    }
)
RUN_FIELDS: Final = frozenset({"url", "conclusion"})
ATTESTATION_FIELDS: Final = frozenset({"verified", "signer_workflow", "source_ref"})

FORBIDDEN_FIELD_PATTERN: Final = re.compile(
    r"(?:api[_-]?key|private[_-]?key|public[_-]?key|ssh[_-]?key|known[_-]?hosts?|"
    r"secret|token|password|credential|database[_-]?url|qdrant|"
    r"env(?:ironment)?[_-]?(?:file|contents?|values?)|corpus|srt|pdf|ip[_-]?address)",
    re.IGNORECASE,
)
FORBIDDEN_VALUE_PATTERNS: Final = (
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\bOPENAI_API_KEY\b", re.IGNORECASE),
    re.compile(r"\bssh-(?:ed25519|rsa)\s+[A-Za-z0-9+/=]+"),
    re.compile(r"(?:^|[\\/])(?:\.env|knowledge|corpus)(?:$|[\\/])", re.IGNORECASE),
    re.compile(r"\.(?:srt|pdf)(?:$|\s)", re.IGNORECASE),
    re.compile(r"(?<![0-9])(?:[0-9]{1,3}\.){3}[0-9]{1,3}(?![0-9])"),
)


class EvidenceValidationError(ValueError):
    """A public activation evidence record violates its fixed contract."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise EvidenceValidationError(message)


def _reject_private_material(value: object, *, key: str | None = None) -> None:
    if key is not None and key != "host_key_fingerprint":
        _require(FORBIDDEN_FIELD_PATTERN.search(key) is None, "forbidden evidence field")
    if isinstance(value, Mapping):
        for nested_key, nested_value in value.items():
            _require(isinstance(nested_key, str), "evidence field names must be strings")
            _reject_private_material(nested_value, key=nested_key)
    elif isinstance(value, list):
        for item in value:
            _reject_private_material(item)
    elif isinstance(value, str):
        _require(
            not any(pattern.search(value) for pattern in FORBIDDEN_VALUE_PATTERNS),
            "forbidden evidence value",
        )


def _require_exact_object(value: object, fields: frozenset[str], name: str) -> Mapping[str, object]:
    _require(isinstance(value, Mapping), f"{name} must be an object")
    typed_value = cast(Mapping[str, object], value)
    _require(set(typed_value) == fields, f"{name} fields do not match the schema")
    return typed_value


def _validate_run(value: object, name: str) -> None:
    run = _require_exact_object(value, RUN_FIELDS, name)
    _require(isinstance(run["url"], str), f"{name} URL must be a string")
    run_url = cast(str, run["url"])
    _require(RUN_URL_PATTERN.fullmatch(run_url) is not None, f"{name} URL is invalid")
    _require(run["conclusion"] == SUCCESS, f"{name} did not succeed")


def _validate_utc_timestamp(value: object) -> None:
    _require(isinstance(value, str), "observation time must be a string")
    timestamp = cast(str, value)
    _require(UTC_TIMESTAMP_PATTERN.fullmatch(timestamp) is not None, "observation time is invalid")
    try:
        datetime.strptime(timestamp, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as error:
        raise EvidenceValidationError("observation time is invalid") from error


def validate_evidence(value: object) -> None:
    """Validate one decoded evidence object without returning or logging its values."""

    _reject_private_material(value)
    evidence = _require_exact_object(value, TOP_LEVEL_FIELDS, "activation evidence")
    _require(
        isinstance(evidence["schema_version"], int)
        and not isinstance(evidence["schema_version"], bool),
        "schema version must be an integer",
    )
    _require(evidence["schema_version"] == SCHEMA_VERSION, "schema version is unsupported")
    _require(evidence["environment"] == ENVIRONMENT, "environment must be Dev")

    bootstrap_sha = evidence["bootstrap_sha"]
    release_sha = evidence["release_sha"]
    image_tag = evidence["image_tag"]
    image_digest = evidence["image_digest"]
    fingerprint = evidence["host_key_fingerprint"]
    observed_at = evidence["observed_at"]
    _require(isinstance(bootstrap_sha, str), "bootstrap SHA must be a string")
    _require(SHA_PATTERN.fullmatch(cast(str, bootstrap_sha)) is not None, "bootstrap SHA is invalid")
    _require(isinstance(release_sha, str), "release SHA must be a string")
    _require(SHA_PATTERN.fullmatch(cast(str, release_sha)) is not None, "release SHA is invalid")
    _require(isinstance(image_tag, str), "image tag must be a string")
    _require(image_tag == f"sha-{release_sha}", "image tag does not match the release SHA")
    _require(isinstance(image_digest, str), "image digest must be a string")
    _require(DIGEST_PATTERN.fullmatch(cast(str, image_digest)) is not None, "image digest is invalid")
    _require(isinstance(fingerprint, str), "host fingerprint must be a string")
    _require(
        FINGERPRINT_PATTERN.fullmatch(cast(str, fingerprint)) is not None,
        "host fingerprint is invalid",
    )
    _validate_utc_timestamp(observed_at)

    _validate_run(evidence["quality_run"], "Quality run")
    _validate_run(evidence["publish_run"], "publish run")
    _validate_run(evidence["deploy_run"], "deployment run")
    run_urls = {
        cast(Mapping[str, object], evidence[name])["url"]
        for name in ("quality_run", "publish_run", "deploy_run")
    }
    _require(len(run_urls) == 3, "workflow run URLs must be distinct")

    attestation = _require_exact_object(evidence["attestation"], ATTESTATION_FIELDS, "attestation")
    _require(attestation["verified"] is True, "attestation must be verified")
    _require(attestation["signer_workflow"] == ATTESTATION_SIGNER, "attestation signer is invalid")
    _require(attestation["source_ref"] == MAIN_SOURCE_REF, "attestation source ref is invalid")
    _require(evidence["readiness_verified"] is True, "readiness must be verified")


def _reject_duplicate_object_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        _require(key not in value, "activation evidence contains duplicate fields")
        value[key] = item
    return value


def load_and_validate(path: Path) -> None:
    try:
        value = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_object_keys,
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise EvidenceValidationError("activation evidence could not be read") from error
    validate_evidence(value)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("evidence_file", type=Path)
    args = parser.parse_args(argv)
    try:
        load_and_validate(args.evidence_file)
    except EvidenceValidationError:
        print("activation evidence is invalid", file=sys.stderr)
        return 1
    print("activation evidence is valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
