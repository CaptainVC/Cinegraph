from __future__ import annotations

import errno
import hashlib
import json
import os
import stat
import struct
import subprocess
import zipfile
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Callable

import pytest
import scripts.build_private_corpus_bundle as build_cli
import scripts.stage_private_corpus_bundle as stage_cli

import cinegraph.common.private_corpus_bundle as bundle_contract
from cinegraph.common.private_corpus_bundle import (
    BundleError,
    build_bundle,
    stage_bundle,
    verify_bundle,
)

DIGEST = "a" * 64
SEASON_ROOT = "Modern_Family - season 1.en"
REVIEWED_DIRECTORY = f"{SEASON_ROOT}/reviewed"
ALIGNED_DIRECTORY = f"{SEASON_ROOT}/script-aligned"


def _reviewed_source(root: Path) -> list[str | Path]:
    reviewed = root / SEASON_ROOT / "reviewed"
    reviewed.mkdir(parents=True)
    (reviewed / "ep.reviewed.srt").write_text(
        "1\n00:00:00,000 --> 00:00:01,000\nA: hello\n", encoding="utf-8"
    )
    (reviewed / "review-ledger.json").write_text('{"records":[]}', encoding="utf-8")
    return [
        Path(REVIEWED_DIRECTORY) / "ep.reviewed.srt",
        f"{REVIEWED_DIRECTORY}/review-ledger.json",
    ]


def _speaker_source(root: Path) -> list[str]:
    season = root / SEASON_ROOT
    aligned = season / "script-aligned"
    aligned.mkdir(parents=True)
    (root / "Modern Family S01 Script.pdf").write_bytes(b"synthetic-pdf")
    (aligned / "ep.script-aligned.srt").write_bytes(b"synthetic-srt")
    return [
        "Modern Family S01 Script.pdf",
        f"{ALIGNED_DIRECTORY}/ep.script-aligned.srt",
    ]


def _build(
    source: Path,
    archive: Path,
    *,
    purpose: str = "speaker_review",
    selected: list[str | Path] | None = None,
) -> None:
    chosen = selected if selected is not None else _speaker_source(source)
    build_bundle(
        source_root=source,
        output_archive=archive,
        purpose=purpose,
        selected_paths=chosen,
        catalogue_sha256=DIGEST,
        season_number=1,
    )


def _rewrite(
    source: Path,
    destination: Path,
    mutate: Callable[[str, bytes], tuple[str, bytes]] | None = None,
    extra: tuple[zipfile.ZipInfo, bytes] | None = None,
) -> None:
    with zipfile.ZipFile(source) as original, zipfile.ZipFile(destination, "w") as edited:
        for info in original.infolist():
            name = info.filename
            value = original.read(info)
            if mutate is not None:
                name, value = mutate(name, value)
                info.filename = name
            edited.writestr(info, value)
        if extra is not None:
            edited.writestr(*extra)


def _private_parent(path: Path) -> None:
    if os.name != "nt":
        path.chmod(0o700)


