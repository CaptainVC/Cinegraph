"""HTTP and replayable SSE adapter for agent jobs."""

import json
import time
from collections.abc import Awaitable, Callable, Mapping
from uuid import UUID

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response, status
from fastapi.responses import StreamingResponse

from cinegraph.adapters.api.agent_job_schemas import (
    AgentEvidenceExcerptResponse,
    AgentEvidenceResponse,
    AgentJobCitationResponse,
    AgentJobEntityResponse,
    AgentJobRelationshipResponse,
    AgentJobRequest,
    AgentJobResponse,
    AgentJobResultResponse,
)
from cinegraph.adapters.api.context import ApiContext
from cinegraph.adapters.evidence import build_agent_evidence_request
from cinegraph.application.models.agent_job import (
    AgentJob,
    AgentJobEvent,
    AgentJobEventKind,
)
from cinegraph.application.models.series_agent_result import SeriesAgentCitation
from cinegraph.application.service.agent_job_service import (
    AgentJobServiceProtocol,
    SubmitAgentJobCommand,
)
from cinegraph.common.error_messages import AgentJobErrorMessages
from cinegraph.config import (
    DEFAULT_AGENT_JOB_CONFIGURATION,
    AgentJobConfiguration,
)
from cinegraph.domain.exceptions.errors import InvalidModelError
from cinegraph.domain.models.identity import SessionPrincipal
from cinegraph.domain.models.watch_state import EpisodeRef
from cinegraph.ports.agent_jobs.agent_evidence_reader import AgentEvidenceNotFoundError
from cinegraph.ports.agent_jobs.agent_job_repository import (
    AgentJobIdempotencyConflictError,
    AgentJobUnavailableError,
)


def _bounded_job_candidates(
    visible_episodes: tuple[EpisodeRef, ...],
    safe_through_episode_id: UUID | None,
    limit: int,
) -> tuple[EpisodeRef, ...]:
    """Keep protected windows bounded without dropping their trusted boundary."""

    if len(visible_episodes) <= limit:
        return visible_episodes
    if safe_through_episode_id is None:
        return visible_episodes[:limit]
    boundary_index = next(
        (
            index
            for index, episode in enumerate(visible_episodes)
            if episode.episode_id == safe_through_episode_id
        ),
        None,
    )
    if boundary_index is None:
        return ()
    start = max(0, boundary_index - limit + 1)
    return visible_episodes[start : boundary_index + 1]


