"""Explicitly provision and verify the configured Qdrant transcript collection."""

import argparse
import os
import time
from dataclasses import replace
from pathlib import Path
from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from qdrant_client import QdrantClient

from cinegraph.adapters.qdrant.qdrant_collection_provisioner import (
    QdrantTranscriptCollectionProvisioner,
)
from cinegraph.config import DEFAULT_QDRANT_TRANSCRIPT_COLLECTION_SCHEMA

QDRANT_READY_RETRIES = 30
QDRANT_READY_DELAY_SECONDS = 2


def _wait_for_qdrant(url: str, api_key: str | None) -> None:
    if urlsplit(url).scheme not in {"http", "https"}:
        raise ValueError("Qdrant URL must use HTTP(S)")
    ready_url = f"{url.rstrip('/')}/readyz"
    headers = {"api-key": api_key} if api_key else {}
    for attempt in range(QDRANT_READY_RETRIES):
        try:
            # URL scheme is checked above; Qdrant is an operator-configured HTTP endpoint.
            with urlopen(Request(ready_url, headers=headers), timeout=5) as response:  # nosec B310
                if 200 <= response.status < 300:
                    return
        except (OSError, URLError):
            pass
        if attempt < QDRANT_READY_RETRIES - 1:
            time.sleep(QDRANT_READY_DELAY_SECONDS)
    raise RuntimeError("Qdrant did not become ready before the provisioning deadline")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    args = parser.parse_args()
    if args.env_file.is_file():
        from scripts.validate_vps_runtime import parse_env_file

        for key, value in parse_env_file(args.env_file).items():
            os.environ.setdefault(key, value)
    url = os.environ["CINEGRAPH_QDRANT_URL"]
    api_key = os.environ.get("CINEGRAPH_QDRANT_API_KEY") or None
    collection_name = os.environ["CINEGRAPH_QDRANT_COLLECTION_NAME"]
    schema = replace(DEFAULT_QDRANT_TRANSCRIPT_COLLECTION_SCHEMA, collection_name=collection_name)
    _wait_for_qdrant(url, api_key)
    client = QdrantClient(url=url, api_key=api_key)
    try:
        result = QdrantTranscriptCollectionProvisioner(client, schema).provision()
        print(
            f"Qdrant collection ready: {result.collection_name}; "
            f"created={result.collection_created}; indexes={len(result.payload_indexes_created)}"
        )
    finally:
        client.close()


if __name__ == "__main__":
    main()
