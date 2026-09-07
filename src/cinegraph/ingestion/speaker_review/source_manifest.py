from __future__ import annotations

import json
import os
import re
from dataclasses import replace
from pathlib import Path

from cinegraph.common.error_messages import SpeakerReviewErrorMessages
from cinegraph.config import SpeakerReviewConfiguration
from cinegraph.config.speaker_review_filesystem import (
    DEFAULT_SPEAKER_REVIEW_FILESYSTEM_CONFIGURATION,
    PRIVATE_ARTIFACT_MAX_BYTES,
    PRIVATE_SOURCE_MAX_BYTES,
    SOURCE_MANIFEST_FILENAME,
    SpeakerReviewFilesystemConfiguration,
)
from cinegraph.ingestion.speaker_review.private_io import (
    PrivateFileSnapshot,
    SpeakerReviewFilesystemError,
    canonical_corpus_root,
    canonical_relative_locator,
    canonical_run_directory,
    read_private_artifact,
    stable_relative_file_snapshot,
)
from cinegraph.ingestion.subtitle_alignment.subtitle_parser import (
    decode_subtitle_text,
)

_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")


def speaker_review_filesystem_configuration(
    configuration: SpeakerReviewConfiguration,
) -> SpeakerReviewFilesystemConfiguration:
    return replace(
        DEFAULT_SPEAKER_REVIEW_FILESYSTEM_CONFIGURATION,
        run_directory_name=configuration.run_directory_name,
    )


def validate_run_directory(
    run_directory: Path,
    run_id: str,
    configuration: SpeakerReviewConfiguration,
) -> tuple[Path, Path]:
    filesystem_configuration = speaker_review_filesystem_configuration(configuration)
    lexical = Path(os.path.abspath(run_directory))
    root_candidate = lexical.parent.parent
    canonical = canonical_run_directory(
        root_candidate,
        run_id,
        configuration=filesystem_configuration,
    )
    if os.path.normcase(os.fspath(canonical)) != os.path.normcase(
        os.fspath(lexical)
    ):
        raise SpeakerReviewFilesystemError(
            SpeakerReviewErrorMessages.SPEAKER_REVIEW_RUN_DIRECTORY_INVALID
        )
    return canonical, canonical_corpus_root(
        root_candidate,
        configuration=filesystem_configuration,
    )


def source_manifest_payload(
    corpus_root: Path,
    sources: dict[str, PrivateFileSnapshot],
    configuration: SpeakerReviewConfiguration,
) -> dict[str, object]:
    filesystem_configuration = speaker_review_filesystem_configuration(configuration)
    root = canonical_corpus_root(
        corpus_root,
        configuration=filesystem_configuration,
    )
    records: dict[str, object] = {}
    seen_names: set[str] = set()
    for filename, snapshot in sorted(sources.items()):
        name = canonical_relative_locator(
            filename,
            configuration=filesystem_configuration,
        )
        if len(name.parts) != 1 or name.name.casefold() in seen_names:
            raise SpeakerReviewFilesystemError(
                SpeakerReviewErrorMessages.SPEAKER_REVIEW_SOURCE_FILE_INVALID
            )
        seen_names.add(name.name.casefold())
        try:
            locator = snapshot.path.relative_to(root).as_posix()
        except ValueError:
            raise SpeakerReviewFilesystemError(
                SpeakerReviewErrorMessages.SPEAKER_REVIEW_SOURCE_FILE_INVALID
            ) from None
        canonical_relative_locator(
            locator,
            configuration=filesystem_configuration,
        )
        records[name.name] = {
            "path": locator,
            "sha256": snapshot.sha256,
            "size": snapshot.size,
        }
    return {
        "schema_version": filesystem_configuration.filesystem_schema_version,
        "sources": records,
    }