def _response(job: AgentJob, status_url: str, events_url: str) -> AgentJobResponse:
    result = None
    if job.result is not None:
        result = AgentJobResultResponse(
            answer=job.result.answer,
            is_safe_refusal=job.result.is_safe_refusal,
            used_tools=job.result.used_tools if not job.result.is_safe_refusal else (),
            citations=tuple(
                AgentJobCitationResponse(
                    citation_id=citation.citation_id,
                    kind=citation.kind,
                    episode_id=citation.episode.episode_id,
                    season_number=citation.episode.position.season_number,
                    episode_number=citation.episode.position.episode_number,
                    start_ms=citation.start_ms,
                    end_ms=citation.end_ms,
                    segment_id=citation.segment_id,
                    claim_id=citation.claim_id,
                    evidence_id=citation.evidence_id,
                    graph=_graph_response(citation),
                )
                for citation in job.result.citations
            ),
            evidence_url=(
                f"{status_url}/evidence" if not job.result.is_safe_refusal else None
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


def _graph_response(citation: SeriesAgentCitation) -> AgentJobRelationshipResponse | None:
    if not all(
        value is not None
        for value in (
            citation.subject_entity_id,
            citation.subject_kind,
            citation.subject_display_name,
            citation.predicate,
            citation.object_entity_id,
            citation.object_kind,
            citation.object_display_name,
            citation.polarity,
            citation.hop_distance,
            citation.score,
        )
    ):
        return None
    assert citation.subject_entity_id is not None
    assert citation.subject_kind is not None
    assert citation.subject_display_name is not None
    assert citation.predicate is not None
    assert citation.object_entity_id is not None
    assert citation.object_kind is not None
    assert citation.object_display_name is not None
    assert citation.polarity is not None
    assert citation.hop_distance is not None
    assert citation.score is not None
    return AgentJobRelationshipResponse(
        subject=AgentJobEntityResponse(
            entity_id=citation.subject_entity_id,
            kind=citation.subject_kind.value,
            display_name=citation.subject_display_name,
        ),
        predicate=citation.predicate,
        object=AgentJobEntityResponse(
            entity_id=citation.object_entity_id,
            kind=citation.object_kind.value,
            display_name=citation.object_display_name,
        ),
        polarity=citation.polarity.value,
        hop_distance=citation.hop_distance,
        score=citation.score,
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
        # Keep event metadata alongside frames so the replay cursor advances only
        # after a frame has actually been yielded to the client.
        self._pending: list[tuple[int, bytes, bool]] = []
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
            self._pending.extend(
                (
                    event.sequence,
                    _sse(event),
                    event.kind
                    in {
                        AgentJobEventKind.SUCCEEDED,
                        AgentJobEventKind.SAFE_REFUSAL,
                        AgentJobEventKind.FAILED,
                    },
                )
                for event in batch
            )
            if self._pending:
                sequence, frame, terminal = self._pending.pop(0)
                self._sequence = sequence
                self._terminal = terminal
                self._emitted += 1
                return frame
            now = self._clock()
            if now - self._heartbeat_at >= self._configuration.sse_heartbeat_interval_seconds:
                self._heartbeat_at = now
                return b": heartbeat\n\n"
            await self._sleeper(self._configuration.sse_poll_interval_seconds)
        sequence, frame, terminal = self._pending.pop(0)
        self._sequence = sequence
        self._terminal = terminal
        self._emitted += 1
        return frame


async def _async_sleep(seconds: float) -> None:
    import asyncio

    await asyncio.sleep(seconds)


async def _never_disconnected() -> bool:
    return False


def register_agent_job_routes(app: FastAPI, prefix: str) -> None:
    # Local imports keep the legacy API module importable without this adapter.
    from cinegraph.adapters.api.fastapi_app import _build_watch_state, _context, _principal
    from cinegraph.domain.policy.spoiler_policy import SpoilerPolicy
    from cinegraph.domain.retrieval import RetrievalScopeCompiler

    def authorized_job(
        context: ApiContext, job_id: UUID, principal: SessionPrincipal
    ) -> AgentJob | None:
        service = context.agent_job_service
        if service is None:
            raise HTTPException(status_code=503, detail=AgentJobErrorMessages.SYSTEM_UNAVAILABLE)
        job = service.get(job_id, principal.profile_id)
        if job is None:
            return None
        if (
            job.permission_scope_revision != principal.corpus_access_scope.revision
            or not principal.corpus_access_scope.allows_all(job.candidate_episodes)
        ):
            return None
        return job

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
        all_episodes = tuple(
            item for item in context.catalogue.episode_refs() if item.series_id == body.series_id
        )
        entitled = tuple(
            item for item in all_episodes if principal.corpus_access_scope.allows_episode(item)
        )
        if body.safe_through_episode_id is not None and not any(
            item.episode_id == body.safe_through_episode_id for item in entitled
        ):
            raise HTTPException(status_code=404, detail=AgentJobErrorMessages.SERIES_NOT_FOUND)
        watch_state = _build_watch_state(context, principal, body)
        scope = RetrievalScopeCompiler(SpoilerPolicy()).compile(
            body.series_id, entitled, watch_state, principal.corpus_access_scope
        )
        candidates = _bounded_job_candidates(
            tuple(item.episode for item in scope.episode_scopes),
            body.safe_through_episode_id,
            DEFAULT_AGENT_JOB_CONFIGURATION.candidate_max_episodes,
        )
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
                    request_id=getattr(request.state, "request_id", None),
                    spoiler_mode=body.spoiler_mode,
                    safe_through_episode_id=body.safe_through_episode_id,
                )
            )
        except AgentJobIdempotencyConflictError as error:
            raise HTTPException(
                status_code=409, detail=AgentJobErrorMessages.IDEMPOTENCY_CONFLICT
            ) from error
        except AgentJobUnavailableError as error:
            raise HTTPException(
                status_code=503, detail=AgentJobErrorMessages.SYSTEM_UNAVAILABLE
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
        try:
            job = authorized_job(_context(request), job_id, principal)
        except AgentJobUnavailableError as error:
            raise HTTPException(
                status_code=503, detail=AgentJobErrorMessages.SYSTEM_UNAVAILABLE
            ) from error
        if job is None:
            raise HTTPException(status_code=404, detail=AgentJobErrorMessages.JOB_NOT_FOUND)
        status_url, events_url = _resource_urls(request, job.job_id)
        return _response(job, status_url, events_url)

    @app.get(
        f"{prefix}/agent/jobs/{{job_id}}/evidence",
        response_model=AgentEvidenceResponse,
    )
    def agent_job_evidence(
        job_id: UUID,
        request: Request,
        response: Response,
        principal: SessionPrincipal = Depends(_principal),
    ) -> AgentEvidenceResponse:
        context = _context(request)
        try:
            job = authorized_job(context, job_id, principal)
        except AgentJobUnavailableError as error:
            raise HTTPException(status_code=503, detail=AgentJobErrorMessages.SYSTEM_UNAVAILABLE) from error
        if job is None or job.result is None or job.result.is_safe_refusal:
            raise HTTPException(status_code=404, detail=AgentJobErrorMessages.EVIDENCE_NOT_FOUND)
        citation_id = tuple(
            item.citation_id
            for item in job.result.citations[: DEFAULT_AGENT_JOB_CONFIGURATION.evidence_citation_limit]
        )
        if not citation_id:
            raise HTTPException(status_code=404, detail=AgentJobErrorMessages.EVIDENCE_NOT_FOUND)
        response.headers["Cache-Control"] = DEFAULT_AGENT_JOB_CONFIGURATION.evidence_cache_control
        reader = context.evidence_reader
        if reader is None:
            raise HTTPException(status_code=503, detail=AgentJobErrorMessages.SYSTEM_UNAVAILABLE)
        try:
            result = reader.read(
                build_agent_evidence_request(job, citation_id),
                principal.corpus_access_scope,
            )
        except (AgentEvidenceNotFoundError, InvalidModelError, ValueError) as error:
            raise HTTPException(status_code=404, detail=AgentJobErrorMessages.EVIDENCE_NOT_FOUND) from error
        except Exception as error:
            raise HTTPException(status_code=503, detail=AgentJobErrorMessages.SYSTEM_UNAVAILABLE) from error
        return AgentEvidenceResponse(
            job_id=job.job_id,
            items=tuple(
                AgentEvidenceExcerptResponse(
                    citation_id=item.citation_id,
                    excerpt=item.text,
                )
                for item in result.excerpts
            ),
        )

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
        try:
            job = authorized_job(_context(request), job_id, principal)
        except AgentJobUnavailableError as error:
            raise HTTPException(
                status_code=503, detail=AgentJobErrorMessages.SYSTEM_UNAVAILABLE
            ) from error
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
