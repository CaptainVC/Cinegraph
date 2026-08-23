from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient
from tests.factories import DEFAULT_SERIES_ID
from tests.unit.adapters.api.test_fastapi_app import make_context

from cinegraph.adapters.api.fastapi_app import create_app
from cinegraph.adapters.api.guardrails import (
    ApiGuardrailServices,
    InMemoryTokenBucketRateLimiter,
)
from cinegraph.application.models.audit import HttpAuditEvent
from cinegraph.config import DEFAULT_API_CONFIGURATION


class MutableMonotonicClock:
    def __init__(self) -> None:
        self.value = 100.0

    def __call__(self) -> float:
        return self.value


class RecordingAuditSink:
    def __init__(self) -> None:
        self.events: list[HttpAuditEvent] = []

    def emit(self, event: HttpAuditEvent) -> None:
        self.events.append(event)


def make_guardrails(configuration=DEFAULT_API_CONFIGURATION):
    monotonic = MutableMonotonicClock()
    audit = RecordingAuditSink()
    return (
        ApiGuardrailServices(
            rate_limiter=InMemoryTokenBucketRateLimiter(
                configuration,
                clock=monotonic,
            ),
            audit_sink=audit,
            request_id_generator=lambda: "generated-request-id",
            monotonic_clock=monotonic,
            utc_clock=lambda: datetime(2026, 8, 16, 12, 0, tzinfo=UTC),
        ),
        monotonic,
        audit,
    )


def test_token_bucket_refills_and_reports_retry_after() -> None:
    configuration = replace(
        DEFAULT_API_CONFIGURATION,
        rate_limit_capacity=10,
        rate_limit_refill_per_second=2.0,
        authentication_request_cost=5,
        chat_request_cost=10,
    )
    services, clock, _ = make_guardrails(configuration)

    first = services.rate_limiter.check("client", 10)
    rejected = services.rate_limiter.check("client", 5)
    clock.value += 2.5
    accepted = services.rate_limiter.check("client", 5)

    assert first.allowed is True
    assert rejected.allowed is False
    assert rejected.retry_after_seconds == 3
    assert accepted.allowed is True
    assert accepted.remaining == 0


def test_request_id_security_headers_and_audit_metadata(tmp_path: Path) -> None:
    context, _ = make_context(tmp_path)
    services, _, audit = make_guardrails()
    with TestClient(create_app(context, services)) as client:
        client.post("/api/v1/auth/guest")
        response = client.get(
            "/api/v1/catalogue",
            headers={"X-Request-ID": "browser-request-123"},
        )

    assert response.headers["X-Request-ID"] == "browser-request-123"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["Cache-Control"] == "no-store"
    event = audit.events[-1]
    assert event.request_id == "browser-request-123"
    assert event.path == "/api/v1/catalogue"
    assert event.principal_kind == "guest"
    assert not hasattr(event, "request_body")


def test_invalid_request_is_sanitized_and_does_not_echo_password(
    tmp_path: Path,
) -> None:
    context, _ = make_context(tmp_path)
    with TestClient(create_app(context)) as client:
        response = client.post(
            "/api/v1/auth/register",
            json={
                "email": "viewer@example.com",
                "password": "secret",
                "display_name": "Viewer",
            },
        )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_request"
    assert response.json()["error"]["fields"] == ["password"]
    assert "secret" not in response.text


def test_oversized_request_is_rejected_before_route_processing(tmp_path: Path) -> None:
    context, _ = make_context(tmp_path)
    configuration = replace(
        DEFAULT_API_CONFIGURATION,
        maximum_request_body_bytes=64,
    )
    services, _, audit = make_guardrails(configuration)
    with TestClient(
        create_app(context, services, api_configuration=configuration)
    ) as client:
        response = client.post(
            "/api/v1/auth/register",
            content=b"x" * 65,
            headers={"content-type": "application/json"},
        )

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "request_too_large"
    assert audit.events[-1].status_code == 413


def test_expensive_chat_route_is_rate_limited_per_client(tmp_path: Path) -> None:
    context, _ = make_context(tmp_path)
    configuration = replace(
        DEFAULT_API_CONFIGURATION,
        rate_limit_capacity=15,
        rate_limit_refill_per_second=0.0001,
        maximum_request_body_bytes=4_096,
    )
    services, _, audit = make_guardrails(configuration)
    payload = {
        "series_id": str(DEFAULT_SERIES_ID),
        "question": "Who introduces the family?",
    }
    with TestClient(
        create_app(context, services, api_configuration=configuration)
    ) as client:
        client.post("/api/v1/auth/guest")
        first = client.post("/api/v1/chat", json=payload)
        rejected = client.post("/api/v1/chat", json=payload)

    assert first.status_code == 200
    assert rejected.status_code == 429
    assert rejected.headers["Retry-After"]
    assert rejected.json()["error"]["request_id"] == "generated-request-id"
    assert audit.events[-1].status_code == 429


def test_unexpected_errors_do_not_expose_internal_details(tmp_path: Path) -> None:
    context, workflow = make_context(tmp_path)

    def fail(query):
        raise RuntimeError("private database path exploded")

    workflow.execute = fail
    with TestClient(
        create_app(context),
        raise_server_exceptions=False,
    ) as client:
        client.post("/api/v1/auth/guest")
        response = client.post(
            "/api/v1/chat",
            json={
                "series_id": str(DEFAULT_SERIES_ID),
                "question": "Who introduces the family?",
            },
        )

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "internal_error"
    assert "private database" not in response.text
