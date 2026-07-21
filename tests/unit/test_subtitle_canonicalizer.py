from scripts.canonicalize_modern_family_subtitles import (
    CANONICAL_FILENAMES,
    archive_duplicates,
    build_plan,
)


def test_build_plan_requires_every_canonical_file(tmp_path) -> None:
    (tmp_path / CANONICAL_FILENAMES[0]).touch()

    try:
        build_plan(tmp_path)
    except ValueError as error:
        assert "Missing canonical subtitle files" in str(error)
    else:
        raise AssertionError("Expected the incomplete subtitle directory to be rejected.")


def test_archive_duplicates_preserves_canonical_files(tmp_path) -> None:
    for filename in CANONICAL_FILENAMES:
        (tmp_path / filename).touch()

    duplicate = tmp_path / "Modern Family - 1x01 - Pilot.DVDRip.CLERKS.en.srt"
    speaker_review = tmp_path / "Modern Family - 1x01 - Pilot.HDTV.2HD.speaker-review.srt"
    duplicate.touch()
    speaker_review.touch()

    plan = build_plan(tmp_path)
    archive_directory = tmp_path / "archive"
    archive_duplicates(plan, archive_directory)

    assert {path.name for path in tmp_path.glob("*.en.srt")} == set(CANONICAL_FILENAMES)
    assert speaker_review.is_file()
    assert (archive_directory / duplicate.name).is_file()