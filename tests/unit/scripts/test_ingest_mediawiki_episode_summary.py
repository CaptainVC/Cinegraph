import subprocess
import sys
from pathlib import Path
from uuid import UUID

import pytest
from scripts.ingest_mediawiki_episode_summary import build_parser, parse_uuid


def test_parse_uuid_rejects_invalid_value() -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args(
            [
                "--series-id", "not-a-uuid",
                "--season-id", "00000000-0000-0000-0000-000000000101",
                "--episode-id", "00000000-0000-0000-0000-000000001001",
                "--season-number", "1",
                "--episode-number", "4",
                "--page-title", "Modern Family (season 1)",
                "--rights-status", "allowed",
                "--user-agent", "CineGraph/0.1 (test@example.com)",
            ]
        )


def test_parser_builds_typed_mediawiki_ingestion_arguments() -> None:
    arguments = build_parser().parse_args(
        [
            "--series-id", "00000000-0000-0000-0000-000000000011",
            "--season-id", "00000000-0000-0000-0000-000000000101",
            "--episode-id", "00000000-0000-0000-0000-000000001004",
            "--season-number", "1",
            "--episode-number", "4",
            "--page-title", "Modern Family (season 1)",
            "--rights-status", "allowed",
            "--user-agent", "CineGraph/0.1 (test@example.com)",
        ]
    )

    assert arguments.series_id == UUID("00000000-0000-0000-0000-000000000011")
    assert arguments.season_number == 1
    assert arguments.episode_number == 4
    assert arguments.rights_status == "allowed"


def test_parse_uuid_returns_uuid() -> None:
    assert parse_uuid("00000000-0000-0000-0000-000000000011") == UUID(
        "00000000-0000-0000-0000-000000000011"
    )


def test_script_executes_main_when_run_directly() -> None:
    script_path = Path("scripts/ingest_mediawiki_episode_summary.py")

    result = subprocess.run(
        [sys.executable, str(script_path), "--help"],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert "Ingest one MediaWiki episode summary" in result.stdout
