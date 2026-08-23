from copy import deepcopy
from datetime import datetime
from threading import RLock
from uuid import UUID

from cinegraph.domain.enums.enum import IngestionJobStatus
from cinegraph.domain.models.ingestion_job import IngestionJob, IngestionJobEvent


class _InMemoryIngestionJobUnitOfWork:
    def __init__(self, factory: "InMemoryIngestionJobUnitOfWorkFactory") -> None:
        self._factory = factory
        self._lock = factory._lock
        self._jobs: dict[UUID, IngestionJob] = {}
        self._events: dict[UUID, list[IngestionJobEvent]] = {}
        self._active = False
        self._closed = False
        self._committed = False
        self.jobs = _TransactionalIngestionJobRepository(self)

    def __enter__(self) -> "_InMemoryIngestionJobUnitOfWork":
        if self._active or self._closed:
            raise RuntimeError("Ingestion job unit of work cannot be re-entered.")
        self._lock.acquire()
        self._jobs = deepcopy(self._factory._jobs)
        self._events = deepcopy(self._factory._events)
        self._active = True
        self._committed = False
        return self

    def __exit__(self, exception_type: object, exception: object, traceback: object) -> None:
        try:
            if exception_type is not None:
                self.rollback()
        finally:
            self._active = False
            self._closed = True
            self._lock.release()

    def commit(self) -> None:
        if not self._active or self._committed:
            raise RuntimeError("Ingestion job unit of work is not active.")
        self._factory._jobs = deepcopy(self._jobs)
        self._factory._events = deepcopy(self._events)
        self._committed = True

    def rollback(self) -> None:
        if self._active and not self._committed:
            self._jobs = deepcopy(self._factory._jobs)
            self._events = deepcopy(self._factory._events)


class _TransactionalIngestionJobRepository:
    def __init__(self, unit_of_work: _InMemoryIngestionJobUnitOfWork) -> None:
        self._unit_of_work = unit_of_work

    def get_by_idempotency_key(self, key: str) -> IngestionJob | None:
        return next(
            (job for job in self._unit_of_work._jobs.values() if job.idempotency_key == key), None
        )

    def get(self, job_id: UUID) -> IngestionJob | None:
        return self._unit_of_work._jobs.get(job_id)

    def add(self, job: IngestionJob) -> IngestionJob:
        existing = self.get_by_idempotency_key(job.idempotency_key)
        if existing is not None:
            return existing
        self._unit_of_work._jobs[job.job_id] = job
        self._unit_of_work._events[job.job_id] = []
        return job

    def _replace(self, job: IngestionJob) -> None:
        self._unit_of_work._jobs[job.job_id] = job

    def save_owned(
        self,
        job: IngestionJob,
        worker_id: str,
        now: datetime,
        expected_lease_expires_at: datetime,
    ) -> None:
        current = self.get(job.job_id)
        if (
            current is None
            or current.lease_owner != worker_id
            or current.lease_expires_at != expected_lease_expires_at
            or current.lease_expires_at <= now
        ):
            raise ValueError("The worker lease is no longer valid for this ingestion job.")
        self._replace(job)

    def save_pending(self, job: IngestionJob, now: datetime) -> None:
        current = self.get(job.job_id)
        if current is None or current.status is not IngestionJobStatus.PENDING:
            raise ValueError("The pending ingestion job changed before cancellation.")
        self._replace(job)

    def save_reclaimed(
        self, job: IngestionJob, now: datetime, expected_lease_expires_at: datetime
    ) -> None:
        current = self.get(job.job_id)
        if (
            current is None
            or current.status is not IngestionJobStatus.RUNNING
            or current.lease_expires_at != expected_lease_expires_at
            or current.lease_expires_at > now
        ):
            raise ValueError("The ingestion job lease changed before reclaim.")
        self._replace(job)

    def claim_next(
        self, worker_id: str, now: datetime, lease_expires_at: datetime
    ) -> IngestionJob | None:
        candidates = sorted(
            (
                job
                for job in self._unit_of_work._jobs.values()
                if job.status is IngestionJobStatus.PENDING
                and job.scheduled_at <= now
                and (job.next_attempt_at is None or job.next_attempt_at <= now)
            ),
            key=lambda item: (
                -item.priority,
                item.scheduled_at,
                item.created_at or item.scheduled_at,
                item.job_id.hex,
            ),
        )
        if not candidates:
            return None
        claimed = candidates[0].claim(worker_id, now, lease_expires_at)
        self._replace(claimed)
        return claimed

    def events(self, job_id: UUID) -> tuple[IngestionJobEvent, ...]:
        return tuple(self._unit_of_work._events.get(job_id, ()))

    def append_event(self, event: IngestionJobEvent) -> None:
        events = self._unit_of_work._events.setdefault(event.job_id, [])
        if events and event.sequence_number != events[-1].sequence_number + 1:
            raise ValueError("Ingestion job events must be append-only and ordered.")
        if not events and event.sequence_number != 1:
            raise ValueError("The first ingestion job event must have sequence number 1.")
        events.append(event)


class InMemoryIngestionJobUnitOfWorkFactory:
    def __init__(self) -> None:
        self._jobs: dict[UUID, IngestionJob] = {}
        self._events: dict[UUID, list[IngestionJobEvent]] = {}
        self._lock = RLock()

    def __call__(self) -> _InMemoryIngestionJobUnitOfWork:
        return _InMemoryIngestionJobUnitOfWork(self)
