import json
from pathlib import Path

import httpx
import pytest
from openai import APIConnectionError, InternalServerError, OpenAI

from cinegraph.adapters.llm.openai_speaker_review_batch_gateway import (
    OpenAISpeakerReviewBatchGateway,
)
from cinegraph.config import DEFAULT_SPEAKER_REVIEW_CONFIGURATION
from cinegraph.config.speaker_review_transport import (
    SPEAKER_REVIEW_OBSERVATION_MAX_RETRIES,
    SPEAKER_REVIEW_OBSERVATION_TIMEOUT_SECONDS,
    SPEAKER_REVIEW_SUBMISSION_TIMEOUT_SECONDS,
)


@pytest.mark.parametrize("failed_path", ["/v1/files", "/v1/batches"])
@pytest.mark.parametrize("failure", ["timeout", "server"])
def test_submission_does_not_retry_ambiguous_creation(
    tmp_path: Path, failed_path: str, failure: str
) -> None:
    calls: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        assert request.extensions["timeout"]["read"] == (SPEAKER_REVIEW_SUBMISSION_TIMEOUT_SECONDS)
        if request.url.path == failed_path:
            if failure == "timeout":
                raise httpx.ReadTimeout("synthetic timeout", request=request)
            return httpx.Response(500, json={"error": {"message": "synthetic"}})
        return httpx.Response(
            200,
            json={
                "id": "file-synthetic",
                "object": "file",
                "bytes": 3,
                "created_at": 0,
                "filename": "synthetic.jsonl",
                "purpose": "batch",
                "status": "processed",
            },
        )

    request_filename = "requests.jsonl"
    request_bytes = b"{}\n"
    with OpenAI(
        api_key="synthetic-key",
        max_retries=3,
        http_client=httpx.Client(transport=httpx.MockTransport(handle)),
    ) as client:
        gateway = OpenAISpeakerReviewBatchGateway(client, DEFAULT_SPEAKER_REVIEW_CONFIGURATION)
        error_type = APIConnectionError if failure == "timeout" else InternalServerError
        with pytest.raises(error_type):
            gateway.submit(request_filename, request_bytes, "24h", {"stage": "synthetic"})
        assert client.max_retries == 3

    assert calls.count(failed_path) == 1
    assert calls == (["/v1/files"] if failed_path == "/v1/files" else ["/v1/files", "/v1/batches"])


def test_successful_submission_preserves_request_and_batch_identity(tmp_path: Path) -> None:
    calls: list[str] = []
    metadata = {"cinegraph_run_id": "synthetic-run", "part": "1"}

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path == "/v1/files":
            assert b'{"custom_id":"synthetic"}' in request.content
            assert b'filename="requests.jsonl"' in request.content
            return httpx.Response(
                200,
                json={
                    "id": "file-synthetic",
                    "object": "file",
                    "bytes": 26,
                    "created_at": 0,
                    "filename": "requests.jsonl",
                    "purpose": "batch",
                },
            )
        assert json.loads(request.content) == {
            "input_file_id": "file-synthetic",
            "endpoint": DEFAULT_SPEAKER_REVIEW_CONFIGURATION.batch_endpoint,
            "completion_window": "24h",
            "metadata": metadata,
        }
        return httpx.Response(
            200,
            json={"id": "batch-synthetic", "object": "batch", "status": "validating"},
        )

    request_filename = "requests.jsonl"
    request_bytes = b'{"custom_id":"synthetic"}\n'
    with OpenAI(
        api_key="synthetic-key",
        http_client=httpx.Client(transport=httpx.MockTransport(handle)),
    ) as client:
        gateway = OpenAISpeakerReviewBatchGateway(client, DEFAULT_SPEAKER_REVIEW_CONFIGURATION)
        result = gateway.submit(request_filename, request_bytes, "24h", metadata)

    assert (result.batch_id, result.input_file_id, result.status) == (
        "batch-synthetic",
        "file-synthetic",
        "validating",
    )
    assert calls == ["/v1/files", "/v1/batches"]


@pytest.mark.parametrize("filename", ["", ".", "..", "nested/requests.jsonl", "nested\\requests.jsonl", "requests\n.jsonl"])
def test_submission_rejects_unsafe_filename(filename: str) -> None:
    gateway = OpenAISpeakerReviewBatchGateway(object(), DEFAULT_SPEAKER_REVIEW_CONFIGURATION)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="safe basename"):
        gateway.submit(filename, b"{}\n", "24h", {})


def test_submission_rejects_unbounded_or_empty_bytes() -> None:
    from cinegraph.config.speaker_review_submission import SUBMISSION_REQUEST_MAX_BYTES

    gateway = OpenAISpeakerReviewBatchGateway(object(), DEFAULT_SPEAKER_REVIEW_CONFIGURATION)  # type: ignore[arg-type]
    for content in (b"", b"x" * (SUBMISSION_REQUEST_MAX_BYTES + 1)):
        with pytest.raises(ValueError, match="configured limit"):
            gateway.submit("requests.jsonl", content, "24h", {})


class _ObservationClient:
    def __init__(self) -> None:
        self.options: list[dict[str, object]] = []
        self.retrieve_calls: list[str] = []
        self.content_calls: list[str] = []
        self.batches = self
        self.files = self
        self.request_counts = type("Counts", (), {"total": 3, "completed": 2, "failed": 1})()
        self.id = "batch-observed"
        self.status = "completed"
        self.output_file_id = "output-file"
        self.error_file_id = "error-file"
        self.text = "{}\n"

    def with_options(self, **options: object) -> "_ObservationClient":
        self.options.append(options)
        return self

    def retrieve(self, batch_id: str) -> "_ObservationClient":
        self.retrieve_calls.append(batch_id)
        return self

    def content(self, file_id: str) -> "_ObservationClient":
        self.content_calls.append(file_id)
        return self


def test_retrieve_uses_one_no_retry_bounded_observation_client_call() -> None:
    client = _ObservationClient()
    gateway = OpenAISpeakerReviewBatchGateway(client, DEFAULT_SPEAKER_REVIEW_CONFIGURATION)  # type: ignore[arg-type]

    result = gateway.retrieve("batch-observed")

    assert client.options == [
        {
            "max_retries": SPEAKER_REVIEW_OBSERVATION_MAX_RETRIES,
            "timeout": SPEAKER_REVIEW_OBSERVATION_TIMEOUT_SECONDS,
        }
    ]
    assert client.retrieve_calls == ["batch-observed"]
    assert result.batch_id == "batch-observed"
    assert (result.total_requests, result.completed_requests, result.failed_requests) == (3, 2, 1)


def test_download_file_uses_one_no_retry_bounded_observation_client_call() -> None:
    client = _ObservationClient()
    gateway = OpenAISpeakerReviewBatchGateway(client, DEFAULT_SPEAKER_REVIEW_CONFIGURATION)  # type: ignore[arg-type]

    assert gateway.download_file("output-file") == "{}\n"
    assert client.options == [
        {
            "max_retries": SPEAKER_REVIEW_OBSERVATION_MAX_RETRIES,
            "timeout": SPEAKER_REVIEW_OBSERVATION_TIMEOUT_SECONDS,
        }
    ]
    assert client.content_calls == ["output-file"]
