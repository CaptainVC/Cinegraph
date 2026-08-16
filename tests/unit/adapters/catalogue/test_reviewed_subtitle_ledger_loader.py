import hashlib
import json
from pathlib import Path

import pytest

from cinegraph.adapters.catalogue import (
    JsonCatalogueManifestLoader,
    ReviewedSubtitleLedgerLoader,
)
from cinegraph.common.error_messages import CorpusIngestionErrorMessages
from cinegraph.domain.exceptions.errors import InvalidModelError


REVIEWED_FILENAME = "Example Family - 1x01 - Pilot.reviewed.srt"


def write_catalogue(path: Path, filename: str | None = REVIEWED_FILENAME) -> None:
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "series": [
                    {
                        "series_id": "00000000-0000-0000-0000-000000000011",
                        "series_name": "Example Family",
                        "seasons": [
                            {
                                "season_id": "00000000-0000-0000-0000-000000000101",
                                "season_number": 1,
                                "episodes": [
                                    {
                                        "episode_id": "00000000-0000-0000-0000-000000001001",
                                        "episode_number": 1,
                                        "episode_title": "Pilot",
                                        "reviewed_subtitle_filename": filename,
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


def write_ledger(path: Path, reviewed_hash: str, filename: str = REVIEWED_FILENAME) -> None:
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "review_status": "reviewed",
                "reviewed_by": "corpus-review",
                "reviewed_at": "2026-08-01T10:00:00+00:00",
                "records": [
                    {
                        "candidate_filename": "candidate.srt",
                        "reviewed_filename": filename,
                        "candidate_sha256": "a" * 64,
                        "reviewed_sha256": reviewed_hash,
                        "promoted_question_mark_labels": 1,
                        "removed_redaction_lines": 0,
                        "removed_cue_numbers": [],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )


def test_ledger_maps_episode_and_verifies_exact_file_hash(tmp_path: Path) -> None:
    catalogue_path = tmp_path / "catalogue.json"
    ledger_path = tmp_path / "review-ledger.json"
    reviewed_directory = tmp_path / "reviewed"
    reviewed_directory.mkdir()
    source_path = reviewed_directory / REVIEWED_FILENAME
    content = b"1\n00:00:00,000 --> 00:00:01,000\nClaire: Hello.\n"
    source_path.write_bytes(content)
    write_catalogue(catalogue_path)
    write_ledger(ledger_path, hashlib.sha256(content).hexdigest())
    manifest = JsonCatalogueManifestLoader().load(catalogue_path).manifest

    batch = ReviewedSubtitleLedgerLoader().load(
        manifest,
        ledger_path,
        reviewed_directory,
    )

    assert len(batch.items) == 1
    item = batch.items[0]
    assert item.source_path == source_path
    assert item.episode.position.season_number == 1
    assert item.episode.position.episode_number == 1
    assert item.episode_title == "Example Family: Pilot"
    assert item.content_sha256 == hashlib.sha256(content).hexdigest()
    assert item.reviewed_by == "corpus-review"


@pytest.mark.parametrize(
    ("catalogue_filename", "ledger_filename", "hash_value", "message"),
    [
        (
            None,
            REVIEWED_FILENAME,
            "a" * 64,
            CorpusIngestionErrorMessages.REVIEWED_SUBTITLE_MUST_MAP_TO_CATALOGUE,
        ),
        (
            REVIEWED_FILENAME,
            "missing.reviewed.srt",
            "a" * 64,
            CorpusIngestionErrorMessages.REVIEWED_SUBTITLE_MUST_MAP_TO_CATALOGUE,
        ),
        (
            REVIEWED_FILENAME,
            REVIEWED_FILENAME,
            "b" * 64,
            CorpusIngestionErrorMessages.REVIEWED_SUBTITLE_HASH_MUST_MATCH_LEDGER,
        ),
    ],
)
def test_unmapped_or_hash_mismatched_record_fails_closed(
    tmp_path: Path,
    catalogue_filename: str | None,
    ledger_filename: str,
    hash_value: str,
    message: str,
) -> None:
    catalogue_path = tmp_path / "catalogue.json"
    ledger_path = tmp_path / "review-ledger.json"
    reviewed_directory = tmp_path / "reviewed"
    reviewed_directory.mkdir()
    (reviewed_directory / REVIEWED_FILENAME).write_text("content", encoding="utf-8")
    write_catalogue(catalogue_path, catalogue_filename)
    write_ledger(ledger_path, hash_value, ledger_filename)
    manifest = JsonCatalogueManifestLoader().load(catalogue_path).manifest

    with pytest.raises(InvalidModelError, match=message):
        ReviewedSubtitleLedgerLoader().load(
            manifest,
            ledger_path,
            reviewed_directory,
        )
