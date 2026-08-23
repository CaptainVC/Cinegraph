"""HTTP and replayable SSE adapter for agent jobs."""

import json
import time
from collections.abc import Awaitable, Callable, Mapping
from uuid import UUID

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response, status
from fastapi.responses import StreamingResponse

from cinegraph.adapters.api.agent_job_schemas import (
    AgentJobCitationResponse,
    AgentJobRequest,
    AgentJobResponse,
    AgentJobResultResponse,
)
from cinegraph.application.models.agent_job import (
    AgentJob,
    AgentJobEvent,
    AgentJobEventKind,
)
from cinegraph.application.service.agent_job_service import (
    AgentJobServiceProtocol,
    SubmitAgentJobCommand,
)
from cinegraph.common.error_messages import AgentJobErrorMessages
from cinegraph.config import (
    DEFAULT_AGENT_JOB_CONFIGURATION,
    AgentJobConfiguration,
)
from cinegraph.domain.models.identity import SessionPrincipal
from cinegraph.ports.agent_jobs.agent_job_repository import AgentJobIdempotencyConflictError


def _response(job: AgentJob, status_url: str, events_url: str) -> AgentJobResponse:
    result = None
    if job.result is not None:
        result = AgentJobResultResponse(
            answer=job.result.answer,
            is_safe_refusal=job.result.is_safe_refusal,
            used_tools=job.result.used_tools if not job.result.is_safe_refusal else (),
            citations=tuple(
                AgentJobCitationResponse(
                    kind=citation.kind,
                    episode_id=citation.episode.episode_id,
                    season_number=citation.episode.position.season_number,
                    episode_number=citation.episode.position.episode_number,
                    start_ms=citation.start_ms,
                    end_ms=citation.end_ms,
                    segment_id=citation.segment_id,
                    claim_id=citation.claim_id,
                    evidence_id=citation.evidence_id,
                )
                for citation in job.result.citations
            ),
        )
    return AgentJobResponse(
        job_id=job.job_id,
        thread_id=job.thread_id,
        series_id=job.series_id,
        status=job.status.value,
        created_at=job.created_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
        result=result,
        error_code=job.error_code,
        status_url=status_url,
        events_url=events_url,
    )


def _sse(event: AgentJobEvent) -> bytes:
    payload = json.dumps(
        _json_compatible(event.payload),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    return f"id: {event.sequence}\nevent: {event.kind.value}\ndata: {payload}\n\n".encode("utf-8")


class AgentJobEventStream:
    """Cancellable async SSE iterator with injectable timing for deterministic tests."""

    def __init__(
        self,
        service: AgentJobServiceProtocol,
        job_id: UUID,
        owner_profile_id: UUID,
        sequence: int,
        configuration: AgentJobConfiguration = DEFAULT_AGENT_JOB_CONFIGURATION,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], Awaitable[None]] | None = None,
        disconnected: Callable[[], Awaitable[bool]] | None = None,
    ) -> None:
        self._service = service
        self._job_id = job_id
        self._owner_profile_id = owner_profile_id
        self._sequence = sequence
        self._configuration = configuration
        self._clock = clock
        self._sleeper = sleeper or _async_sleep
        self._disconnected = disconnected or _never_disconnected
        self._started = clock()
        self._heartbeat_at = self._started
        self._emitted = 0
        self._pending: list[bytes] = []
        self._terminal = False

    def __aiter__(self) -> "AgentJobEventStream":
        return self

    async def __anext__(self) -> bytes:
        if (
            self._emitted >= self._configuration.sse_max_events
            or self._clock() - self._started >= self._configuration.sse_max_duration_seconds
            or await self._disconnected()
        ):
            raise StopAsyncIteration
        while not self._pending:
            if (
                self._terminal
                or self._emitted >= self._configuration.sse_max_events
                or self._clock() - self._started >= self._configuration.sse_max_duration_seconds
                or await self._disconnected()
            ):
                raise StopAsyncIteration
            remaining = self._configuration.sse_max_events - self._emitted
            batch_size = min(self._configuration.sse_replay_batch, remaining)
            events = self._service.events_after(
                self._job_id, self._owner_profile_id, self._sequence
            )
            batch = events[:batch_size]
            self._pending.extend(_sse(event) for event in batch)
            if self._pending:
                self._sequence = batch[-1].sequence
                if any(
                    item.kind
                    in {
                        AgentJobEventKind.SUCCEEDED,
                        AgentJobEventKind.SAFE_REFUSAL,
                        AgentJobEventKind.FAILED,
                    }
                    for item in batch
                ):
                    self._terminal = True
                self._emitted += 1
                return self._pending.pop(0)
            now = self._clock()
            if now - self._heartbeat_at >= self._configuration.sse_heartbeat_interval_seconds:
                self._heartbeat_at = now
                return b": heartbeat\n\n"
            await self._sleeper(self._configuration.sse_poll_interval_seconds)
        self._emitted += 1
        return self._pending.pop(0)


async def _async_sleep(seconds: float) -> None:
    import asyncio

    await asyncio.sleep(seconds)


async def _never_disconnected() -> bool:
    return False


