import json
from pathlib import Path

import pytest

from cinegraph.config import DEFAULT_SPEAKER_REVIEW_CONFIGURATION
from cinegraph.config.speaker_review_filesystem import SOURCE_MANIFEST_FILENAME
from cinegraph.domain.models.transcript import (
    SpeakerReviewCandidate,
    SpeakerReviewEvidence,
)
from cinegraph.ingestion.speaker_review.private_io import SpeakerReviewFilesystemError
from cinegraph.ingestion.speaker_review.workflow import SpeakerReviewWorkflow


class _UnusedGateway:
    def submit(self, *args, **kwargs):
        raise AssertionError("prepare must not contact the provider")

    def retrieve(self, batch_id: str):
        raise AssertionError("prepare must not contact the provider")

    def download_file(self, file_id: str) -> str:
        raise AssertionError("prepare must not contact the provider")


def _workflow() -> SpeakerReviewWorkflow:
    return SpeakerReviewWorkflow(
        gateway=_UnusedGateway(),
        configuration=DEFAULT_SPEAKER_REVIEW_CONFIGURATION,
        primary_model="gpt-5.6-luna",
        adjudication_model="gpt-5.6-luna",
        final_review_model="gpt-5.6-luna",
        primary_reasoning_effort="low",
        adjudication_reasoning_effort="low",
        final_review_reasoning_effort="low",
    )


def _corpus(tmp_path: Path) -> tuple[Path, Path, bytes]:
    root = tmp_path / "private-corpus"
    root.mkdir()
    (root / "Modern Family S01 Script.pdf").write_bytes(b"private-pdf")
    aligned = root / "Modern Family season 1.en" / "script-aligned"
    aligned.mkdir(parents=True)
    subtitle = aligned / "Modern Family - 1x01 - Pilot.script-aligned.srt"
    content = b"1\n00:00:00,000 --> 00:00:01,000\nPHIL?: Hello\n"
    subtitle.write_bytes(content)
    return root, subtitle, content


def _candidate(source_name: str) -> SpeakerReviewCandidate:
    return SpeakerReviewCandidate(
        candidate_id="S01E01-C0001-L00003-test",
        source_filename=source_name,
        source_sha256="a" * 64,
        season_number=1,
        episode_number=1,
        cue_number=1,
        line_number=3,
        proposed_speaker="PHIL",
        dialogue_text="Hello",
        allowed_speakers=("PHIL",),
        evidence=(SpeakerReviewEvidence("subtitle-1", "subtitle", "PHIL", "Hello"),),
    )


def test_prepare_snapshots_sources_and_writes_only_relative_manifest_locators(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root, subtitle, source_bytes = _corpus(tmp_path)
    captured: dict[str, object] = {}

    def build_candidates(**kwargs):
        captured.update(kwargs)
        return (_candidate(subtitle.name),)

    monkeypatch.setattr(
        "cinegraph.ingestion.speaker_review.workflow.build_speaker_review_candidates",
        build_candidates,
    )

    run_directory, state = _workflow().prepare(corpus_root=root, seasons=(1,))

    assert run_directory == root / "review-runs" / state.run_id
    assert state.run_id.startswith("speaker-review-")
    assert captured["source_pdf_name"] == "Modern Family S01 Script.pdf"
    assert captured["source_pdf_content"] == b"private-pdf"
    assert captured["aligned_subtitles"] == (
        (
            Path("Modern Family season 1.en/script-aligned") / subtitle.name,
            source_bytes,
        ),
    )
    manifest = json.loads((run_directory / SOURCE_MANIFEST_FILENAME).read_text("utf-8"))
    record = manifest["sources"][subtitle.name]
    assert record["path"] == (
        f"Modern Family season 1.en/script-aligned/{subtitle.name}"
    )
    assert str(root.resolve()) not in json.dumps(manifest)
    assert record["size"] == len(source_bytes)


def test_prepare_resume_rejects_source_drift_before_reusing_run(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root, subtitle, _ = _corpus(tmp_path)
    monkeypatch.setattr(
        "cinegraph.ingestion.speaker_review.workflow.build_speaker_review_candidates",
        lambda **_: (_candidate(subtitle.name),),
    )
    workflow = _workflow()
    workflow.prepare(corpus_root=root, seasons=(1,))
    subtitle.write_bytes(b"changed-after-prepare")

    with pytest.raises(SpeakerReviewFilesystemError):
        workflow.prepare(corpus_root=root, seasons=(1,))


@pytest.mark.parametrize("linked_source", ["pdf", "subtitle"])
def test_prepare_rejects_hardlinked_private_sources(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    linked_source: str,
) -> None:
    root, subtitle, _ = _corpus(tmp_path)
    source = (
        root / "Modern Family S01 Script.pdf"
        if linked_source == "pdf"
        else subtitle
    )
    outside = tmp_path / f"outside-{linked_source}"
    source.replace(outside)
    try:
        source.hardlink_to(outside)
    except (NotImplementedError, PermissionError, OSError):
        pytest.skip("hardlink creation is not available")
    monkeypatch.setattr(
        "cinegraph.ingestion.speaker_review.workflow.build_speaker_review_candidates",
        lambda **_: (_candidate(subtitle.name),),
    )

    with pytest.raises(SpeakerReviewFilesystemError):
        _workflow().prepare(corpus_root=root, seasons=(1,))
