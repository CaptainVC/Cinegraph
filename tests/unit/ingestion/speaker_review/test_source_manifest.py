import errno
import hashlib
import json
from pathlib import Path

import pytest

from cinegraph.config import DEFAULT_SPEAKER_REVIEW_CONFIGURATION
from cinegraph.ingestion.speaker_review.private_io import (
    PrivateFileSnapshot,
    SpeakerReviewFilesystemError,
    create_run_directory,
    stable_file_snapshot,
)
from cinegraph.ingestion.speaker_review.source_manifest import (
    load_source_texts,
    source_manifest_payload,
)

RUN_ID = "speaker-review-" + "a" * 16
SOURCE_NAME = "Modern Family - 1x01.script-aligned.srt"
SOURCE_TEXT = "1\n00:00:01,000 --> 00:00:02,000\nCLAIRE?: Hello there.\n"


def _symlink(link: Path, target: Path) -> None:
    try:
        link.symlink_to(target, target_is_directory=target.is_dir())
    except OSError as error:
        if isinstance(error, PermissionError) or getattr(error, "winerror", None) == 1314 or error.errno in {
            errno.EACCES,
            errno.EPERM,
        }:
            pytest.skip("symlink creation is not permitted")
        raise


def _fixture(tmp_path: Path) -> tuple[Path, Path, Path, PrivateFileSnapshot]:
    root = tmp_path / "corpus"
    root.mkdir()
    source = root / SOURCE_NAME
    source.write_bytes(SOURCE_TEXT.encode("utf-8"))
    run = create_run_directory(root, RUN_ID)
    snapshot = stable_file_snapshot(source, max_bytes=32 * 1024 * 1024)
    return root, run, source, snapshot


