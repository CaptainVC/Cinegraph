from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path

import pytest
from scripts import prepare_private_speaker_review_workspace as worker

from cinegraph.common.private_corpus_bundle import MANIFEST_FILENAME
from cinegraph.domain.enums.enum import SpeakerReviewRunStatus
from cinegraph.ingestion.speaker_review.workflow import SpeakerReviewRunState


def _state() -> SpeakerReviewRunState:
    return SpeakerReviewRunState(
        schema_version=5,
        run_id="speaker-review-0123456789abcdef",
        status=SpeakerReviewRunStatus.PREPARED,
        created_at="2026-09-01T00:00:00+00:00",
        updated_at="2026-09-01T00:00:00+00:00",
        candidate_count=4,
        primary_model="gpt-5.6-luna",
        adjudication_model="gpt-5.6-terra",
        prompt_version="speaker-review-v1",
        maximum_cost_usd=5.0,
        estimated_primary_cost_usd=0.123456789,
        actual_primary_cost_usd=0.0,
        actual_adjudication_cost_usd=0.0,
        primary_part_count=2,
    )


def test_prepare_worker_uses_langgraph_and_emits_bounded_aggregate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / MANIFEST_FILENAME).write_bytes(
        (
            json.dumps(
            {
                "schema_version": 1,
                "purpose": "speaker_review",
                "season_number": 2,
                "files": [
                    {
                        "path": "Modern Family - season 2.en/script-aligned/Episode 1.script-aligned.srt",
                        "size": 1,
                        "sha256": sha256(b"srt").hexdigest(),
                    },
                    {
                        "path": "Modern Family S02 Script.pdf",
                        "size": 3,
                        "sha256": sha256(b"pdf").hexdigest(),
                    },
                ],
                "file_count": 2,
                "total_bytes": 4,
                "source_catalogue_sha256": "a" * 64,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8")
    )
    calls: list[tuple[Path, tuple[int, ...]]] = []

    class FakeGraph:
        def prepare(self, *, corpus_root: Path, seasons: tuple[int, ...]):
            calls.append((corpus_root, seasons))
            return tmp_path / "review-runs" / _state().run_id, _state()

    run_directory = tmp_path / "review-runs" / _state().run_id
    run_directory.mkdir(parents=True)
    monkeypatch.setattr(worker, "_workflow", lambda: FakeGraph())
    monkeypatch.setattr(
        worker,
        "load_validated_run_state",
        lambda directory, configuration: (directory, _state()),
    )

    result = worker.prepare(tmp_path)

    assert calls == [(tmp_path.resolve(), (2,))]
    assert result == {
        "candidate_count": 4,
        "estimated_primary_cost_usd": 0.123457,
        "file_count": 2,
        "operation": "prepare",
        "primary_part_count": 2,
        "purpose": "speaker_review",
        "run_id": "speaker-review-0123456789abcdef",
        "season_number": 2,
        "status": "prepared",
        "total_bytes": 4,
    }
    assert set(result) == {
        "candidate_count",
        "estimated_primary_cost_usd",
        "file_count",
        "operation",
        "primary_part_count",
        "purpose",
        "run_id",
        "season_number",
        "status",
        "total_bytes",
    }


def test_offline_gateway_rejects_every_provider_operation() -> None:
    gateway = worker.OfflineSpeakerReviewGateway()
    with pytest.raises(worker.ProviderAccessRejected):
        gateway.submit("request.jsonl", b"{}", "24h", {})
    with pytest.raises(worker.ProviderAccessRejected):
        gateway.retrieve("batch")
    with pytest.raises(worker.ProviderAccessRejected):
        gateway.download_file("file")


def test_prepare_rejects_non_speaker_review_or_non_season_two_manifest(
    tmp_path: Path,
) -> None:
    (tmp_path / MANIFEST_FILENAME).write_bytes(
        (
            json.dumps(
            {
                "schema_version": 1,
                "purpose": "reviewed_ingestion",
                "season_number": 1,
                "files": [
                    {
                        "path": "Modern Family S01 Script.pdf",
                        "size": 1,
                        "sha256": sha256(b"x").hexdigest(),
                    }
                ],
                "file_count": 1,
                "total_bytes": 1,
                "source_catalogue_sha256": "a" * 64,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8")
    )
    with pytest.raises(ValueError):
        worker.prepare(tmp_path)
