from __future__ import annotations

import argparse
from pathlib import Path

from cinegraph.ingestion.subtitle_alignment import annotate_subtitle_file


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Label raw SRT dialogue with speaker names from a script PDF."
    )
    parser.add_argument("--script-pdf", required=True, type=Path)
    parser.add_argument("--subtitle-directory", required=True, type=Path)
    parser.add_argument("--output-directory", required=True, type=Path)
    parser.add_argument("--report-directory", required=True, type=Path)
    parser.add_argument("--season", required=True, type=int)
    parser.add_argument("--minimum-score", default=92.0, type=float)
    parser.add_argument(
        "--fail-on-unresolved",
        action="store_true",
        help="Exit with an error when any subtitle dialogue line remains unresolved.",
    )
    arguments = parser.parse_args()

    subtitle_paths = sorted(arguments.subtitle_directory.glob(f"*{arguments.season}x*.en.srt"))
    if not subtitle_paths:
        raise ValueError("No raw .en.srt files matched the requested season.")

    review_count = 0
    for subtitle_path in subtitle_paths:
        output_path = arguments.output_directory / subtitle_path.name.replace(
            ".en.srt", ".script-aligned.srt"
        )
        report_path = arguments.report_directory / subtitle_path.name.replace(
            ".en.srt", ".alignment-report.json"
        )
        report = annotate_subtitle_file(
            source_pdf=arguments.script_pdf,
            source_subtitle=subtitle_path,
            output_subtitle=output_path,
            report_path=report_path,
            minimum_score=arguments.minimum_score,
        )
        review_count += len(report.unresolved_lines)
        print(
            f"{subtitle_path.name}: direct={report.labelled_lines} "
            f"fallback={report.fallback_labelled_lines} "
            f"review={len(report.unresolved_lines)}"
        )

    if arguments.fail_on_unresolved and review_count:
        raise SystemExit(f"{review_count} subtitle lines require review.")


if __name__ == "__main__":
    main()