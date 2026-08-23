from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from uuid import UUID

from cinegraph.common.error_messages import IngestionJobErrorMessages
from cinegraph.domain.enums.enum import (
    IngestionJobEventKind,
    IngestionJobKind,
    IngestionJobStatus,
)
from cinegraph.domain.exceptions.errors import InvalidModelError
from cinegraph.domain.models.ingestion_job_limits import (
    ALLOWED_INGESTION_ERROR_CODES,
    LEASE_EXPIRED_ERROR_CODE,
    LEASE_EXPIRED_MAX_ATTEMPTS_ERROR_CODE,
    MAX_INGESTION_JOB_ATTEMPTS,
    MAX_INGESTION_JOB_PRIORITY,
    REVISION_PATTERN,
    SHA256_PATTERN,
    WORKER_PATTERN,
)


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise InvalidModelError(IngestionJobErrorMessages.UTC_TIMESTAMP_REQUIRED)
    return value.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class IngestionJob:
    job_id: UUID
    idempotency_key: str
    kind: IngestionJobKind
    status: IngestionJobStatus
    series_id: UUID
    season_number: int | None
    episode_number: int | None
    source_fingerprint: str
    pipeline_revision: str
    priority: int
    scheduled_at: datetime
    max_attempts: int
    created_at: datetime
    attempts: int = 0
    lease_owner: str | None = None
    lease_expires_at: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    last_error_code: str | None = None
    next_attempt_at: datetime | None = None

    def __post_init__(self) -> None:
        if not SHA256_PATTERN.fullmatch(self.idempotency_key):
            raise InvalidModelError(IngestionJobErrorMessages.INVALID_TRANSITION)
        if not SHA256_PATTERN.fullmatch(self.source_fingerprint):
            raise InvalidModelError(IngestionJobErrorMessages.SOURCE_FINGERPRINT_REQUIRED)
        if not REVISION_PATTERN.fullmatch(self.pipeline_revision):
            raise InvalidModelError(IngestionJobErrorMessages.PIPELINE_REVISION_REQUIRED)
        if self.season_number is None and self.episode_number is not None:
            raise InvalidModelError(IngestionJobErrorMessages.EPISODE_REQUIRES_SEASON)
        if self.season_number is not None and self.season_number < 1:
            raise InvalidModelError(IngestionJobErrorMessages.INVALID_TRANSITION)
        if self.episode_number is not None and self.episode_number < 1:
            raise InvalidModelError(IngestionJobErrorMessages.INVALID_TRANSITION)
        if self.priority < 0 or self.priority > MAX_INGESTION_JOB_PRIORITY:
            raise InvalidModelError(IngestionJobErrorMessages.INVALID_TRANSITION)
        if self.max_attempts < 1 or self.max_attempts > MAX_INGESTION_JOB_ATTEMPTS:
            raise InvalidModelError(IngestionJobErrorMessages.MAX_ATTEMPTS_INVALID)
        if self.attempts < 0 or self.attempts > self.max_attempts:
            raise InvalidModelError(IngestionJobErrorMessages.ATTEMPTS_INVALID)
        for timestamp in (
            self.started_at,
            self.finished_at,
            self.lease_expires_at,
            self.next_attempt_at,
        ):
            if timestamp is not None:
                _utc(timestamp)
        _utc(self.created_at)
        _utc(self.scheduled_at)
        if self.started_at is not None and self.started_at < self.created_at:
            raise InvalidModelError(IngestionJobErrorMessages.INVALID_TRANSITION)
        if self.finished_at is not None and self.finished_at < self.created_at:
            raise InvalidModelError(IngestionJobErrorMessages.INVALID_TRANSITION)
        if (
            self.started_at is not None
            and self.finished_at is not None
            and self.finished_at < self.started_at
        ):
            raise InvalidModelError(IngestionJobErrorMessages.INVALID_TRANSITION)
        if self.next_attempt_at is not None and self.status is not IngestionJobStatus.PENDING:
            raise InvalidModelError(IngestionJobErrorMessages.INVALID_TRANSITION)
        if self.next_attempt_at is not None and self.next_attempt_at < self.scheduled_at:
            raise InvalidModelError(IngestionJobErrorMessages.INVALID_TRANSITION)
        if self.status is IngestionJobStatus.RUNNING:
            if (
                not self.lease_owner
                or self.lease_expires_at is None
                or self.started_at is None
                or self.attempts < 1
            ):
                raise InvalidModelError(IngestionJobErrorMessages.LEASE_ONLY_RUNNING)
        elif self.lease_owner is not None or self.lease_expires_at is not None:
            raise InvalidModelError(IngestionJobErrorMessages.LEASE_ONLY_RUNNING)
        if (
            self.status
            in {
                IngestionJobStatus.SUCCEEDED,
                IngestionJobStatus.FAILED,
                IngestionJobStatus.CANCELLED,
            }
            and self.finished_at is None
        ):
            raise InvalidModelError(IngestionJobErrorMessages.INVALID_TRANSITION)
        if self.status is IngestionJobStatus.FAILED and self.last_error_code is None:
            raise InvalidModelError(IngestionJobErrorMessages.ERROR_CODE_NOT_ALLOWLISTED)
        if self.status is IngestionJobStatus.SUCCEEDED and self.last_error_code is not None:
            raise InvalidModelError(IngestionJobErrorMessages.INVALID_TRANSITION)
        if (
            self.last_error_code is not None
            and self.last_error_code not in ALLOWED_INGESTION_ERROR_CODES
        ):
            raise InvalidModelError(IngestionJobErrorMessages.ERROR_CODE_NOT_ALLOWLISTED)
        if self.last_error_code is not None and self.status not in {
            IngestionJobStatus.PENDING,
            IngestionJobStatus.FAILED,
        }:
            raise InvalidModelError(IngestionJobErrorMessages.INVALID_TRANSITION)
        if self.next_attempt_at is not None and self.status is not IngestionJobStatus.PENDING:
            raise InvalidModelError(IngestionJobErrorMessages.INVALID_TRANSITION)
        if self.status is IngestionJobStatus.PENDING and self.finished_at is not None:
            raise InvalidModelError(IngestionJobErrorMessages.INVALID_TRANSITION)
        if self.status is IngestionJobStatus.PENDING and (
            (self.last_error_code is None) != (self.next_attempt_at is None)
        ):
            raise InvalidModelError(IngestionJobErrorMessages.INVALID_TRANSITION)
        if (
            self.status is IngestionJobStatus.PENDING
            and self.attempts == 0
            and (self.last_error_code is not None or self.next_attempt_at is not None)
        ):
            raise InvalidModelError(IngestionJobErrorMessages.INVALID_TRANSITION)
        if (
            self.status is IngestionJobStatus.PENDING
            and self.attempts > 0
            and (self.last_error_code is None or self.next_attempt_at is None)
        ):
            raise InvalidModelError(IngestionJobErrorMessages.INVALID_TRANSITION)
        if self.status is IngestionJobStatus.CANCELLED and self.last_error_code is not None:
            raise InvalidModelError(IngestionJobErrorMessages.INVALID_TRANSITION)
        if (
            self.status in {IngestionJobStatus.SUCCEEDED, IngestionJobStatus.FAILED}
            and self.attempts < 1
        ):
            raise InvalidModelError(IngestionJobErrorMessages.ATTEMPTS_INVALID)
        if (
            self.status in {IngestionJobStatus.SUCCEEDED, IngestionJobStatus.FAILED}
            and self.started_at is None
        ):
            raise InvalidModelError(IngestionJobErrorMessages.INVALID_TRANSITION)
        if self.status is IngestionJobStatus.RUNNING and self.finished_at is not None:
            raise InvalidModelError(IngestionJobErrorMessages.INVALID_TRANSITION)

    def claim(self, worker_id: str, now: datetime, lease_expires_at: datetime) -> IngestionJob:
        self._assert_worker(worker_id)
        if self.status is not IngestionJobStatus.PENDING:
            raise InvalidModelError(IngestionJobErrorMessages.INVALID_TRANSITION)
        now = _utc(now)
        lease_expires_at = _utc(lease_expires_at)
        if lease_expires_at <= now:
            raise InvalidModelError(IngestionJobErrorMessages.INVALID_TRANSITION)
        return replace(
            self,
            status=IngestionJobStatus.RUNNING,
            attempts=self.attempts + 1,
            lease_owner=worker_id,
            lease_expires_at=lease_expires_at,
            started_at=self.started_at or now,
            next_attempt_at=None,
            last_error_code=None,
        )

    def heartbeat(self, worker_id: str, now: datetime, lease_expires_at: datetime) -> IngestionJob:
        self._assert_lease(worker_id, now)
        current_lease = self.lease_expires_at
        if current_lease is None:
            raise InvalidModelError(IngestionJobErrorMessages.STALE_LEASE)
        lease_expires_at = _utc(lease_expires_at)
        if lease_expires_at <= _utc(now) or lease_expires_at <= current_lease:
            raise InvalidModelError(IngestionJobErrorMessages.INVALID_TRANSITION)
        return replace(self, lease_expires_at=lease_expires_at)

    def succeed(self, worker_id: str, now: datetime) -> IngestionJob:
        self._assert_lease(worker_id, now)
        return replace(
            self,
            status=IngestionJobStatus.SUCCEEDED,
            lease_owner=None,
            lease_expires_at=None,
            finished_at=_utc(now),
            last_error_code=None,
        )

    def retry(
        self, worker_id: str, now: datetime, next_attempt_at: datetime, error_code: str
    ) -> IngestionJob:
        self._assert_lease(worker_id, now)
        if _utc(next_attempt_at) <= _utc(now):
            raise InvalidModelError(IngestionJobErrorMessages.INVALID_TRANSITION)
        if self.attempts >= self.max_attempts:
            return self.fail(worker_id, now, error_code)
        return replace(
            self,
            status=IngestionJobStatus.PENDING,
            lease_owner=None,
            lease_expires_at=None,
            next_attempt_at=_utc(next_attempt_at),
            last_error_code=error_code,
        )

    def fail(self, worker_id: str, now: datetime, error_code: str) -> IngestionJob:
        self._assert_lease(worker_id, now)
        return replace(
            self,
            status=IngestionJobStatus.FAILED,
            lease_owner=None,
            lease_expires_at=None,
            finished_at=_utc(now),
            last_error_code=error_code,
        )

    def cancel(self, now: datetime) -> IngestionJob:
        if self.status is not IngestionJobStatus.PENDING:
            raise InvalidModelError(IngestionJobErrorMessages.INVALID_TRANSITION)
        return replace(
            self,
            status=IngestionJobStatus.CANCELLED,
            finished_at=_utc(now),
            last_error_code=None,
            next_attempt_at=None,
        )

    def reclaim(self, now: datetime, next_attempt_at: datetime) -> IngestionJob:
        if self.status is not IngestionJobStatus.RUNNING:
            raise InvalidModelError(IngestionJobErrorMessages.INVALID_TRANSITION)
        if self.lease_expires_at is None or self.lease_expires_at > _utc(now):
            raise InvalidModelError(IngestionJobErrorMessages.INVALID_TRANSITION)
        if _utc(next_attempt_at) <= _utc(now) and self.attempts < self.max_attempts:
            raise InvalidModelError(IngestionJobErrorMessages.INVALID_TRANSITION)
        if self.attempts >= self.max_attempts:
            return replace(
                self,
                status=IngestionJobStatus.FAILED,
                lease_owner=None,
                lease_expires_at=None,
                finished_at=_utc(now),
                last_error_code=LEASE_EXPIRED_MAX_ATTEMPTS_ERROR_CODE,
            )
        return replace(
            self,
            status=IngestionJobStatus.PENDING,
            lease_owner=None,
            lease_expires_at=None,
            next_attempt_at=_utc(next_attempt_at),
            last_error_code=LEASE_EXPIRED_ERROR_CODE,
        )

    def _assert_worker(self, worker_id: str) -> None:
        if not WORKER_PATTERN.fullmatch(worker_id):
            raise InvalidModelError(IngestionJobErrorMessages.WORKER_REQUIRED)

    def _assert_lease(self, worker_id: str, now: datetime) -> None:
        self._assert_worker(worker_id)
        if (
            self.status is not IngestionJobStatus.RUNNING
            or self.lease_owner != worker_id
            or self.lease_expires_at is None
            or self.lease_expires_at <= _utc(now)
        ):
            raise InvalidModelError(IngestionJobErrorMessages.STALE_LEASE)


