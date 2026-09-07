"""Prepare a Season 2 speaker-review run without provider access.

This worker is deliberately offline.  It is the safe first stage of the VPS
speaker-review flow: it validates the mounted private bundle, creates the
deterministic LangGraph run artifacts, and returns only a bounded aggregate.
Provider submission is a separate, explicitly enabled operation.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_RELEASE_ROOT = Path(__file__).resolve().parents[1]
for _root in (_RELEASE_ROOT, _RELEASE_ROOT / "src"):
    if os.fspath(_root) not in sys.path:
        sys.path.insert(0, os.fspath(_root))

from cinegraph.adapters.workflow.langgraph.speaker_review_graph import (  # noqa: E402
    SpeakerReviewGraphWorkflow,
)  # noqa: E402
from cinegraph.common.private_corpus_bundle import (  # noqa: E402
    MANIFEST_FILENAME,
    _decode_manifest,
)  # noqa: E402
from cinegraph.config import (  # noqa: E402
    DEFAULT_MODEL_CONFIGURATION,
    DEFAULT_PRIVATE_CORPUS_BUNDLE_CONFIGURATION,
    DEFAULT_SPEAKER_REVIEW_CONFIGURATION,
)
from cinegraph.domain.enums.enum import SpeakerReviewRunStatus  # noqa: E402
from cinegraph.ingestion.speaker_review.private_io import stable_file_snapshot  # noqa: E402
from cinegraph.ingestion.speaker_review.workflow import (  # noqa: E402
    SpeakerReviewWorkflow,
    load_validated_run_state,
)  # noqa: E402
from cinegraph.ports.llm.speaker_review_batch_gateway import (  # noqa: E402
    BatchSnapshot,
    BatchSubmission,
)  # noqa: E402
from scripts import private_speaker_review_contract as contract  # noqa: E402

PRIVATE_CORPUS_ROOT = Path("/private-corpus")
SEASON_NUMBER = contract.REVIEW_SEASON_NUMBER
OPERATION = "prepare"
PURPOSE = contract.REVIEW_PURPOSE
MANIFEST_MAX_BYTES = DEFAULT_PRIVATE_CORPUS_BUNDLE_CONFIGURATION.max_manifest_bytes


class ProviderAccessRejected(RuntimeError):
    """Raised if an offline preparation path attempts a provider action."""


class OfflineSpeakerReviewGateway:
    """Gateway proving that preparation cannot submit, retrieve, or download."""

    def submit(
        self,
        request_filename: str,
        request_bytes: bytes,
        completion_window: str,
        metadata: dict[str, str],
    ) -> BatchSubmission:
        del request_filename, request_bytes, completion_window, metadata
        raise ProviderAccessRejected("provider access is disabled during preparation")

    def retrieve(self, batch_id: str) -> BatchSnapshot:
        del batch_id
        raise ProviderAccessRejected("provider access is disabled during preparation")

    def download_file(self, file_id: str) -> str:
        del file_id
        raise ProviderAccessRejected("provider access is disabled during preparation")


def _workflow() -> SpeakerReviewGraphWorkflow:
    models = DEFAULT_MODEL_CONFIGURATION
    review_workflow = SpeakerReviewWorkflow(
        gateway=OfflineSpeakerReviewGateway(),
        configuration=DEFAULT_SPEAKER_REVIEW_CONFIGURATION,
        primary_model=models.speaker_review_model,
        adjudication_model=models.speaker_adjudication_model,
        final_review_model=models.speaker_final_review_model,
        primary_reasoning_effort=models.speaker_review_reasoning_effort,
        adjudication_reasoning_effort=models.speaker_adjudication_reasoning_effort,
        final_review_reasoning_effort=models.speaker_final_review_reasoning_effort,
    )
    return SpeakerReviewGraphWorkflow(review_workflow)


def _bundle_aggregate(corpus_root: Path) -> tuple[int, int]:
    """Read only the already-validated public aggregate from the bundle manifest."""

    raw = stable_file_snapshot(
        corpus_root / MANIFEST_FILENAME,
        max_bytes=MANIFEST_MAX_BYTES,
    ).content
    payload = _decode_manifest(raw)
    if (
        payload.get("purpose") != PURPOSE
        or payload.get("season_number") != SEASON_NUMBER
        or type(payload.get("file_count")) is not int
        or type(payload.get("total_bytes")) is not int
        or payload["file_count"] <= 0
        or payload["total_bytes"] <= 0
    ):
        raise ValueError("bundle manifest is invalid")
    return payload["file_count"], payload["total_bytes"]


def prepare(corpus_root: Path = PRIVATE_CORPUS_ROOT) -> dict[str, object]:
    """Prepare exactly Season 2 and return a path/provider-free aggregate."""

    root = corpus_root.resolve(strict=True)
    file_count, total_bytes = _bundle_aggregate(root)
    run_directory, state = _workflow().prepare(
        corpus_root=root,
        seasons=(SEASON_NUMBER,),
    )
    validated_directory, validated_state = load_validated_run_state(
        run_directory,
        DEFAULT_SPEAKER_REVIEW_CONFIGURATION,
    )
    if (
        validated_directory != run_directory
        or validated_state.status is not SpeakerReviewRunStatus.PREPARED
    ):
        raise ValueError("speaker review preparation did not produce a prepared run")
    if state != validated_state:
        raise ValueError("speaker review state changed during validation")
    result = {
        "candidate_count": state.candidate_count,
        "estimated_primary_cost_usd": round(state.estimated_primary_cost_usd, 6),
        "file_count": file_count,
        "operation": OPERATION,
        "primary_part_count": state.primary_part_count,
        "purpose": PURPOSE,
        "run_id": state.run_id,
        "season_number": SEASON_NUMBER,
        "status": state.status.value,
        "total_bytes": total_bytes,
    }
    contract.validate_aggregate(result, operation=OPERATION, status=state.status.value)
    return result


def main() -> int:
    try:
        result = prepare()
        sys.stdout.buffer.write(contract.canonical_json(result))
        return 0
    except Exception:
        sys.stderr.write("error=speaker_review_preparation_failed\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
