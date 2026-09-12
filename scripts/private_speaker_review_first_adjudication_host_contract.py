"""Non-secret policy for the first adjudication submission boundary."""

# This policy deliberately re-exports the complete Phase 68 shared surface.
# ruff: noqa: F403,F405

from __future__ import annotations

from pathlib import Path
from typing import Final

from scripts.private_speaker_review_primary_result_processing_host_contract import *  # noqa: F403
from scripts.private_speaker_review_primary_result_processing_host_contract import (
    SUDOERS_CONTENT as PHASE68_SUDOERS_CONTENT,
)

REVIEW_FIRST_ADJUDICATION_COMMAND: Final = "speaker-review-submit-first-adjudication-v1"
REVIEW_FIRST_ADJUDICATION_HELPER_PATH: Final = Path(
    "/usr/local/sbin/cinegraph-submit-first-private-speaker-review-adjudication"
)
REVIEW_FIRST_ADJUDICATION_RECEIPTS_ROOT: Final = (
    SPEAKER_REVIEW_ROOT / "first-adjudication-submission-receipts"
)
REVIEW_FIRST_ADJUDICATION_COMPOSE_PROFILE: Final = (
    "corpus-speaker-review-submit-first-adjudication"
)
REVIEW_FIRST_ADJUDICATION_COMPOSE_SERVICE: Final = REVIEW_FIRST_ADJUDICATION_COMPOSE_PROFILE
REVIEW_FIRST_ADJUDICATION_COMPOSE_PROJECT: Final = "cinegraph-dev"
REVIEW_FIRST_ADJUDICATION_CONTAINER_NAME: Final = (
    "cinegraph-speaker-review-submit-first-adjudication"
)
REVIEW_FIRST_ADJUDICATION_CONTAINER_WORKDIR: Final = "/app"
REVIEW_FIRST_ADJUDICATION_CONTAINER_COMMAND: Final = (
    "python",
    "scripts/submit_first_private_speaker_review_adjudication_workspace.py",
)
REVIEW_FIRST_ADJUDICATION_NETWORK: Final = f"{REVIEW_FIRST_ADJUDICATION_COMPOSE_PROJECT}_egress"
REVIEW_FIRST_ADJUDICATION_TIMEOUT_SECONDS: Final = 1800
REVIEW_FIRST_ADJUDICATION_KILL_AFTER_SECONDS: Final = 10
REVIEW_FIRST_ADJUDICATION_WORKER_UID: Final = UID_IN_CONTAINER
REVIEW_FIRST_ADJUDICATION_WORKER_GID: Final = GID_IN_CONTAINER
REVIEW_FIRST_ADJUDICATION_RUNS_MOUNT: Final = Path("/review-workspace/review-runs")
REVIEW_FIRST_ADJUDICATION_RUNS_TARGET: Final = REVIEW_FIRST_ADJUDICATION_RUNS_MOUNT

# Phase 69 adds exactly one no-argument root helper to the Phase 68 policy.
SUDOERS_CONTENT: Final = (  # type: ignore[misc]
    PHASE68_SUDOERS_CONTENT
    + f'{REVIEW_USER} ALL=(root) NOPASSWD: {REVIEW_FIRST_ADJUDICATION_HELPER_PATH.as_posix()} ""\n'
)

__all__ = [name for name in globals() if name.isupper()] + ["authorized_key_entry"]