@dataclass(frozen=True, slots=True)
class IngestionJobEvent:
    event_id: UUID
    job_id: UUID
    sequence_number: int
    kind: IngestionJobEventKind
    occurred_at: datetime
    attempt: int
    worker_id: str | None = None
    error_code: str | None = None

    def __post_init__(self) -> None:
        if self.sequence_number < 1 or self.attempt < 0:
            raise InvalidModelError(IngestionJobErrorMessages.INVALID_TRANSITION)
        _utc(self.occurred_at)
        if self.worker_id is not None and not WORKER_PATTERN.fullmatch(self.worker_id):
            raise InvalidModelError(IngestionJobErrorMessages.WORKER_REQUIRED)
        if self.error_code is not None and self.error_code not in ALLOWED_INGESTION_ERROR_CODES:
            raise InvalidModelError(IngestionJobErrorMessages.ERROR_CODE_NOT_ALLOWLISTED)
        if (
            self.kind
            in {
                IngestionJobEventKind.CLAIMED,
                IngestionJobEventKind.HEARTBEAT,
                IngestionJobEventKind.SUCCEEDED,
                IngestionJobEventKind.FAILED,
                IngestionJobEventKind.RETRIED,
            }
            and self.worker_id is None
        ):
            raise InvalidModelError(IngestionJobErrorMessages.WORKER_REQUIRED)
        if (
            self.kind
            in {
                IngestionJobEventKind.RETRIED,
                IngestionJobEventKind.FAILED,
                IngestionJobEventKind.RECLAIMED,
            }
            and self.error_code is None
        ):
            raise InvalidModelError(IngestionJobErrorMessages.ERROR_CODE_NOT_ALLOWLISTED)
        if (
            self.kind
            not in {
                IngestionJobEventKind.RETRIED,
                IngestionJobEventKind.FAILED,
                IngestionJobEventKind.RECLAIMED,
            }
            and self.error_code is not None
        ):
            raise InvalidModelError(IngestionJobErrorMessages.INVALID_TRANSITION)
