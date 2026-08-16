from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path

from cinegraph.common.error_messages import SpeakerReviewErrorMessages
from cinegraph.config import DEFAULT_SECRET_PROVISIONING_CONFIGURATION


CONFIGURATION = DEFAULT_SECRET_PROVISIONING_CONFIGURATION
KEY_VALUE_PATTERN = re.compile(
    CONFIGURATION.key_value_pattern
)


def extract_openai_key(source_path: Path) -> str:
    values: list[str] = []
    for line in source_path.read_text(encoding="utf-8-sig").splitlines():
        match = KEY_VALUE_PATTERN.fullmatch(line.strip())
        if match is None or match.group("name") != CONFIGURATION.openai_key_name:
            continue
        value = match.group("value").strip().strip('"').strip("'")
        if value:
            values.append(value)
    if not values:
        raise ValueError(SpeakerReviewErrorMessages.OPENAI_KEY_NOT_FOUND)
    if len(values) != 1:
        raise ValueError(SpeakerReviewErrorMessages.OPENAI_KEY_DUPLICATED)
    return values[0]


def provision_openai_environment(
    *,
    source_path: Path,
    destination_path: Path,
    delete_source: bool,
) -> None:
    openai_key = extract_openai_key(source_path)
    retained_lines: list[str] = []
    if destination_path.exists():
        for line in destination_path.read_text(encoding="utf-8").splitlines():
            match = KEY_VALUE_PATTERN.fullmatch(line.strip())
            if (
                match is not None
                and match.group("name") in CONFIGURATION.excluded_key_names
            ):
                continue
            retained_lines.append(line)

    destination_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = destination_path.with_suffix(destination_path.suffix + ".tmp")
    descriptor = os.open(
        temporary_path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
        CONFIGURATION.private_file_mode,
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as destination:
            destination.write(
                f"{CONFIGURATION.openai_key_name}={openai_key}\n"
            )
            if retained_lines:
                destination.write("\n".join(retained_lines).rstrip() + "\n")
        os.replace(temporary_path, destination_path)
        os.chmod(destination_path, CONFIGURATION.private_file_mode)
    except BaseException:
        if temporary_path.exists():
            temporary_path.unlink()
        raise

    if os.name != "nt" and destination_path.stat().st_mode & 0o077:
        raise PermissionError(
            SpeakerReviewErrorMessages.SECRET_DESTINATION_NOT_PRIVATE
        )
    if delete_source:
        source_path.unlink()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Provision only OPENAI_API_KEY into a private environment file."
    )
    parser.add_argument("source_path", type=Path)
    parser.add_argument("destination_path", type=Path)
    parser.add_argument("--delete-source", action="store_true")
    arguments = parser.parse_args()
    provision_openai_environment(
        source_path=arguments.source_path,
        destination_path=arguments.destination_path,
        delete_source=arguments.delete_source,
    )
    print(
        json.dumps(
            {
                "destination": str(arguments.destination_path),
                "openai_key_provisioned": True,
                "moonshot_key_copied": False,
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
