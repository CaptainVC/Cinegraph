from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True, slots=True)
class BatchSubmission:
    batch_id: str
    input_file_id: str
    status: str


@dataclass(frozen=True, slots=True)
class BatchSnapshot:
    batch_id: str
    status: str
    output_file_id: str | None
    error_file_id: str | None
    total_requests: int
    completed_requests: int
    failed_requests: int


class SpeakerReviewBatchGateway(Protocol):
    def submit(
        self,
        request_path: Path,
        completion_window: str,
        metadata: dict[str, str],
    ) -> BatchSubmission: ...

    def retrieve(self, batch_id: str) -> BatchSnapshot: ...

    def download_file(self, file_id: str) -> str: ...
