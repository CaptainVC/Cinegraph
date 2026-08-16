from pathlib import Path

from openai import AuthenticationError, OpenAI

from cinegraph.common.error_messages import SpeakerReviewErrorMessages
from cinegraph.config import SpeakerReviewConfiguration
from cinegraph.ports.llm.speaker_review_batch_gateway import (
    BatchSnapshot,
    BatchSubmission,
)


class OpenAISpeakerReviewBatchGateway:
    def __init__(
        self,
        client: OpenAI,
        configuration: SpeakerReviewConfiguration,
    ) -> None:
        self._client = client
        self._configuration = configuration

    def submit(
        self,
        request_path: Path,
        completion_window: str,
        metadata: dict[str, str],
    ) -> BatchSubmission:
        try:
            with request_path.open("rb") as request_file:
                input_file = self._client.files.create(
                    file=request_file,
                    purpose="batch",
                )
            batch = self._client.batches.create(
                input_file_id=input_file.id,
                endpoint=self._configuration.batch_endpoint,
                completion_window=completion_window,
                metadata=metadata,
            )
        except AuthenticationError:
            raise RuntimeError(
                SpeakerReviewErrorMessages.OPENAI_AUTHENTICATION_FAILED
            ) from None
        return BatchSubmission(
            batch_id=batch.id,
            input_file_id=input_file.id,
            status=_enum_value(batch.status),
        )

    def retrieve(self, batch_id: str) -> BatchSnapshot:
        batch = self._client.batches.retrieve(batch_id)
        counts = batch.request_counts
        return BatchSnapshot(
            batch_id=batch.id,
            status=_enum_value(batch.status),
            output_file_id=batch.output_file_id,
            error_file_id=batch.error_file_id,
            total_requests=counts.total if counts is not None else 0,
            completed_requests=counts.completed if counts is not None else 0,
            failed_requests=counts.failed if counts is not None else 0,
        )

    def download_file(self, file_id: str) -> str:
        return self._client.files.content(file_id).text


def _enum_value(value: object) -> str:
    raw_value = getattr(value, "value", value)
    return str(raw_value)