def register_agent_job_routes(app: FastAPI, prefix: str) -> None:
    # Local imports keep the legacy API module importable without this adapter.
    from cinegraph.adapters.api.fastapi_app import _context, _principal

    @app.post(
        f"{prefix}/agent/jobs",
        response_model=AgentJobResponse,
        status_code=status.HTTP_202_ACCEPTED,
    )
    def submit_agent_job(
        body: AgentJobRequest,
        request: Request,
        response: Response,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        principal: SessionPrincipal = Depends(_principal),
    ) -> AgentJobResponse:
        if idempotency_key is None:
            raise HTTPException(status_code=422, detail=AgentJobErrorMessages.IDEMPOTENCY_REQUIRED)
        try:
            key = UUID(idempotency_key)
        except (ValueError, AttributeError) as error:
            raise HTTPException(
                status_code=422, detail=AgentJobErrorMessages.IDEMPOTENCY_INVALID
            ) from error
        if idempotency_key != str(key):
            raise HTTPException(status_code=422, detail=AgentJobErrorMessages.IDEMPOTENCY_INVALID)
        context = _context(request)
        service = context.agent_job_service
        if service is None:
            raise HTTPException(status_code=503, detail=AgentJobErrorMessages.SYSTEM_UNAVAILABLE)
        candidates = tuple(
            sorted(
                {
                    item
                    for item in context.catalogue.episode_refs()
                    if item.series_id == body.series_id
                    and principal.corpus_access_scope.allows_episode(item)
                },
                key=lambda item: (
                    item.position.season_number,
                    item.position.episode_number,
                    item.episode_id.hex,
                ),
            )
        )[: DEFAULT_AGENT_JOB_CONFIGURATION.candidate_max_episodes]
        if not candidates:
            raise HTTPException(status_code=404, detail=AgentJobErrorMessages.SERIES_NOT_FOUND)
        try:
            job = service.submit(
                SubmitAgentJobCommand(
                    owner_profile_id=principal.profile_id,
                    thread_id=body.thread_id,
                    series_id=body.series_id,
                    question=body.question,
                    permission_scope_revision=principal.corpus_access_scope.revision,
                    corpus_access_scope=principal.corpus_access_scope,
                    candidate_episodes=candidates,
                    idempotency_key=str(key),
                )
            )
        except AgentJobIdempotencyConflictError as error:
            raise HTTPException(
                status_code=409, detail=AgentJobErrorMessages.IDEMPOTENCY_CONFLICT
            ) from error
        status_url, events_url = _resource_urls(request, job.job_id)
        job_response = _response(job, status_url, events_url)
        response.headers["Location"] = job_response.status_url
        return job_response

    @app.get(
        f"{prefix}/agent/jobs/{{job_id}}", response_model=AgentJobResponse, name="get_agent_job"
    )
    def get_agent_job(
        job_id: UUID, request: Request, principal: SessionPrincipal = Depends(_principal)
    ) -> AgentJobResponse:
        service = _context(request).agent_job_service
        if service is None:
            raise HTTPException(status_code=503, detail=AgentJobErrorMessages.SYSTEM_UNAVAILABLE)
        job = service.get(job_id, principal.profile_id)
        if job is None:
            raise HTTPException(status_code=404, detail=AgentJobErrorMessages.JOB_NOT_FOUND)
        status_url, events_url = _resource_urls(request, job.job_id)
        return _response(job, status_url, events_url)

    @app.get(f"{prefix}/agent/jobs/{{job_id}}/events", name="agent_job_events")
    def agent_job_events(
        job_id: UUID,
        request: Request,
        last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
        principal: SessionPrincipal = Depends(_principal),
    ) -> StreamingResponse:
        service = _context(request).agent_job_service
        if service is None:
            raise HTTPException(status_code=503, detail=AgentJobErrorMessages.SYSTEM_UNAVAILABLE)
        job = service.get(job_id, principal.profile_id)
        if job is None:
            raise HTTPException(status_code=404, detail=AgentJobErrorMessages.JOB_NOT_FOUND)
        sequence = 0
        if last_event_id is not None:
            if (
                len(last_event_id) > 20
                or not last_event_id.isdigit()
                or (len(last_event_id) > 1 and last_event_id.startswith("0"))
            ):
                raise HTTPException(
                    status_code=422, detail=AgentJobErrorMessages.LAST_EVENT_ID_INVALID
                )
            sequence = int(last_event_id)
            if sequence > DEFAULT_AGENT_JOB_CONFIGURATION.sse_max_events:
                raise HTTPException(
                    status_code=422,
                    detail=AgentJobErrorMessages.LAST_EVENT_ID_INVALID,
                )
        stream = AgentJobEventStream(
            service,
            job_id,
            principal.profile_id,
            sequence,
            disconnected=request.is_disconnected,
        )
        return StreamingResponse(
            stream,
            media_type=DEFAULT_AGENT_JOB_CONFIGURATION.sse_media_type,
            headers={
                "Cache-Control": DEFAULT_AGENT_JOB_CONFIGURATION.sse_cache_control,
                "X-Accel-Buffering": DEFAULT_AGENT_JOB_CONFIGURATION.sse_accel_buffering,
                "Connection": DEFAULT_AGENT_JOB_CONFIGURATION.sse_connection,
            },
        )


def _resource_urls(request: Request, job_id: UUID) -> tuple[str, str]:
    return (
        str(request.url_for("get_agent_job", job_id=str(job_id))),
        str(request.url_for("agent_job_events", job_id=str(job_id))),
    )


def _json_compatible(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: _json_compatible(child) for key, child in value.items()}
    if isinstance(value, tuple):
        return [_json_compatible(child) for child in value]
    return value
