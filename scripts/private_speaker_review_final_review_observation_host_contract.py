"""Centralized host policy for observing final-review part one."""

# ruff: noqa: F401

from __future__ import annotations

from pathlib import Path
from typing import Final

from scripts.private_speaker_review_adjudication_result_processing_host_contract import (
    REVIEW_ADJUDICATION_RESULT_PROCESSING_RECEIPTS_ROOT as REVIEW_PHASE78_RECEIPTS_ROOT,
)
from scripts.private_speaker_review_final_review_host_contract import (
    REVIEW_FINAL_REVIEW_RECEIPTS_ROOT as REVIEW_FINAL_REVIEW_SUBMISSION_RECEIPTS_ROOT,
)
from scripts.private_speaker_review_final_review_host_contract import (
    SUDOERS_CONTENT as PHASE79_SUDOERS_CONTENT,
)
from scripts.private_speaker_review_final_review_observation_contract import COMMAND
from scripts.private_speaker_review_observation_host_contract import (
    GID_IN_CONTAINER,
    REVIEW_OBSERVATION_TIMEOUT_SECONDS,
    UID_IN_CONTAINER,
)
from scripts.private_speaker_review_submission_host_contract import (
    CLIENT_TIMEOUT_MARGIN_SECONDS,
    REVIEW_USER,
    SPEAKER_REVIEW_ROOT,
)

REVIEW_FINAL_REVIEW_OBSERVATION_COMMAND: Final = COMMAND
REVIEW_FINAL_REVIEW_OBSERVATION_HELPER_PATH: Final = Path(
    "/usr/local/sbin/cinegraph-observe-final-private-speaker-review"
)
REVIEW_FINAL_REVIEW_OBSERVATION_RECEIPTS_ROOT: Final = (
    SPEAKER_REVIEW_ROOT / "final-review-observation-receipts"
)
REVIEW_FINAL_REVIEW_OBSERVATION_COMPOSE_PROFILE: Final = (
    "corpus-speaker-review-observe-final-review"
)
REVIEW_FINAL_REVIEW_OBSERVATION_COMPOSE_SERVICE: Final = (
    REVIEW_FINAL_REVIEW_OBSERVATION_COMPOSE_PROFILE
)
REVIEW_FINAL_REVIEW_OBSERVATION_COMPOSE_PROJECT: Final = "cinegraph-dev"
REVIEW_FINAL_REVIEW_OBSERVATION_CONTAINER_NAME: Final = (
    "cinegraph-speaker-review-observe-final-review"
)
REVIEW_FINAL_REVIEW_OBSERVATION_CONTAINER_WORKDIR: Final = "/app"
REVIEW_FINAL_REVIEW_OBSERVATION_CONTAINER_COMMAND: Final = (
    "python",
    "scripts/observe_final_private_speaker_review_workspace.py",
)
REVIEW_FINAL_REVIEW_OBSERVATION_NETWORK: Final = "cinegraph-dev_egress"
REVIEW_FINAL_REVIEW_OBSERVATION_TIMEOUT_SECONDS: Final = REVIEW_OBSERVATION_TIMEOUT_SECONDS
REVIEW_FINAL_REVIEW_OBSERVATION_KILL_AFTER_SECONDS: Final = 10
REVIEW_FINAL_REVIEW_OBSERVATION_WORKER_UID: Final = UID_IN_CONTAINER
REVIEW_FINAL_REVIEW_OBSERVATION_WORKER_GID: Final = GID_IN_CONTAINER
REVIEW_FINAL_REVIEW_OBSERVATION_RUNS_MOUNT: Final = Path("/review-workspace/review-runs")
REVIEW_FINAL_REVIEW_OBSERVATION_RUNS_TARGET: Final = REVIEW_FINAL_REVIEW_OBSERVATION_RUNS_MOUNT
REVIEW_FINAL_REVIEW_OBSERVATION_SECRET_TARGET: Final = "/run/secrets/openai_api_key"
REVIEW_FINAL_REVIEW_OBSERVATION_TMP_TARGET: Final = "/tmp"  # nosec B108
SUDOERS_CONTENT: Final = (
    PHASE79_SUDOERS_CONTENT + f"{REVIEW_USER} ALL=(root) NOPASSWD: "
    f'{REVIEW_FINAL_REVIEW_OBSERVATION_HELPER_PATH.as_posix()} ""\n'
)

__all__ = [name for name in globals() if name.isupper() or name == "authorized_key_entry"]
