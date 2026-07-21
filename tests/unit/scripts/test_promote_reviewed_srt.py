import json
from pathlib import Path

import pytest

from scripts.promote_reviewed_srt import promote_reviewed_srt_directory


def test_promotes_reviewed_labels_and_writes_hash_ledger(tmp_path: Path) -> None:
    candidate_directory = tmp_path / "script-aligned"
    reviewed_directory = tmp_path / "reviewed"
    candidate_directory.mkdir()
    (candidate_directory / "episode.script-aligned.srt").write_text(
        "1\n00:00:01,000 --> 00:00:02,000\n"
        "CLAIRE?: Hello there.\n"
        "***\n",
        encoding="utf-8",
    )

    records = promote_reviewed_srt_directory(
        candidate_directory=candidate_directory,
        reviewed_directory=reviewed_directory,
        reviewed_by="local-corpus-owner",
    )

    reviewed_path = reviewed_directory / "episode.reviewed.srt"
    assert reviewed_path.read_text(encoding="utf-8") == (
        "1\n00:00:01,000 --> 00:00:02,000\nCLAIRE: Hello there.\n"
    )
    assert records[0].promoted_question_mark_labels == 1
    assert records[0].removed_redaction_lines == 1

    ledger = json.loads(
        (reviewed_directory / "review-ledger.json").read_text(encoding="utf-8")
    )
    assert ledger["review_status"] == "reviewed"
    assert ledger["reviewed_by"] == "local-corpus-owner"
    assert ledger["records"][0]["candidate_sha256"]
    assert ledger["records"][0]["reviewed_sha256"]


def test_rejects_unrecognized_unlabeled_dialogue(tmp_path: Path) -> None:
    candidate_directory = tmp_path / "script-aligned"
    reviewed_directory = tmp_path / "reviewed"
    candidate_directory.mkdir()
    (candidate_directory / "episode.script-aligned.srt").write_text(
        "1\n00:00:01,000 --> 00:00:02,000\nUnlabeled dialogue.\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Unreviewed subtitle line"):
        promote_reviewed_srt_directory(
            candidate_directory=candidate_directory,
            reviewed_directory=reviewed_directory,
            reviewed_by="local-corpus-owner",
        )
