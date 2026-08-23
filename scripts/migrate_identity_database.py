"""Apply or intentionally reverse the checked-in identity schema migrations."""

import argparse
from pathlib import Path

from cinegraph.adapters.identity.migration_runner import (
    downgrade_identity_database,
    upgrade_identity_database,
)
from cinegraph.config import CinegraphRuntimeSettings


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("upgrade", "downgrade"))
    parser.add_argument("revision", nargs="?", default="-1")
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    args = parser.parse_args()
    settings = CinegraphRuntimeSettings(_env_file=args.env_file)
    if args.action == "upgrade":
        upgrade_identity_database(settings)
    else:
        downgrade_identity_database(settings, args.revision)


if __name__ == "__main__":
    main()
