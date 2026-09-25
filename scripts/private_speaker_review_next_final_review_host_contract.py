"""Centralized non-secret host policy for final-review part two."""

# Re-export the inherited host policy as one centralized contract.
# ruff: noqa: F401, I001

from __future__ import annotations

from pathlib import Path
from typing import Final

from scripts.private_speaker_review_final_review_host_contract import (
    BOOTSTRAP_COMMANDS,
    CINEGRAPH_IMAGE_REVISION_LABEL,
    CINEGRAPH_IMAGE_SOURCE,
    CINEGRAPH_IMAGE_SOURCE_LABEL,
    CINEGRAPH_IMAGE_VERSION_LABEL,
    CLIENT_TIMEOUT_MARGIN_SECONDS,
    CURRENT_LINK,
    DEPLOY_ROOT,
    DEPLOYMENT_LOCK,
    ENV_FILE,
    GID_IN_CONTAINER,
    RELEASES_ROOT,
    REPOSITORY_URL,
    REVIEW_AUTHORIZATION_ROOT,
    REVIEW_AUTHORIZED_KEYS,
    REVIEW_DISPATCH_PATH,
    REVIEW_GROUP,
    REVIEW_HOME,
    REVIEW_PASSWORD_FIELD,
    REVIEW_SHELL,
    REVIEW_SUBMISSION_RECEIPTS_ROOT,
    REVIEW_UID,
    REVIEW_USER,
    SHARED_ROOT,
    SPEAKER_REVIEW_LOCK,
    SPEAKER_REVIEW_ROOT,
    SPEAKER_REVIEW_RUNS_ROOT,
    TRANSFER_LOCK,
    UID_IN_CONTAINER,
    authorized_key_entry,
)
from scripts.private_speaker_review_adjudication_result_processing_host_contract import (
    REVIEW_ADJUDICATION_RESULT_PROCESSING_RECEIPTS_ROOT as REVIEW_PHASE78_RECEIPTS_ROOT,
)
from scripts.private_speaker_review_final_review_host_contract import (
    REVIEW_FINAL_REVIEW_RECEIPTS_ROOT as REVIEW_PHASE79_RECEIPTS_ROOT,
)
from scripts.private_speaker_review_final_review_observation_host_contract import (
    REVIEW_FINAL_REVIEW_OBSERVATION_RECEIPTS_ROOT as REVIEW_PHASE80_RECEIPTS_ROOT,
    SUDOERS_CONTENT as PHASE80_SUDOERS_CONTENT,
)
from scripts.private_speaker_review_next_final_review_submission_contract import COMMAND

REVIEW_NEXT_FINAL_REVIEW_COMMAND: Final = COMMAND
REVIEW_NEXT_FINAL_REVIEW_HELPER_PATH: Final = Path(
    "/usr/local/sbin/cinegraph-submit-next-final-private-speaker-review"
)
REVIEW_NEXT_FINAL_REVIEW_RECEIPTS_ROOT: Final = SPEAKER_REVIEW_ROOT / "next-final-review-submission-receipts"
REVIEW_NEXT_FINAL_REVIEW_COMPOSE_PROFILE: Final = "corpus-speaker-review-submit-next-final-review"
REVIEW_NEXT_FINAL_REVIEW_COMPOSE_SERVICE: Final = REVIEW_NEXT_FINAL_REVIEW_COMPOSE_PROFILE
REVIEW_NEXT_FINAL_REVIEW_COMPOSE_PROJECT: Final = "cinegraph-dev"
REVIEW_NEXT_FINAL_REVIEW_CONTAINER_NAME: Final = "cinegraph-speaker-review-submit-next-final-review"
REVIEW_NEXT_FINAL_REVIEW_CONTAINER_WORKDIR: Final = "/app"
REVIEW_NEXT_FINAL_REVIEW_CONTAINER_COMMAND: Final = (
    "python", "scripts/submit_next_final_private_speaker_review_workspace.py"
)
REVIEW_NEXT_FINAL_REVIEW_NETWORK: Final = f"{REVIEW_NEXT_FINAL_REVIEW_COMPOSE_PROJECT}_egress"
REVIEW_NEXT_FINAL_REVIEW_TIMEOUT_SECONDS: Final = 1800
REVIEW_NEXT_FINAL_REVIEW_KILL_AFTER_SECONDS: Final = 10
REVIEW_NEXT_FINAL_REVIEW_MEMORY_BYTES: Final = 1024 * 1024 * 1024
REVIEW_NEXT_FINAL_REVIEW_NANO_CPUS: Final = 1_000_000_000
REVIEW_NEXT_FINAL_REVIEW_WORKER_UID: Final = UID_IN_CONTAINER
REVIEW_NEXT_FINAL_REVIEW_WORKER_GID: Final = GID_IN_CONTAINER
REVIEW_NEXT_FINAL_REVIEW_RUNS_MOUNT: Final = Path("/review-workspace/review-runs")
REVIEW_NEXT_FINAL_REVIEW_RUNS_TARGET: Final = REVIEW_NEXT_FINAL_REVIEW_RUNS_MOUNT
REVIEW_NEXT_FINAL_REVIEW_SECRET_TARGET: Final = "/run/secrets/openai_api_key"
REVIEW_NEXT_FINAL_REVIEW_TMP_TARGET: Final = "/tmp"  # nosec B108

SUDOERS_CONTENT: Final = (
    PHASE80_SUDOERS_CONTENT
    + f'{REVIEW_USER} ALL=(root) NOPASSWD: {REVIEW_NEXT_FINAL_REVIEW_HELPER_PATH.as_posix()} ""\n'
)

__all__ = [name for name in globals() if name.isupper() or name == "authorized_key_entry"]