def test_bundle_is_deterministic_and_stages_with_private_modes(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    selected = _reviewed_source(source)
    one = tmp_path / "one.zip"
    two = tmp_path / "two.zip"
    _build(source, one, purpose="reviewed_ingestion", selected=selected)
    _build(source, two, purpose="reviewed_ingestion", selected=list(reversed(selected)))

    assert one.read_bytes() == two.read_bytes()
    manifest, files = verify_bundle(one)
    assert manifest["source_catalogue_sha256"] == DIGEST
    assert manifest["season_number"] == 1
    assert [item.path for item in files] == sorted(item.path for item in files)
    assert "catalogue.json" not in one.read_bytes().decode("utf-8", errors="ignore")

    _private_parent(tmp_path)
    destination = tmp_path / "staged"
    result = stage_bundle(archive_path=one, destination=destination)
    assert result.file_count == 2
    assert not (destination / ".bundle.zip").exists()
    assert (destination / "manifest.json").is_file()
    if os.name != "nt":
        assert stat.S_IMODE(destination.stat().st_mode) == 0o700
        staged = destination / REVIEWED_DIRECTORY / "ep.reviewed.srt"
        assert stat.S_IMODE(staged.stat().st_mode) == 0o600


@pytest.mark.parametrize(
    "selected",
    [
        ["../outside.pdf"],
        ["C:/outside.pdf"],
        ["folder\\outside.pdf"],
        ["folder/./outside.pdf"],
        ["folder/CON.pdf"],
        ["folder/trailing .pdf"],
    ],
)
def test_builder_rejects_noncanonical_or_nonportable_paths(
    tmp_path: Path, selected: list[str]
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    with pytest.raises(BundleError):
        _build(source, tmp_path / "invalid.zip", selected=selected)


@pytest.mark.parametrize(
    ("name", "content"),
    [
        ("key.txt", b"not-a-key"),
        ("cinegraph_dev_actions_ed25519.pdf", b"not-a-key"),
        ("episode.secret.pdf", b"not-a-key"),
        ("script.pdf", b"OPENAI_API_KEY=never-log-this"),
        ("script.pdf", b"-----BEGIN OPENSSH " + b"PRIVATE" + b" KEY-----"),
    ],
)
def test_builder_rejects_forbidden_names_and_secret_markers(
    tmp_path: Path, name: str, content: bytes
) -> None:
    source = tmp_path / "source"
    season = source / SEASON_ROOT
    aligned = season / "script-aligned"
    aligned.mkdir(parents=True)
    (source / name).write_bytes(content)
    (aligned / "ep.script-aligned.srt").write_bytes(b"srt")
    with pytest.raises(BundleError):
        _build(
            source,
            tmp_path / "forbidden.zip",
            selected=[name, f"{ALIGNED_DIRECTORY}/ep.script-aligned.srt"],
        )


def test_builder_requires_digest_and_exact_purpose_layout(tmp_path: Path) -> None:
    source = tmp_path / "source"
    selected = _speaker_source(source)
    with pytest.raises(BundleError, match="catalogue digest"):
        build_bundle(
            source_root=source,
            output_archive=tmp_path / "digest.zip",
            purpose="speaker_review",
            selected_paths=selected,
            catalogue_sha256="A" * 64,
            season_number=1,
        )
    second_script = source / "extra.pdf"
    second_script.write_bytes(b"another-pdf")
    with pytest.raises(BundleError, match="layout"):
        _build(
            source,
            tmp_path / "multiple.zip",
            selected=[*selected, "extra.pdf"],
        )
    misplaced = source / SEASON_ROOT / "misplaced.script-aligned.srt"
    misplaced.write_bytes(b"misplaced")
    with pytest.raises(BundleError, match="layout"):
        _build(
            source,
            tmp_path / "misplaced.zip",
            selected=[selected[0], f"{SEASON_ROOT}/misplaced.script-aligned.srt"],
        )


def test_builder_rejects_hardlinks_and_symlinks(tmp_path: Path) -> None:
    source = tmp_path / "source"
    selected = _speaker_source(source)
    original = source / "Modern Family S01 Script.pdf"
    hardlink = source / "alias.pdf"
    try:
        os.link(original, hardlink)
    except OSError:
        pytest.skip("hardlinks are unavailable on this filesystem")
    with pytest.raises(BundleError, match="hardlinked"):
        _build(source, tmp_path / "hardlink.zip", selected=selected)
    hardlink.unlink()
    link = source / "linked.pdf"
    try:
        link.symlink_to(original)
    except OSError:
        pytest.skip("symlinks are unavailable for this test user")
    with pytest.raises(BundleError):
        _build(
            source,
            tmp_path / "symlink.zip",
            selected=["linked.pdf", selected[1]],
        )


def test_builder_enforces_size_policy_and_cleans_partial_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source"
    selected = _speaker_source(source)
    policy = replace(bundle_contract._policy(), max_file_bytes=4, max_total_bytes=8)
    monkeypatch.setattr(
        bundle_contract._bundle_config, "DEFAULT_PRIVATE_CORPUS_BUNDLE_CONFIGURATION", policy
    )
    archive = tmp_path / "oversize.zip"
    with pytest.raises(BundleError):
        _build(source, archive, selected=selected)
    assert not archive.exists()


def test_builder_rejects_invalid_boundaries_and_supports_dry_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source"
    selected = _speaker_source(source)
    with pytest.raises(BundleError, match="purpose"):
        build_bundle(
            source_root=source,
            output_archive=tmp_path / "purpose.zip",
            purpose="unknown",
            selected_paths=selected,
            catalogue_sha256=DIGEST,
            season_number=1,
        )
    with pytest.raises(BundleError, match="season"):
        build_bundle(
            source_root=source,
            output_archive=tmp_path / "season.zip",
            purpose="speaker_review",
            selected_paths=selected,
            catalogue_sha256=DIGEST,
            season_number=False,
        )
    existing = tmp_path / "existing.zip"
    existing.write_bytes(b"preserve")
    with pytest.raises(BundleError, match="already exists"):
        _build(source, existing, selected=selected)
    with pytest.raises(BundleError, match="inside"):
        _build(source, source / "inside.zip", selected=selected)
    with pytest.raises(BundleError, match="extension"):
        _build(source, tmp_path / "bundle.bin", selected=selected)
    with pytest.raises(BundleError, match="file count"):
        _build(source, tmp_path / "empty.zip", selected=[])
    with pytest.raises(BundleError, match="duplicate"):
        _build(source, tmp_path / "duplicate-source.zip", selected=[*selected, selected[1]])
    with pytest.raises(BundleError, match="relative"):
        _build(source, tmp_path / "absolute.zip", selected=[source.resolve(), selected[1]])
    with pytest.raises(BundleError, match="text"):
        _build(source, tmp_path / "nontext.zip", selected=[42, selected[1]])  # type: ignore[list-item]

    dry_run = build_bundle(
        source_root=source,
        output_archive=tmp_path / "dry-run.zip",
        purpose="speaker_review",
        selected_paths=selected,
        catalogue_sha256=DIGEST,
        season_number=1,
        dry_run=True,
    )
    assert dry_run.archive_bytes == 0
    assert not (tmp_path / "dry-run.zip").exists()

    with monkeypatch.context() as context:
        context.setattr(
            bundle_contract._bundle_config,
            "DEFAULT_PRIVATE_CORPUS_BUNDLE_CONFIGURATION",
            replace(bundle_contract._policy(), max_total_bytes=20),
        )
        with pytest.raises(BundleError, match="bundle size"):
            _build(source, tmp_path / "total.zip", selected=selected)
    with monkeypatch.context() as context:
        context.setattr(
            bundle_contract._bundle_config,
            "DEFAULT_PRIVATE_CORPUS_BUNDLE_CONFIGURATION",
            replace(bundle_contract._policy(), max_manifest_bytes=20),
        )
        with pytest.raises(BundleError, match="manifest"):
            _build(source, tmp_path / "manifest.zip", selected=selected)
    with monkeypatch.context() as context:
        context.setattr(
            bundle_contract._bundle_config,
            "DEFAULT_PRIVATE_CORPUS_BUNDLE_CONFIGURATION",
            replace(bundle_contract._policy(), max_archive_bytes=30),
        )
        with pytest.raises(BundleError, match="archive"):
            _build(source, tmp_path / "archive-size.zip", selected=selected)
        assert not (tmp_path / "archive-size.zip").exists()


def test_builder_rejects_missing_source_invalid_root_and_unexpected_extension(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    selected = _speaker_source(source)
    missing_root = tmp_path / "missing"
    with pytest.raises(BundleError, match="knowledge root"):
        _build(missing_root, tmp_path / "root.zip", selected=selected)
    with pytest.raises(BundleError, match="outside"):
        _build(
            source,
            tmp_path / "missing-source.zip",
            selected=[selected[0], f"{ALIGNED_DIRECTORY}/missing.script-aligned.srt"],
        )
    unexpected = source / SEASON_ROOT / "script-aligned" / "notes.json"
    unexpected.write_text("{}", encoding="utf-8")
    with pytest.raises(BundleError, match="unexpected"):
        _build(
            source,
            tmp_path / "unexpected.zip",
            selected=[selected[0], f"{ALIGNED_DIRECTORY}/notes.json"],
        )


def test_builder_output_race_preserves_the_competing_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source"
    selected = _speaker_source(source)
    output = tmp_path / "bundle.zip"
    original_verify = bundle_contract.verify_bundle

    def create_competitor(temporary: Path):
        verified = original_verify(temporary)
        output.write_bytes(b"owned-by-another-process")
        return verified

    monkeypatch.setattr(bundle_contract, "verify_bundle", create_competitor)
    with pytest.raises(BundleError):
        _build(source, output, selected=selected)
    assert output.read_bytes() == b"owned-by-another-process"
    assert not list(tmp_path.glob(".bundle.zip.*.tmp"))


def test_git_safety_fails_closed_and_rejects_unignored_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source"
    selected = _speaker_source(source)
    repository = tmp_path / "repository"
    (repository / ".git").mkdir(parents=True)
    staging = repository / "staging"
    staging.mkdir()

    def fail_git(*args: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        raise subprocess.TimeoutExpired("git", 3)

    monkeypatch.setattr(bundle_contract.subprocess, "run", fail_git)
    with pytest.raises(BundleError, match="could not be verified"):
        _build(source, staging / "bundle.zip", selected=selected)


@pytest.mark.parametrize(
    ("tracked_code", "ignored_code", "message"),
    [(0, 1, "not an ignored"), (1, 1, "not an ignored"), (2, 1, "could not be verified")],
)
def test_git_safety_interprets_only_expected_exit_codes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    tracked_code: int,
    ignored_code: int,
    message: str,
) -> None:
    source = tmp_path / "source"
    selected = _speaker_source(source)
    repository = tmp_path / "repository"
    (repository / ".git").mkdir(parents=True)
    staging = repository / "staging"
    staging.mkdir()
    results = iter(
        [
            subprocess.CompletedProcess([], tracked_code),
            subprocess.CompletedProcess([], ignored_code),
        ]
    )
    monkeypatch.setattr(bundle_contract.subprocess, "run", lambda *args, **kwargs: next(results))
    with pytest.raises(BundleError, match=message):
        _build(source, staging / "bundle.zip", selected=selected)


def test_manifest_decoder_rejects_schema_and_aggregate_mutations(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    archive = tmp_path / "bundle.zip"
    _build(source, archive)
    with zipfile.ZipFile(archive) as opened:
        valid = json.loads(opened.read("manifest.json"))

    invalid: list[object] = []
    for field, value in (
        ("schema_version", 2),
        ("purpose", "unknown"),
        ("season_number", 0),
        ("files", []),
        ("file_count", 99),
        ("total_bytes", 99),
        ("source_catalogue_sha256", "A" * 64),
    ):
        candidate = json.loads(json.dumps(valid))
        candidate[field] = value
        invalid.append(candidate)
    extra = json.loads(json.dumps(valid))
    extra["unexpected"] = True
    invalid.append(extra)
    bad_entry = json.loads(json.dumps(valid))
    bad_entry["files"][0] = {"path": "only-a-path"}
    invalid.append(bad_entry)
    zero_size = json.loads(json.dumps(valid))
    zero_size["total_bytes"] -= zero_size["files"][0]["size"]
    zero_size["files"][0]["size"] = 0
    invalid.append(zero_size)
    bad_hash = json.loads(json.dumps(valid))
    bad_hash["files"][0]["sha256"] = "not-a-hash"
    invalid.append(bad_hash)
    duplicate_path = json.loads(json.dumps(valid))
    duplicate_path["files"][1]["path"] = duplicate_path["files"][0]["path"]
    invalid.append(duplicate_path)

    for candidate in invalid:
        with pytest.raises(BundleError):
            bundle_contract._decode_manifest(bundle_contract._canonical_json(candidate))
    for malformed in (
        b"",
        b"\xef\xbb\xbf{}\n",
        b"not-json\n",
        b"[]\n",
        b'{"duplicate":1,"duplicate":2}\n',
    ):
        with pytest.raises(BundleError):
            bundle_contract._decode_manifest(malformed)


def test_verifier_rejects_extra_member_traversal_and_duplicate(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    archive = tmp_path / "bundle.zip"
    _build(source, archive)

    extra_info = bundle_contract._zip_info("extra.script-aligned.srt")
    extra = tmp_path / "extra.zip"
    _rewrite(archive, extra, extra=(extra_info, b"extra"))
    with pytest.raises(BundleError):
        verify_bundle(extra)

    def traversal(name: str, value: bytes) -> tuple[str, bytes]:
        if name == "manifest.json":
            manifest = json.loads(value)
            manifest["files"][0]["path"] = "../escape.pdf"
            value = bundle_contract._canonical_json(manifest)
        return name, value

    escaped = tmp_path / "escaped.zip"
    _rewrite(archive, escaped, traversal)
    with pytest.raises(BundleError):
        verify_bundle(escaped)

    duplicate = tmp_path / "duplicate.zip"
    with zipfile.ZipFile(archive) as original, zipfile.ZipFile(duplicate, "w") as edited:
        for info in original.infolist():
            edited.writestr(info, original.read(info))
        repeated = original.infolist()[-1]
        with pytest.warns(UserWarning, match="Duplicate name"):
            edited.writestr(repeated, original.read(repeated))
    with pytest.raises(BundleError):
        verify_bundle(duplicate)


def test_verifier_rejects_noncanonical_manifest_and_content_tamper(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    archive = tmp_path / "bundle.zip"
    _build(source, archive)

    def pretty_manifest(name: str, value: bytes) -> tuple[str, bytes]:
        if name == "manifest.json":
            value = json.dumps(json.loads(value), indent=2).encode("utf-8")
        return name, value

    noncanonical = tmp_path / "noncanonical.zip"
    _rewrite(archive, noncanonical, pretty_manifest)
    with pytest.raises(BundleError, match="canonical"):
        verify_bundle(noncanonical)

    def change_content(name: str, value: bytes) -> tuple[str, bytes]:
        if name.endswith(".srt"):
            value += b"tampered"
        return name, value

    tampered = tmp_path / "tampered.zip"
    _rewrite(archive, tampered, change_content)
    with pytest.raises(BundleError, match="metadata|hash"):
        verify_bundle(tampered)


def test_verifier_rejects_trailing_prefix_compression_and_secret_content(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    archive = tmp_path / "bundle.zip"
    _build(source, archive)

    trailing = tmp_path / "trailing.zip"
    trailing.write_bytes(archive.read_bytes() + b"trailing")
    with pytest.raises(BundleError):
        verify_bundle(trailing)

    prefixed = tmp_path / "prefixed.zip"
    prefixed.write_bytes(b"prefix" + archive.read_bytes())
    with pytest.raises(BundleError):
        verify_bundle(prefixed)

    compressed = tmp_path / "compressed.zip"
    with zipfile.ZipFile(archive) as original, zipfile.ZipFile(
        compressed, "w", compression=zipfile.ZIP_DEFLATED
    ) as edited:
        for old in original.infolist():
            info = bundle_contract._zip_info(old.filename)
            info.compress_type = zipfile.ZIP_DEFLATED
            edited.writestr(info, original.read(old))
    with pytest.raises(BundleError, match="member"):
        verify_bundle(compressed)

    def inject_secret(name: str, value: bytes) -> tuple[str, bytes]:
        if name.endswith(".pdf"):
            return name, b"OPENAI_API_KEY=not-allowed"
        if name == "manifest.json":
            manifest = json.loads(value)
            script = next(item for item in manifest["files"] if item["path"].endswith(".pdf"))
            secret = b"OPENAI_API_KEY=not-allowed"
            manifest["total_bytes"] += len(secret) - script["size"]
            script["size"] = len(secret)
            script["sha256"] = hashlib.sha256(secret).hexdigest()
            return name, bundle_contract._canonical_json(manifest)
        return name, value

    secret = tmp_path / "secret.zip"
    _rewrite(archive, secret, inject_secret)
    with pytest.raises(BundleError, match="content"):
        verify_bundle(secret)


def test_verifier_preflights_member_count_before_opening_central_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    archive = tmp_path / "bundle.zip"
    _build(source, archive)
    raw = bytearray(archive.read_bytes())
    count = bundle_contract._policy().max_file_count + 2
    struct.pack_into("<H", raw, len(raw) - 22 + 8, count)
    struct.pack_into("<H", raw, len(raw) - 22 + 10, count)
    archive.write_bytes(raw)

    def must_not_open(*args: object, **kwargs: object) -> zipfile.ZipFile:
        raise AssertionError("central directory was materialized before the count limit")

    monkeypatch.setattr(bundle_contract.zipfile, "ZipFile", must_not_open)
    with pytest.raises(BundleError, match="trailing or ambiguous"):
        verify_bundle(archive)


def test_verifier_rejects_missing_misordered_and_nonregular_archives(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    archive = tmp_path / "bundle.zip"
    _build(source, archive)

    with pytest.raises(BundleError, match="regular"):
        verify_bundle(tmp_path)
    malformed = tmp_path / "malformed.zip"
    malformed.write_bytes(b"not-a-zip")
    with pytest.raises(BundleError, match="malformed|trailing"):
        verify_bundle(malformed)
    hardlink = tmp_path / "hardlink.zip"
    try:
        os.link(archive, hardlink)
    except OSError:
        pass
    else:
        with pytest.raises(BundleError, match="hardlinked"):
            verify_bundle(hardlink)
        hardlink.unlink()

    missing_manifest = tmp_path / "missing-manifest.zip"
    with zipfile.ZipFile(missing_manifest, "w") as edited:
        edited.writestr(bundle_contract._zip_info("episode.script-aligned.srt"), b"srt")
    with pytest.raises(BundleError, match="manifest"):
        verify_bundle(missing_manifest)

    misordered = tmp_path / "misordered.zip"
    with zipfile.ZipFile(archive) as original, zipfile.ZipFile(misordered, "w") as edited:
        infos = original.infolist()
        for info in [*infos[1:], infos[0]]:
            edited.writestr(info, original.read(info))
    with pytest.raises(BundleError, match="manifest member"):
        verify_bundle(misordered)


def test_atomic_rename_linux_contract_handles_kernel_outcomes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[object, ...]] = []

    class Rename:
        argtypes: list[object] = []
        restype: object = None

        def __init__(self, result: int) -> None:
            self.result = result

        def __call__(self, *args: object) -> int:
            calls.append(args)
            return self.result

    monkeypatch.setattr(bundle_contract.sys, "platform", "linux")
    success = Rename(0)
    monkeypatch.setattr(bundle_contract.ctypes, "CDLL", lambda *args, **kwargs: SimpleNamespace(renameat2=success))
    bundle_contract._rename_no_replace(tmp_path / "source", tmp_path / "destination")
    assert calls[-1][-1] == 1

    monkeypatch.setattr(bundle_contract.ctypes, "CDLL", lambda *args, **kwargs: SimpleNamespace())
    with pytest.raises(OSError):
        bundle_contract._rename_no_replace(tmp_path / "source", tmp_path / "destination")

    failure = Rename(-1)
    monkeypatch.setattr(bundle_contract.ctypes, "CDLL", lambda *args, **kwargs: SimpleNamespace(renameat2=failure))
    monkeypatch.setattr(bundle_contract.ctypes, "get_errno", lambda: errno.EEXIST)
    with pytest.raises(OSError, match="exist"):
        bundle_contract._rename_no_replace(tmp_path / "source", tmp_path / "destination")


def test_stage_existing_destination_is_unchanged(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    archive = tmp_path / "bundle.zip"
    _build(source, archive)
    _private_parent(tmp_path)
    destination = tmp_path / "staged"
    destination.mkdir()
    marker = destination / "owner-data"
    marker.write_text("preserve", encoding="utf-8")
    with pytest.raises(BundleError):
        stage_bundle(archive_path=archive, destination=destination)
    assert marker.read_text(encoding="utf-8") == "preserve"


def test_stage_does_not_remove_an_existing_operator_lock(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    archive = tmp_path / "bundle.zip"
    _build(source, archive)
    _private_parent(tmp_path)
    destination = tmp_path / "staged"
    lock = tmp_path / ".staged.private-corpus.lock"
    lock.write_text("owned-by-another-process", encoding="utf-8")
    with pytest.raises(BundleError):
        stage_bundle(archive_path=archive, destination=destination)
    assert lock.read_text(encoding="utf-8") == "owned-by-another-process"


def test_stage_failure_cleans_temp_and_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    archive = tmp_path / "bundle.zip"
    _build(source, archive)
    _private_parent(tmp_path)
    destination = tmp_path / "staged"
    original = bundle_contract._verified_member_bytes
    calls = 0

    def fail_during_extract(
        opened: zipfile.ZipFile, info: zipfile.ZipInfo, item: bundle_contract.BundleFile
    ) -> bytes:
        nonlocal calls
        calls += 1
        if calls > 2:
            raise OSError("private path must never be printed")
        return original(opened, info, item)

    monkeypatch.setattr(bundle_contract, "_verified_member_bytes", fail_during_extract)
    with pytest.raises(BundleError, match="destination was not published"):
        stage_bundle(archive_path=archive, destination=destination)
    assert not destination.exists()
    assert not list(tmp_path.glob(".private-corpus-stage-*"))
    assert not list(tmp_path.glob("*.private-corpus.lock"))


def test_stage_no_replace_race_preserves_concurrent_destination(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    archive = tmp_path / "bundle.zip"
    _build(source, archive)
    _private_parent(tmp_path)
    destination = tmp_path / "staged"
    original = bundle_contract._rename_no_replace

    def race(stage: Path, final: Path) -> None:
        final.mkdir()
        original(stage, final)

    monkeypatch.setattr(bundle_contract, "_rename_no_replace", race)
    with pytest.raises(BundleError):
        stage_bundle(archive_path=archive, destination=destination)
    assert destination.is_dir()
    assert not any(destination.iterdir())
    assert not list(tmp_path.glob(".private-corpus-stage-*"))


def test_stage_uses_verified_snapshot_if_source_is_replaced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    archive = tmp_path / "bundle.zip"
    _build(source, archive)
    original_archive = archive.read_bytes()
    _private_parent(tmp_path)
    original_verify = bundle_contract.verify_bundle

    def replace_source(snapshot: Path):
        verified = original_verify(snapshot)
        archive.write_bytes(b"replaced-after-snapshot")
        return verified

    monkeypatch.setattr(bundle_contract, "verify_bundle", replace_source)
    destination = tmp_path / "staged"
    stage_bundle(archive_path=archive, destination=destination)
    assert archive.read_bytes() != original_archive
    assert (destination / ALIGNED_DIRECTORY / "ep.script-aligned.srt").read_bytes() == b"synthetic-srt"


def _catalogue(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "series": [
                    {
                        "series_id": "11111111-1111-1111-1111-111111111111",
                        "series_name": "Modern Family",
                        "seasons": [
                            {
                                "season_id": "22222222-2222-2222-2222-222222222222",
                                "season_number": 1,
                                "episodes": [
                                    {
                                        "episode_id": "33333333-3333-3333-3333-333333333333",
                                        "episode_number": 1,
                                        "episode_title": "Pilot",
                                        "reviewed_subtitle_filename": "ep.reviewed.srt",
                                    }
                                ],
                            }
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )


def test_build_cli_derives_exact_nested_speaker_selection(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    knowledge = tmp_path / "knowledge"
    _speaker_source(knowledge)
    catalogue = tmp_path / "catalogue.json"
    _catalogue(catalogue)
    output = tmp_path / "speaker.zip"
    assert (
        build_cli.main(
            [
                "--knowledge-root",
                str(knowledge),
                "--output",
                str(output),
                "--purpose",
                "speaker_review",
                "--catalogue",
                str(catalogue),
                "--season",
                "1",
            ]
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["file_count"] == 2
    manifest, files = verify_bundle(output)
    assert manifest["season_number"] == 1
    assert [item.path for item in files] == sorted(_speaker_source_paths())


def _speaker_source_paths() -> list[str]:
    return [
        "Modern Family S01 Script.pdf",
        f"{ALIGNED_DIRECTORY}/ep.script-aligned.srt",
    ]


def test_build_cli_failure_is_sanitized(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    knowledge = tmp_path / "knowledge"
    knowledge.mkdir()
    catalogue = tmp_path / "catalogue.json"
    catalogue.write_text("{}", encoding="utf-8")

    def fail_load(self: object, path: Path) -> object:
        raise ValueError("E:/private/key.txt must never appear")

    monkeypatch.setattr(build_cli.JsonCatalogueManifestLoader, "load", fail_load)
    assert (
        build_cli.main(
            [
                "--knowledge-root",
                str(knowledge),
                "--output",
                str(tmp_path / "bundle.zip"),
                "--purpose",
                "speaker_review",
                "--catalogue",
                str(catalogue),
                "--season",
                "1",
            ]
        )
        == 2
    )
    captured = capsys.readouterr()
    assert "bundle build failed" in captured.err
    assert "key.txt" not in captured.err


def test_stage_cli_prints_only_aggregate_result(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    archive = tmp_path / "bundle.zip"
    _build(source, archive)
    _private_parent(tmp_path)
    destination = tmp_path / "staged"
    assert (
        stage_cli.main(["--bundle", str(archive), "--destination", str(destination)]) == 0
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload == {"file_count": 2, "purpose": "speaker_review", "total_bytes": 26}
