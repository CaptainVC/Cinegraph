import math
import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import JSONResponse, Response

from cinegraph.adapters.observability import JsonLoggingAuditSink
from cinegraph.application.models.audit import HttpAuditEvent
from cinegraph.config import (
    DEFAULT_API_CONFIGURATION,
    ApiConfiguration,
    RuntimeEnvironment,
)
from cinegraph.ports.observability import AuditSink

REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
BODY_METHODS = frozenset({"POST", "PUT", "PATCH"})


@dataclass(frozen=True, slots=True)
class RateLimitDecision:
    allowed: bool
    remaining: int
    retry_after_seconds: int


@dataclass(slots=True)
class _TokenBucket:
    tokens: float
    updated_at: float


class InMemoryTokenBucketRateLimiter:
    def __init__(
        self,
        configuration: ApiConfiguration = DEFAULT_API_CONFIGURATION,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._configuration = configuration
        self._clock = clock
        self._buckets: dict[str, _TokenBucket] = {}
        self._lock = threading.Lock()

    def check(self, key: str, cost: int) -> RateLimitDecision:
        if cost <= 0:
            return RateLimitDecision(
                allowed=True,
                remaining=self._configuration.rate_limit_capacity,
                retry_after_seconds=0,
            )
        now = self._clock()
        with self._lock:
            bucket = self._buckets.get(key)
            if bucket is None:
                self._make_capacity_for_new_bucket(now)
                bucket = _TokenBucket(
                    tokens=float(self._configuration.rate_limit_capacity),
                    updated_at=now,
                )
                self._buckets[key] = bucket
            elapsed = max(0.0, now - bucket.updated_at)
            bucket.tokens = min(
                float(self._configuration.rate_limit_capacity),
                bucket.tokens
                + elapsed * self._configuration.rate_limit_refill_per_second,
            )
            bucket.updated_at = now
            if bucket.tokens >= cost:
                bucket.tokens -= cost
                return RateLimitDecision(
                    allowed=True,
                    remaining=max(0, math.floor(bucket.tokens)),
                    retry_after_seconds=0,
                )
            missing = cost - bucket.tokens
            return RateLimitDecision(
                allowed=False,
                remaining=max(0, math.floor(bucket.tokens)),
                retry_after_seconds=max(
                    1,
                    math.ceil(
                        missing
                        / self._configuration.rate_limit_refill_per_second
                    ),
                ),
            )

    def _make_capacity_for_new_bucket(self, now: float) -> None:
        if len(self._buckets) < self._configuration.maximum_rate_limit_buckets:
            return
        idle_before = (
            now - self._configuration.rate_limit_bucket_idle_ttl_seconds
        )
        idle_keys = [
            key
            for key, bucket in self._buckets.items()
            if bucket.updated_at <= idle_before
        ]
        for key in idle_keys:
            del self._buckets[key]
        if len(self._buckets) >= self._configuration.maximum_rate_limit_buckets:
            oldest_key = min(
                self._buckets,
                key=lambda key: self._buckets[key].updated_at,
            )
            del self._buckets[oldest_key]


@dataclass(slots=True)
class ApiGuardrailServices:
    rate_limiter: InMemoryTokenBucketRateLimiter
    audit_sink: AuditSink
    request_id_generator: Callable[[], str] = lambda: str(uuid4())
    monotonic_clock: Callable[[], float] = time.monotonic
    utc_clock: Callable[[], datetime] = lambda: datetime.now(UTC)

    @classmethod
    def defaults(
        cls,
        configuration: ApiConfiguration = DEFAULT_API_CONFIGURATION,
    ) -> "ApiGuardrailServices":
        return cls(
            rate_limiter=InMemoryTokenBucketRateLimiter(configuration),
            audit_sink=JsonLoggingAuditSink(),
        )


def error_response(
    *,
    status_code: int,
    code: str,
    message: str,
    request_id: str,
    headers: dict[str, str] | None = None,
    fields: tuple[str, ...] = (),
) -> JSONResponse:
    error_payload: dict[str, object] = {
        "code": code,
        "message": message,
        "request_id": request_id,
    }
    if fields:
        error_payload["fields"] = fields
    return JSONResponse(
        status_code=status_code,
        content={"error": error_payload},
        headers=headers,
    )


class ApiGuardrailMiddleware(BaseHTTPMiddleware):
    def __init__(
        self,
        app,
        *,
        services: ApiGuardrailServices,
        configuration: ApiConfiguration = DEFAULT_API_CONFIGURATION,
    ) -> None:
        super().__init__(app)
        self._services = services
        self._configuration = configuration

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        request_id = self._request_id(request)
        request.state.request_id = request_id
        request.state.principal_kind = None
        started_at = self._services.monotonic_clock()
        decision = self._services.rate_limiter.check(
            self._client_key(request),
            self._request_cost(request),
        )
        if not decision.allowed:
            response = error_response(
                status_code=429,
                code="rate_limit_exceeded",
                message="Request rate limit exceeded.",
                request_id=request_id,
                headers={"Retry-After": str(decision.retry_after_seconds)},
            )
            return self._finalize(
                request,
                response,
                request_id,
                started_at,
                decision,
            )

        if request.method in BODY_METHODS:
            content_length = request.headers.get("content-length")
            if (
                content_length is not None
                and content_length.isdigit()
                and int(content_length) > self._configuration.maximum_request_body_bytes
            ):
                response = self._request_too_large(request_id)
                return self._finalize(
                    request,
                    response,
                    request_id,
                    started_at,
                    decision,
                )
            body = await request.body()
            if len(body) > self._configuration.maximum_request_body_bytes:
                response = self._request_too_large(request_id)
                return self._finalize(
                    request,
                    response,
                    request_id,
                    started_at,
                    decision,
                )

        response = await call_next(request)
        return self._finalize(
            request,
            response,
            request_id,
            started_at,
            decision,
        )

    def _request_id(self, request: Request) -> str:
        supplied = request.headers.get(self._configuration.request_id_header)
        if supplied is not None and REQUEST_ID_PATTERN.fullmatch(supplied):
            return supplied
        return self._services.request_id_generator()

    def _client_key(self, request: Request) -> str:
        client_host = request.client.host if request.client is not None else "unknown"
        return client_host

    def _request_cost(self, request: Request) -> int:
        path = request.url.path
        if path == "/" or path.startswith("/assets/"):
            return self._configuration.static_asset_request_cost
        if path.startswith("/health/"):
            return self._configuration.health_request_cost
        if path.endswith("/chat"):
            return self._configuration.chat_request_cost
        if "/auth/" in path:
            return self._configuration.authentication_request_cost
        return self._configuration.default_request_cost

    def _request_too_large(self, request_id: str) -> JSONResponse:
        return error_response(
            status_code=413,
            code="request_too_large",
            message="Request body exceeds the configured limit.",
            request_id=request_id,
        )

    def _finalize(
        self,
        request: Request,
        response: Response,
        request_id: str,
        started_at: float,
        decision: RateLimitDecision,
    ) -> Response:
        response.headers[self._configuration.request_id_header] = request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        response.headers["Permissions-Policy"] = (
            "camera=(), microphone=(), geolocation=()"
        )
        response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "img-src 'self' data:; connect-src 'self'; font-src 'self'; "
            "object-src 'none'; base-uri 'none'; form-action 'self'; "
            "frame-ancestors 'none'"
        )
        response.headers["RateLimit-Limit"] = str(
            self._configuration.rate_limit_capacity
        )
        response.headers["RateLimit-Remaining"] = str(decision.remaining)
        runtime_context = getattr(request.app.state, "cinegraph_context", None)
        if (
            runtime_context is not None
            and runtime_context.settings.environment is RuntimeEnvironment.PRODUCTION
        ):
            response.headers["Strict-Transport-Security"] = (
                "max-age=31536000; includeSubDomains"
            )
        duration_ms = max(
            0.0,
            (self._services.monotonic_clock() - started_at) * 1_000,
        )
        self._services.audit_sink.emit(
            HttpAuditEvent(
                occurred_at=self._services.utc_clock(),
                request_id=request_id,
                method=request.method,
                path=request.url.path,
                status_code=response.status_code,
                duration_ms=round(duration_ms, 3),
                outcome=self._outcome(response.status_code),
                principal_kind=request.state.principal_kind,
            )
        )
        return response

    @staticmethod
    def _outcome(status_code: int) -> str:
        if status_code < 400:
            return "success"
        if status_code < 500:
            return "client_error"
        return "server_error"
