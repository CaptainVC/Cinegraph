import os
from pathlib import Path

from scripts.provision_openai_env import provision_openai_environment


def test_provisions_only_openai_key_and_removes_staging_source(tmp_path: Path) -> None:
    source = tmp_path / "key.txt"
    destination = tmp_path / ".env"
    source.write_text(
        "OPENAI_API_KEY=openai-secret\nMOONSHOT_API_KEY=moonshot-secret\n",
        encoding="utf-8",
    )
    destination.write_text("MAIN_MODEL=gpt-5.6-terra\n", encoding="utf-8")

    provision_openai_environment(
        source_path=source,
        destination_path=destination,
        delete_source=True,
    )

    content = destination.read_text(encoding="utf-8")
    assert "OPENAI_API_KEY=openai-secret" in content
    assert "MAIN_MODEL=gpt-5.6-terra" in content
    assert "moonshot" not in content.casefold()
    assert not source.exists()
    if os.name != "nt":
        assert destination.stat().st_mode & 0o077 == 0
