from __future__ import annotations

import argparse
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path


CANONICAL_FILENAMES = (
    "Modern Family - 1x01 - Pilot.HDTV.2HD.en.srt",
    "Modern Family - 1x02 - The Bicycle Thief.HDTV.2HD.en.srt",
    "Modern Family - 1x03 - Come Fly With Me.HDTV.2HD.en.srt",
    "Modern Family - 1x04 - The Incident.HDTV.2HD.en.srt",
    "Modern Family - 1x05 - Coal Digger.HDTV.2HD.en.srt",
    "Modern Family - 1x06 - Run for Your Wife.HDTV.2HD.en.srt",
    "Modern Family - 1x07 - En Garde.HDTV.2HD.en.srt",
    "Modern Family - 1x08 - Great Expectations.HDTV.POW4.en.srt",
    "Modern Family - 1x09 - Fizbo.HDTV.P0W4.en.srt",
    "Modern Family - 1x10 - Undeck the Halls.HDTV.2HD.en.srt",
    "Modern Family - 1x11 - Up All Night.HDTV.en.srt",
    "Modern Family - 1x12 - Not in My House.HDTV.en.srt",
    "Modern Family - 1x13 - Fifteen Percent.HDTV.POW4.en.srt",
    "Modern Family - 1x14 - Moon Landing.HDTV.FQM.en.srt",
    "Modern Family - 1x15 - My Funky Valentine.DVDRip.CLERKS.en.srt",
    "Modern Family - 1x16 - Fears.HDTV.PropER-NoTV.en.srt",
    "Modern Family - 1x17 - Truth Be Told.HDTV.FQM.en.srt",
    "Modern Family - 1x18 - Starry Night.HDTV.NoTV.en.srt",
    "Modern Family - 1x19 - Game Changer.720p HDTV.DIMENSION.en.srt",
    "Modern Family - 1x20 - Benched.HDTV.LOL.en.srt",
    "Modern Family - 1x21 - Travels With Scout.HDTV.FQM.en.srt",
    "Modern Family - 1x22 - Airport 2010.HDTV.LOL.en.srt",
    "Modern Family - 1x23 - Hawaii.HDTV.LOL.en.srt",
    "Modern Family - 1x24 - Family Portrait.HDTV.LOL.en.srt",
)


@dataclass(frozen=True)
class SubtitlePlan:
    keep: tuple[Path, ...]
    archive: tuple[Path, ...]


def build_plan(subtitle_directory: Path) -> SubtitlePlan:
    keep = tuple(subtitle_directory / filename for filename in CANONICAL_FILENAMES)
    missing = [path.name for path in keep if not path.is_file()]
    if missing:
        missing_files = "\n".join(missing)
        raise ValueError(f"Missing canonical subtitle files:\n{missing_files}")

    all_subtitles = tuple(sorted(subtitle_directory.glob("*.en.srt")))
    if len(keep) != 24:
        raise ValueError("The canonical manifest must contain exactly 24 episodes.")

    keep_set = set(keep)
    archive = tuple(path for path in all_subtitles if path not in keep_set)
    return SubtitlePlan(keep=keep, archive=archive)


def archive_duplicates(plan: SubtitlePlan, archive_directory: Path) -> None:
    if archive_directory.exists():
        raise ValueError(f"Archive directory already exists: {archive_directory}")

    archive_directory.mkdir(parents=True)
    for source_path in plan.archive:
        shutil.move(source_path, archive_directory / source_path.name)


def print_plan(plan: SubtitlePlan) -> None:
    print(f"Keep ({len(plan.keep)}):")
    for path in plan.keep:
        print(f"  {path.name}")

    print(f"Archive ({len(plan.archive)}):")
    for path in plan.archive:
        print(f"  {path.name}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Select one canonical Modern Family Season 1 subtitle per episode."
    )
    parser.add_argument("subtitle_directory", type=Path)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Move duplicate files into a timestamped archive after validation.",
    )
    arguments = parser.parse_args()

    plan = build_plan(arguments.subtitle_directory)
    print_plan(plan)

    if not arguments.apply:
        print("Dry run only. Re-run with --apply to archive duplicates.")
        return

    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    archive_directory = arguments.subtitle_directory / "archive" / timestamp
    archive_duplicates(plan, archive_directory)
    print(f"Archived duplicates in {archive_directory}")


if __name__ == "__main__":
    main()