from __future__ import annotations

from datetime import datetime
from hashlib import sha256
from uuid import NAMESPACE_URL, UUID, uuid5

from cinegraph.application.models.ingestion_job import EnqueueIngestionJob
from cinegraph.common.error_messages import IngestionJobErrorMessages
from cinegraph.config import (
    ALLOWED_INGESTION_ERROR_CODES,
    DEFAULT_INGESTION_JOB_CONFIGURATION,
    IngestionJobConfiguration,
)
from cinegraph.domain.enums.enum import IngestionJobEventKind, IngestionJobStatus
from cinegraph.domain.models.ingestion_job import IngestionJob, IngestionJobEvent
from cinegraph.ports.date_time.clock import Clock
from cinegraph.ports.ingestion_jobs import IngestionJobUnitOfWork, IngestionJobUnitOfWorkFactory


def deterministic_ingestion_idempotency_key(command: EnqueueIngestionJob) -> str:
    raw = ":".join(
        (
            "cinegraph-ingestion-v1",
            command.kind.value,
            str(command.series_id),
            str(command.season_number or ""),
            str(command.episode_number or ""),
            command.source_fingerprint,
            command.pipeline_revision,
        )
    )
    return sha256(raw.encode("utf-8")).hexdigest()


class IngestionJobService:
    def __init__(
        self,
        unit_of_work_factory: IngestionJobUnitOfWorkFactory,
        clock: Clock,
        configuration: IngestionJobConfiguration = DEFAULT_INGESTION_JOB_CONFIGURATION,
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._clock = clock
        self._configuration = configuration

    def enqueue(self, command: EnqueueIngestionJob) -> IngestionJob:
        now = self._clock.now_utc()
        scheduled_at = command.scheduled_at or now
        max_attempts = command.max_attempts or self._configuration.default_max_attempts
        job = IngestionJob(
            job_id=uuid5(
                NAMESPACE_URL,
                f"cinegraph:ingestion:{deterministic_ingestion_idempotency_key(command)}",
            ),
            idempotency_key=deterministic_ingestion_idempotency_key(command),
            kind=command.kind,
            status=IngestionJobStatus.PENDING,
            series_id=command.series_id,
            season_number=command.season_number,
            episode_number=command.episode_number,
            source_fingerprint=command.source_fingerprint,
            pipeline_revision=command.pipeline_revision,
            priority=command.priority,
            scheduled_at=scheduled_at,
            max_attempts=max_attempts,
            created_at=now,
        )
        with self._unit_of_work_factory() as unit_of_work:
            existing = unit_of_work.jobs.get_by_idempotency_key(job.idempotency_key)
            if existing is not None:
                return existing
            inserted = unit_of_work.jobs.add(job)
            if inserted.job_id != job.job_id:
                return inserted
            self._append_event(unit_of_work, job, IngestionJobEventKind.ENQUEUED, now)
            unit_of_work.commit()
            return job

    def claim_next(self, worker_id: str) -> IngestionJob | None:
        now = self._clock.now_utc()
        with self._unit_of_work_factory() as unit_of_work:
            job = unit_of_work.jobs.claim_next(
                worker_id,
                now,
                now + self._configuration.lease_duration,
            )
            if job is None:
                return None
            self._append_event(unit_of_work, job, IngestionJobEventKind.CLAIMED, now, worker_id)
            unit_of_work.commit()
            return job

    def heartbeat(self, job_id: UUID, worker_id: str) -> IngestionJob:
        now = self._clock.now_utc()
        with self._unit_of_work_factory() as unit_of_work:
            job = self._required(unit_of_work, job_id)
            expected_lease = self._required_lease(job)
            updated = job.heartbeat(
                worker_id,
                now,
                expected_lease + self._configuration.heartbeat_extension,
            )
            unit_of_work.jobs.save_owned(updated, worker_id, now, expected_lease)
            self._append_event(
                unit_of_work, updated, IngestionJobEventKind.HEARTBEAT, now, worker_id
            )
            unit_of_work.commit()
            return updated

    def succeed(self, job_id: UUID, worker_id: str) -> IngestionJob:
        now = self._clock.now_utc()
        with self._unit_of_work_factory() as unit_of_work:
            job = self._required(unit_of_work, job_id)
            expected_lease = self._required_lease(job)
            updated = job.succeed(worker_id, now)
            unit_of_work.jobs.save_owned(updated, worker_id, now, expected_lease)
            self._append_event(
                unit_of_work, updated, IngestionJobEventKind.SUCCEEDED, now, worker_id
            )
            unit_of_work.commit()
            return updated

    def fail_or_retry(self, job_id: UUID, worker_id: str, error_code: str) -> IngestionJob:
        if error_code not in ALLOWED_INGESTION_ERROR_CODES:
            raise ValueError(IngestionJobErrorMessages.ERROR_CODE_NOT_ALLOWLISTED)
        now = self._clock.now_utc()
        with self._unit_of_work_factory() as unit_of_work:
            job = self._required(unit_of_work, job_id)
            expected_lease = self._required_lease(job)
            if job.attempts >= job.max_attempts:
                updated = job.fail(worker_id, now, error_code)
                event_kind = IngestionJobEventKind.FAILED
            else:
                delay = min(
                    self._configuration.retry_base_delay * (2 ** max(job.attempts - 1, 0)),
                    self._configuration.retry_max_delay,
                )
                updated = job.retry(worker_id, now, now + delay, error_code)
                event_kind = IngestionJobEventKind.RETRIED
            unit_of_work.jobs.save_owned(updated, worker_id, now, expected_lease)
            self._append_event(unit_of_work, updated, event_kind, now, worker_id, error_code)
            unit_of_work.commit()
            return updated

    def cancel(self, job_id: UUID) -> IngestionJob:
        now = self._clock.now_utc()
        with self._unit_of_work_factory() as unit_of_work:
            job = self._required(unit_of_work, job_id)
            updated = job.cancel(now)
            unit_of_work.jobs.save_pending(updated, now)
            self._append_event(unit_of_work, updated, IngestionJobEventKind.CANCELLED, now)
            unit_of_work.commit()
            return updated

    def reclaim_expired(self, job_id: UUID) -> IngestionJob:
        now = self._clock.now_utc()
        with self._unit_of_work_factory() as unit_of_work:
            job = self._required(unit_of_work, job_id)
            updated = job.reclaim(now, now + self._configuration.retry_base_delay)
            if job.lease_expires_at is None:
                raise ValueError("Running ingestion job has no lease.")
            unit_of_work.jobs.save_reclaimed(updated, now, job.lease_expires_at)
            self._append_event(
                unit_of_work,
                updated,
                IngestionJobEventKind.RECLAIMED,
                now,
                error_code=updated.last_error_code,
            )
            unit_of_work.commit()
            return updated

    def events(self, job_id: UUID) -> tuple[IngestionJobEvent, ...]:
        with self._unit_of_work_factory() as unit_of_work:
            return unit_of_work.jobs.events(job_id)

    @staticmethod
    def _required(unit_of_work: IngestionJobUnitOfWork, job_id: UUID) -> IngestionJob:
        job = unit_of_work.jobs.get(job_id)
        if job is None:
            raise KeyError(job_id)
        return job

    @staticmethod
    def _required_lease(job: IngestionJob) -> datetime:
        if job.lease_expires_at is None:
            raise ValueError("Running ingestion job has no lease.")
        return job.lease_expires_at

    @staticmethod
    def _append_event(
        unit_of_work: IngestionJobUnitOfWork,
        job: IngestionJob,
        kind: IngestionJobEventKind,
        now: datetime,
        worker_id: str | None = None,
        error_code: str | None = None,
    ) -> None:
        events = unit_of_work.jobs.events(job.job_id)
        unit_of_work.jobs.append_event(
            IngestionJobEvent(
                event_id=uuid5(
                    NAMESPACE_URL, f"cinegraph:ingestion-event:{job.job_id}:{len(events) + 1}"
                ),
                job_id=job.job_id,
                sequence_number=len(events) + 1,
                kind=kind,
                occurred_at=now,
                attempt=job.attempts,
                worker_id=worker_id,
                error_code=error_code,
            )
        )
