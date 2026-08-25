"""Thread-safe, production-shaped in-memory agent job store for this phase."""

from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from threading import RLock
from typing import cast
from uuid import UUID, uuid5

from cinegraph.application.models.agent_job import (
    AgentJob,
    AgentJobEvent,
    AgentJobEventKind,
    AgentJobStatus,
    JsonPayload,
)
from cinegraph.application.models.series_agent_result import SeriesAgentResult
from cinegraph.application.serialization.agent_job_payload import result_event_payload
from cinegraph.common.error_messages import AgentJobErrorMessages
from cinegraph.ports.agent_jobs.agent_job_repository import (
    AgentJobIdempotencyConflictError,
    AgentJobTransitionError,
)


class InMemoryAgentJobRepository:
    def __init__(self, clock: Callable[[], datetime] | None = None) -> None:
        self._jobs: dict[UUID, AgentJob] = {}
        self._events: dict[UUID, list[AgentJobEvent]] = {}
        self._keys: dict[tuple[UUID, str], UUID] = {}
        self._lock = RLock()
        self._clock = clock or (lambda: datetime.now(UTC))

    def create(self, job: AgentJob) -> tuple[AgentJob, bool]:
        with self._lock:
            key = (job.owner_profile_id, job.idempotency_key)
            existing_id = self._keys.get(key)
            if existing_id is not None:
                existing = self._jobs[existing_id]
                if existing.request_fingerprint != job.request_fingerprint:
                    raise AgentJobIdempotencyConflictError(
                        AgentJobErrorMessages.IDEMPOTENCY_CONFLICT
                    )
                return existing, False
            if job.job_id in self._jobs:
                raise ValueError(AgentJobErrorMessages.REPOSITORY_ID_CONFLICT)
            self._jobs[job.job_id] = job
            self._keys[key] = job.job_id
            self._events[job.job_id] = []
            try:
                self._append_locked(job.job_id, AgentJobEventKind.QUEUED, {"status": "queued"})
            except Exception:
                del self._jobs[job.job_id], self._keys[key], self._events[job.job_id]
                raise
            return job, True

    create_or_get = create

    def get(self, job_id: UUID, owner_profile_id: UUID | None = None) -> AgentJob | None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or (
                owner_profile_id is not None and job.owner_profile_id != owner_profile_id
            ):
                return None
            return job

    def _claim_locked(self, job_id: UUID, owner_profile_id: UUID | None = None) -> AgentJob | None:
        job = self.get(job_id, owner_profile_id)
        if job is None or job.status is AgentJobStatus.RUNNING:
            return None
        if job.status.terminal:
            raise AgentJobTransitionError(AgentJobErrorMessages.REPOSITORY_TERMINAL_CLAIM)
        updated = job.start(self._clock())
        self._jobs[job_id] = updated
        return updated

    def claim_with_event(
        self, job_id: UUID, owner_profile_id: UUID | None = None
    ) -> AgentJob | None:
        with self._lock:
            prior = self.get(job_id, owner_profile_id)
            job = self._claim_locked(job_id, owner_profile_id)
            if job is not None:
                try:
                    self._append_locked(job_id, AgentJobEventKind.RUNNING, {"status": "running"})
                except Exception:
                    if prior is not None:
                        self._jobs[job_id] = prior
                    raise
            return job

    def _complete_locked(
        self,
        job_id: UUID,
        result: SeriesAgentResult,
        owner_profile_id: UUID | None = None,
    ) -> AgentJob | None:
        job = self.get(job_id, owner_profile_id)
        if job is None:
            return None
        if job.status is not AgentJobStatus.RUNNING:
            raise AgentJobTransitionError(AgentJobErrorMessages.REPOSITORY_COMPLETE_STATE)
        updated = job.complete(result, self._clock())
        self._jobs[job_id] = updated
        return updated

    def complete_with_event(
        self, job_id: UUID, result: SeriesAgentResult, owner_profile_id: UUID | None = None
    ) -> AgentJob | None:
        with self._lock:
            prior = self.get(job_id, owner_profile_id)
            updated = self._complete_locked(job_id, result, owner_profile_id)
            if updated is not None:
                kind = (
                    AgentJobEventKind.SAFE_REFUSAL
                    if updated.status is AgentJobStatus.SAFE_REFUSAL
                    else AgentJobEventKind.SUCCEEDED
                )
                try:
                    self._append_locked(job_id, kind, result_event_payload(result))
                except Exception:
                    if prior is not None:
                        self._jobs[job_id] = prior
                    raise
            return updated

    def _fail_locked(
        self,
        job_id: UUID,
        error_code: str,
        owner_profile_id: UUID | None = None,
    ) -> AgentJob | None:
        job = self.get(job_id, owner_profile_id)
        if job is None:
            return None
        if job.status is not AgentJobStatus.RUNNING:
            raise AgentJobTransitionError(AgentJobErrorMessages.REPOSITORY_FAIL_STATE)
        updated = job.fail(error_code, self._clock())
        self._jobs[job_id] = updated
        return updated

    def fail_with_event(
        self, job_id: UUID, error_code: str, owner_profile_id: UUID | None = None
    ) -> AgentJob | None:
        with self._lock:
            prior = self.get(job_id, owner_profile_id)
            updated = self._fail_locked(job_id, error_code, owner_profile_id)
            if updated is not None:
                try:
                    self._append_locked(
                        job_id,
                        AgentJobEventKind.FAILED,
                        {"status": "failed", "error_code": error_code},
                    )
                except Exception:
                    if prior is not None:
                        self._jobs[job_id] = prior
                    raise
            return updated

    def _reject_locked(
        self,
        job_id: UUID,
        error_code: str,
        owner_profile_id: UUID | None = None,
    ) -> AgentJob | None:
        job = self.get(job_id, owner_profile_id)
        if job is None:
            return None
        if job.status is not AgentJobStatus.QUEUED:
            raise AgentJobTransitionError(AgentJobErrorMessages.REPOSITORY_REJECT_STATE)
        updated = job.reject(error_code, self._clock())
        self._jobs[job_id] = updated
        return updated

    def reject_with_event(
        self, job_id: UUID, error_code: str, owner_profile_id: UUID | None = None
    ) -> AgentJob | None:
        with self._lock:
            prior = self.get(job_id, owner_profile_id)
            updated = self._reject_locked(job_id, error_code, owner_profile_id)
            if updated is not None:
                try:
                    self._append_locked(
                        job_id,
                        AgentJobEventKind.FAILED,
                        {"status": "failed", "error_code": error_code},
                    )
                except Exception:
                    if prior is not None:
                        self._jobs[job_id] = prior
                    raise
            return updated

    def _append_event_locked(self, event: AgentJobEvent) -> AgentJobEvent:
        if event.job_id not in self._jobs:
            raise KeyError(AgentJobErrorMessages.REPOSITORY_JOB_NOT_FOUND)
        events = self._events[event.job_id]
        expected_kind = AgentJobEventKind(self._jobs[event.job_id].status.value)
        if event.kind is not expected_kind:
            raise ValueError(AgentJobErrorMessages.REPOSITORY_EVENT_STATE)
        expected = len(events) + 1
        if event.sequence != expected:
            raise ValueError(AgentJobErrorMessages.REPOSITORY_EVENT_SEQUENCE)
        if any(item.event_id == event.event_id for item in events):
            raise ValueError(AgentJobErrorMessages.REPOSITORY_EVENT_ID)
        if events and event.occurred_at < events[-1].occurred_at:
            raise ValueError(AgentJobErrorMessages.REPOSITORY_EVENT_TIME)
        events.append(event)
        return event

    def _append_locked(
        self, job_id: UUID, kind: str | AgentJobEventKind, payload: Mapping[str, object]
    ) -> AgentJobEvent:
        event_kind = kind if isinstance(kind, AgentJobEventKind) else AgentJobEventKind(kind)
        job = self._jobs[job_id]
        expected_kind = AgentJobEventKind(job.status.value)
        if event_kind is not expected_kind:
            raise ValueError(AgentJobErrorMessages.REPOSITORY_EVENT_STATE)
        if any(item.kind is event_kind for item in self._events[job_id]):
            raise ValueError(AgentJobErrorMessages.REPOSITORY_EVENT_ONCE)
        event = AgentJobEvent(
            event_id=uuid5(job_id, f"agent-job-event:{len(self._events[job_id]) + 1}"),
            sequence=len(self._events[job_id]) + 1,
            job_id=job_id,
            kind=event_kind,
            occurred_at=self._clock(),
            payload=cast(JsonPayload, payload),
        )
        return self._append_event_locked(event)

    def list_events_after(
        self, job_id: UUID, sequence: int = 0, owner_profile_id: UUID | None = None
    ) -> tuple[AgentJobEvent, ...]:
        if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 0:
            raise ValueError(AgentJobErrorMessages.REPOSITORY_CURSOR)
        with self._lock:
            if self.get(job_id, owner_profile_id) is None:
                return ()
            return tuple(event for event in self._events[job_id] if event.sequence > sequence)

    events_after = list_events_after