def load_source_texts(
    run_directory: Path,
    run_id: str,
    configuration: SpeakerReviewConfiguration,
) -> dict[str, str]:
    run, corpus_root = validate_run_directory(
        run_directory,
        run_id,
        configuration,
    )
    filesystem_configuration = speaker_review_filesystem_configuration(configuration)
    raw = read_private_artifact(
        run,
        SOURCE_MANIFEST_FILENAME,
        max_bytes=PRIVATE_ARTIFACT_MAX_BYTES,
        configuration=filesystem_configuration,
    )
    try:
        payload = json.loads(raw.decode("utf-8"), object_pairs_hook=_reject_duplicates)
        if not isinstance(payload, dict):
            raise ValueError
        if set(payload) == {"sources"}:
            return _load_legacy_sources(
                payload["sources"],
                corpus_root,
                filesystem_configuration,
            )
        if set(payload) != {"schema_version", "sources"} or (
            type(payload["schema_version"]) is not int
            or payload["schema_version"]
            != filesystem_configuration.filesystem_schema_version
        ):
            raise ValueError
        return _load_versioned_sources(
            payload["sources"],
            corpus_root,
            filesystem_configuration,
        )
    except SpeakerReviewFilesystemError:
        raise
    except (UnicodeError, json.JSONDecodeError, TypeError, ValueError):
        raise SpeakerReviewFilesystemError(
            SpeakerReviewErrorMessages.SPEAKER_REVIEW_SOURCE_MANIFEST_INVALID
        ) from None


def _load_versioned_sources(
    raw_sources: object,
    corpus_root: Path,
    filesystem_configuration: SpeakerReviewFilesystemConfiguration,
) -> dict[str, str]:
    if not isinstance(raw_sources, dict):
        raise ValueError
    texts: dict[str, str] = {}
    seen_names: set[str] = set()
    for filename, raw_record in sorted(raw_sources.items()):
        name = _source_name(filename, seen_names, filesystem_configuration)
        if not isinstance(raw_record, dict) or set(raw_record) != {
            "path",
            "sha256",
            "size",
        }:
            raise ValueError
        raw_locator = raw_record["path"]
        if not isinstance(raw_locator, str):
            raise ValueError
        locator = canonical_relative_locator(
            raw_locator,
            configuration=filesystem_configuration,
        )
        if locator.name != name:
            raise ValueError
        expected_hash = raw_record["sha256"]
        expected_size = raw_record["size"]
        if (
            not isinstance(expected_hash, str)
            or _SHA256_PATTERN.fullmatch(expected_hash) is None
            or type(expected_size) is not int
            or expected_size <= 0
            or expected_size > PRIVATE_SOURCE_MAX_BYTES
        ):
            raise ValueError
        snapshot = stable_relative_file_snapshot(
            corpus_root,
            locator.as_posix(),
            max_bytes=PRIVATE_SOURCE_MAX_BYTES,
            configuration=filesystem_configuration,
        )
        if snapshot.sha256 != expected_hash or snapshot.size != expected_size:
            raise ValueError
        texts[name] = decode_subtitle_text(snapshot.content, name)
    return texts


def _load_legacy_sources(
    raw_sources: object,
    corpus_root: Path,
    filesystem_configuration: SpeakerReviewFilesystemConfiguration,
) -> dict[str, str]:
    if not isinstance(raw_sources, dict):
        raise ValueError
    texts: dict[str, str] = {}
    seen_names: set[str] = set()
    for filename, raw_path in sorted(raw_sources.items()):
        name = _source_name(filename, seen_names, filesystem_configuration)
        if not isinstance(raw_path, str) or not Path(raw_path).is_absolute():
            raise ValueError
        try:
            locator = Path(raw_path).resolve(strict=True).relative_to(
                corpus_root
            ).as_posix()
        except (OSError, ValueError):
            raise ValueError from None
        if Path(locator).name != name:
            raise ValueError
        snapshot = stable_relative_file_snapshot(
            corpus_root,
            locator,
            max_bytes=PRIVATE_SOURCE_MAX_BYTES,
            configuration=filesystem_configuration,
        )
        texts[name] = decode_subtitle_text(snapshot.content, name)
    return texts


def _source_name(
    value: object,
    seen_names: set[str],
    filesystem_configuration: SpeakerReviewFilesystemConfiguration,
) -> str:
    if not isinstance(value, str):
        raise ValueError
    locator = canonical_relative_locator(
        value,
        configuration=filesystem_configuration,
    )
    folded = locator.name.casefold()
    if len(locator.parts) != 1 or folded in seen_names:
        raise ValueError
    seen_names.add(folded)
    return locator.name


def _reject_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError
        result[key] = value
    return result
