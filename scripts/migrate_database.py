"""Apply or reverse the checked-in relational schema migrations."""

import argparse
from pathlib import Path

from cinegraph.adapters.persistence.migration_runner import downgrade_database, upgrade_database
from cinegraph.config import CinegraphRuntimeSettings


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("upgrade", "downgrade"))
    parser.add_argument("revision", nargs="?", default="-1")
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    args = parser.parse_args()
    settings = CinegraphRuntimeSettings(_env_file=args.env_file)
    if args.action == "upgrade":
        upgrade_database(settings)
    else:
        downgrade_database(settings, args.revision)


if __name__ == "__main__":
    main()
