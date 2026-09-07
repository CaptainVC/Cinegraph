import unicodedata

from openai import AuthenticationError, OpenAI

from cinegraph.common.error_messages import SpeakerReviewErrorMessages
from cinegraph.config import SpeakerReviewConfiguration
from cinegraph.config.speaker_review_submission import SUBMISSION_REQUEST_MAX_BYTES
from cinegraph.config.speaker_review_transport import (
    SPEAKER_REVIEW_SUBMISSION_MAX_RETRIES,
    SPEAKER_REVIEW_SUBMISSION_TIMEOUT_SECONDS,
)
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
        request_filename: str,
        request_bytes: bytes,
        completion_window: str,
        metadata: dict[str, str],
    ) -> BatchSubmission:
        _validate_submission_payload(request_filename, request_bytes)
        # A timeout may happen after acceptance; never retry a creation implicitly.
        submission_client = self._client.with_options(
            max_retries=SPEAKER_REVIEW_SUBMISSION_MAX_RETRIES,
            timeout=SPEAKER_REVIEW_SUBMISSION_TIMEOUT_SECONDS,
        )
        try:
            input_file = submission_client.files.create(
                file=(request_filename, request_bytes, "application/jsonl"),
                purpose="batch",
            )
            batch = submission_client.batches.create(
                input_file_id=input_file.id,
                endpoint=self._configuration.batch_endpoint,
                completion_window=completion_window,
                metadata=metadata,
            )
        except AuthenticationError:
            raise RuntimeError(SpeakerReviewErrorMessages.OPENAI_AUTHENTICATION_FAILED) from None
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


def _validate_submission_payload(filename: str, content: bytes) -> None:
    if (
        not isinstance(filename, str)
        or not filename
        or filename != unicodedata.normalize("NFC", filename)
        or filename in {".", ".."}
        or "/" in filename
        or "\\" in filename
        or any(ord(character) < 32 or ord(character) == 127 for character in filename)
    ):
        raise ValueError(SpeakerReviewErrorMessages.BATCH_REQUEST_FILENAME_INVALID)
    if (
        not isinstance(content, bytes)
        or not content
        or len(content) > SUBMISSION_REQUEST_MAX_BYTES
    ):
        raise ValueError(SpeakerReviewErrorMessages.BATCH_REQUEST_CONTENT_INVALID)
