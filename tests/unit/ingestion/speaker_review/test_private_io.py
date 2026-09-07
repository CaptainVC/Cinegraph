import errno
import os
from pathlib import Path

import pytest

from cinegraph.common.error_messages import SpeakerReviewErrorMessages
from cinegraph.ingestion.speaker_review.private_io import (
    SpeakerReviewFilesystemError,
    canonical_corpus_root,
    canonical_relative_locator,
    canonical_run_directory,
    create_run_directory,
    private_artifact_path,
    read_private_artifact,
    replace_private_file,
    resolve_relative_directory,
    resolve_relative_file,
    stable_file_snapshot,
    stable_relative_file_snapshot,
    validate_run_id,
    write_private_file_once,
)

RUN_ID = "speaker-review-" + "a" * 16


def _run(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "corpus"
    root.mkdir()
    return root, create_run_directory(root, RUN_ID)


def _make_symlink(link: Path, target: Path) -> None:
    try:
        link.symlink_to(target, target_is_directory=target.is_dir())
    except OSError as error:
        if isinstance(error, PermissionError) or getattr(error, "winerror", None) == 1314 or error.errno in {
            errno.EACCES,
            errno.EPERM,
        }:
            pytest.skip("symlink creation is not permitted")
        raise


@pytest.mark.parametrize(
    "value",
    [
        "",
        ".",
        "..",
        "a/../b",
        "/absolute",
        "C:/drive",
        "//server/share",
        "a\\b",
        "a//b",
        "a/",
        "episode.srt:stream",
        "CON",
        "nul.txt",
        "e\u0301pisode.srt",
    ],
)
def test_relative_locators_reject_noncanonical_forms(value: str) -> None:
    with pytest.raises(SpeakerReviewFilesystemError) as error:
        canonical_relative_locator(value)
    assert str(error.value) == SpeakerReviewErrorMessages.SPEAKER_REVIEW_CORPUS_PATH_INVALID


def test_relative_locator_is_posix_and_canonical() -> None:
    assert canonical_relative_locator("season-01/source.srt").as_posix() == (
        "season-01/source.srt"
    )


def test_root_must_be_physical_and_ancestor_free(tmp_path: Path) -> None:
    root = tmp_path / "corpus"
    root.mkdir()
    assert canonical_corpus_root(root) == root.resolve()

    outside = tmp_path / "outside"
    outside.mkdir()
    linked_root = tmp_path / "linked-root"
    _make_symlink(linked_root, root)
    with pytest.raises(SpeakerReviewFilesystemError):
        canonical_corpus_root(linked_root)

    linked_parent = tmp_path / "linked-parent"
    _make_symlink(linked_parent, tmp_path)
    with pytest.raises(SpeakerReviewFilesystemError):
        canonical_corpus_root(linked_parent / "corpus")

    assert outside.is_dir()


def test_relative_source_rejects_symlink_and_hardlink(tmp_path: Path) -> None:
    root = tmp_path / "corpus"
    root.mkdir()
    source = root / "episode.srt"
    source.write_text("source", encoding="utf-8")
    assert resolve_relative_file(root, "episode.srt") == source.resolve()

    outside = tmp_path / "outside.srt"
    outside.write_text("outside", encoding="utf-8")
    linked = root / "linked.srt"
    _make_symlink(linked, outside)
    with pytest.raises(SpeakerReviewFilesystemError):
        resolve_relative_file(root, "linked.srt")

    hardlinked = root / "hardlinked.srt"
    try:
        hardlinked.hardlink_to(source)
    except PermissionError:
        pytest.skip("hardlink creation is not permitted")
    with pytest.raises(SpeakerReviewFilesystemError):
        stable_relative_file_snapshot(root, "hardlinked.srt", max_bytes=100)


def test_relative_directory_rejects_links_and_stays_beneath_root(tmp_path: Path) -> None:
    root = tmp_path / "corpus"
    root.mkdir()
    season = root / "season-01"
    season.mkdir()
    assert resolve_relative_directory(root, "season-01") == season.resolve()

    outside = tmp_path / "outside-directory"
    outside.mkdir()
    linked = root / "linked-directory"
    _make_symlink(linked, outside)
    with pytest.raises(SpeakerReviewFilesystemError):
        resolve_relative_directory(root, "linked-directory")


def test_stable_snapshot_is_bounded_and_hashed(tmp_path: Path) -> None:
    source = tmp_path / "source.bin"
    source.write_bytes(b"private-bytes")
    snapshot = stable_file_snapshot(source, max_bytes=100)
    assert snapshot.content == b"private-bytes"
    assert snapshot.size == len(snapshot.content)
    assert snapshot.sha256

    with pytest.raises(SpeakerReviewFilesystemError):
        stable_file_snapshot(source, max_bytes=3)

    hardlink = tmp_path / "source-hardlink.bin"
    try:
        hardlink.hardlink_to(source)
    except PermissionError:
        pytest.skip("hardlink creation is not permitted")
    with pytest.raises(SpeakerReviewFilesystemError):
        stable_file_snapshot(hardlink, max_bytes=100)


def test_run_directory_is_exactly_root_configured_name_and_run_id(tmp_path: Path) -> None:
    root, run = _run(tmp_path)
    assert run == root / "review-runs" / RUN_ID
    assert canonical_run_directory(root, RUN_ID) == run.resolve()
    assert validate_run_id(RUN_ID) == RUN_ID

    for bad_id in ("../escape", "run", "speaker-review-zzzzzzzzzzzzzzzz"):
        with pytest.raises(SpeakerReviewFilesystemError):
            validate_run_id(bad_id)

    outside = tmp_path / "outside-run"
    outside.mkdir()
    with pytest.raises(SpeakerReviewFilesystemError):
        canonical_run_directory(root, outside.name)


def test_run_directory_link_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / "corpus"
    root.mkdir()
    (root / "review-runs").mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    _make_symlink(root / "review-runs" / RUN_ID, outside)
    with pytest.raises(SpeakerReviewFilesystemError):
        canonical_run_directory(root, RUN_ID)


def test_private_artifact_write_once_read_and_atomic_replace(tmp_path: Path) -> None:
    _, run = _run(tmp_path)
    write_private_file_once(run, "nested/request.jsonl", b"one\n")
    assert read_private_artifact(run, "nested/request.jsonl", max_bytes=100) == b"one\n"
    write_private_file_once(run, "nested/request.jsonl", b"one\n")
    with pytest.raises(SpeakerReviewFilesystemError):
        write_private_file_once(run, "nested/request.jsonl", b"two\n")
    replace_private_file(run, "nested/request.jsonl", b"two\n")
    assert read_private_artifact(run, "nested/request.jsonl", max_bytes=100) == b"two\n"
    assert not list((run / "nested").glob("*.tmp"))
    if os.name != "nt":
        assert (run / "nested").stat().st_mode & 0o777 == 0o700
        assert (run / "nested/request.jsonl").stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("operation", [write_private_file_once, replace_private_file])
def test_private_artifact_rejects_symlink_and_hardlink_leaves(
    tmp_path: Path, operation
) -> None:
    _, run = _run(tmp_path)
    outside = tmp_path / "outside.txt"
    outside.write_text("outside", encoding="utf-8")

    linked = run / "linked.txt"
    _make_symlink(linked, outside)
    with pytest.raises(SpeakerReviewFilesystemError):
        operation(run, "linked.txt", b"replacement")

    hardlinked = run / "hardlinked.txt"
    hardlinked.hardlink_to(outside)
    with pytest.raises(SpeakerReviewFilesystemError):
        operation(run, "hardlinked.txt", b"replacement")


def test_artifact_locators_cannot_escape_run_directory(tmp_path: Path) -> None:
    _, run = _run(tmp_path)
    with pytest.raises(SpeakerReviewFilesystemError):
        private_artifact_path(run, "../outside.txt")
