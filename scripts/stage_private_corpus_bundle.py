"""Verify a hostile private-corpus bundle, then publish it to private staging."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from cinegraph.common.private_corpus_bundle import BundleError, stage_bundle


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", required=True, type=Path)
    parser.add_argument("--destination", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        result = stage_bundle(archive_path=args.bundle, destination=args.destination)
    except BundleError as error:
        print(f"error={error}", file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "purpose": result.purpose,
                "file_count": result.file_count,
                "total_bytes": result.total_bytes,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