def _write_manifest(run: Path, payload: object) -> None:
    (run / "source-manifest.json").write_text(
        json.dumps(payload, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def test_versioned_manifest_loads_root_relative_source_and_checks_snapshot(tmp_path: Path) -> None:
    root, run, _, snapshot = _fixture(tmp_path)
    _write_manifest(
        run,
        source_manifest_payload(
            root,
            {SOURCE_NAME: snapshot},
            DEFAULT_SPEAKER_REVIEW_CONFIGURATION,
        ),
    )

    assert load_source_texts(run, RUN_ID, DEFAULT_SPEAKER_REVIEW_CONFIGURATION) == {
        SOURCE_NAME: SOURCE_TEXT
    }
    payload = json.loads((run / "source-manifest.json").read_text(encoding="utf-8"))
    assert payload["sources"][SOURCE_NAME]["path"] == SOURCE_NAME
    assert payload["sources"][SOURCE_NAME]["sha256"] == hashlib.sha256(
        SOURCE_TEXT.encode()
    ).hexdigest()


@pytest.mark.parametrize("field", ["sha256", "size"])
def test_versioned_manifest_rejects_digest_or_size_drift(tmp_path: Path, field: str) -> None:
    root, run, _, snapshot = _fixture(tmp_path)
    payload = source_manifest_payload(
        root, {SOURCE_NAME: snapshot}, DEFAULT_SPEAKER_REVIEW_CONFIGURATION
    )
    if field == "sha256":
        payload["sources"][SOURCE_NAME][field] = "f" * 64  # type: ignore[index]
    else:
        payload["sources"][SOURCE_NAME][field] = snapshot.size + 1  # type: ignore[index]
    _write_manifest(run, payload)
    with pytest.raises(SpeakerReviewFilesystemError):
        load_source_texts(run, RUN_ID, DEFAULT_SPEAKER_REVIEW_CONFIGURATION)


@pytest.mark.parametrize(
    "locator",
    ["/escape.srt", "../escape.srt", "season/../escape.srt", "a\\b.srt", "C:/escape.srt", "//server/share.srt"],
)
def test_versioned_manifest_rejects_noncanonical_locators(tmp_path: Path, locator: str) -> None:
    root, run, _, snapshot = _fixture(tmp_path)
    payload = source_manifest_payload(
        root, {SOURCE_NAME: snapshot}, DEFAULT_SPEAKER_REVIEW_CONFIGURATION
    )
    payload["sources"][SOURCE_NAME]["path"] = locator  # type: ignore[index]
    _write_manifest(run, payload)
    with pytest.raises(SpeakerReviewFilesystemError):
        load_source_texts(run, RUN_ID, DEFAULT_SPEAKER_REVIEW_CONFIGURATION)


def test_versioned_manifest_rejects_duplicate_or_case_colliding_names(tmp_path: Path) -> None:
    root, run, _, snapshot = _fixture(tmp_path)
    payload = source_manifest_payload(
        root, {SOURCE_NAME: snapshot}, DEFAULT_SPEAKER_REVIEW_CONFIGURATION
    )
    payload["sources"][SOURCE_NAME.upper()] = payload["sources"][SOURCE_NAME]  # type: ignore[index]
    _write_manifest(run, payload)
    with pytest.raises(SpeakerReviewFilesystemError):
        load_source_texts(run, RUN_ID, DEFAULT_SPEAKER_REVIEW_CONFIGURATION)


def test_manifest_rejects_duplicate_json_keys(tmp_path: Path) -> None:
    _, run, _, _ = _fixture(tmp_path)
    (run / "source-manifest.json").write_text(
        '{"schema_version":1,"schema_version":1,"sources":{}}\n',
        encoding="utf-8",
    )

    with pytest.raises(SpeakerReviewFilesystemError):
        load_source_texts(run, RUN_ID, DEFAULT_SPEAKER_REVIEW_CONFIGURATION)


def test_legacy_absolute_path_under_derived_root_is_accepted(tmp_path: Path) -> None:
    root, run, source, _ = _fixture(tmp_path)
    _write_manifest(run, {"sources": {SOURCE_NAME: str(source)}})
    assert load_source_texts(run, RUN_ID, DEFAULT_SPEAKER_REVIEW_CONFIGURATION)[SOURCE_NAME] == SOURCE_TEXT


def test_legacy_absolute_path_outside_derived_root_is_rejected(tmp_path: Path) -> None:
    _, run, _, _ = _fixture(tmp_path)
    outside = tmp_path / "outside.srt"
    outside.write_text(SOURCE_TEXT, encoding="utf-8")
    _write_manifest(run, {"sources": {SOURCE_NAME: str(outside)}})
    with pytest.raises(SpeakerReviewFilesystemError):
        load_source_texts(run, RUN_ID, DEFAULT_SPEAKER_REVIEW_CONFIGURATION)


@pytest.mark.parametrize("run_name", ["arbitrary", "speaker-review-bad", "speaker-review-" + "b" * 15])
def test_arbitrary_or_misnamed_run_directory_is_rejected(tmp_path: Path, run_name: str) -> None:
    root, run, _, snapshot = _fixture(tmp_path)
    bad_run = root / "review-runs" / run_name
    bad_run.mkdir()
    _write_manifest(
        bad_run,
        source_manifest_payload(root, {SOURCE_NAME: snapshot}, DEFAULT_SPEAKER_REVIEW_CONFIGURATION),
    )
    with pytest.raises(SpeakerReviewFilesystemError):
        load_source_texts(bad_run, run_name, DEFAULT_SPEAKER_REVIEW_CONFIGURATION)
    assert run.is_dir()


def test_symlinked_run_directory_is_rejected(tmp_path: Path) -> None:
    root, run, _, snapshot = _fixture(tmp_path)
    linked = root / "review-runs" / ("speaker-review-" + "c" * 16)
    _symlink(linked, run)
    _write_manifest(
        run,
        source_manifest_payload(root, {SOURCE_NAME: snapshot}, DEFAULT_SPEAKER_REVIEW_CONFIGURATION),
    )
    with pytest.raises(SpeakerReviewFilesystemError):
        load_source_texts(linked, linked.name, DEFAULT_SPEAKER_REVIEW_CONFIGURATION)
