from datetime import datetime
from typing import Protocol
from uuid import UUID

from cinegraph.domain.models.ingestion_job import IngestionJob, IngestionJobEvent


class IngestionJobRepository(Protocol):
    def get_by_idempotency_key(self, key: str) -> IngestionJob | None: ...

    def get(self, job_id: UUID) -> IngestionJob | None: ...

    def add(self, job: IngestionJob) -> IngestionJob: ...

    def save_owned(
        self,
        job: IngestionJob,
        worker_id: str,
        now: datetime,
        expected_lease_expires_at: datetime,
    ) -> None: ...

    def save_pending(self, job: IngestionJob, now: datetime) -> None: ...

    def save_reclaimed(
        self, job: IngestionJob, now: datetime, expected_lease_expires_at: datetime
    ) -> None: ...

    def claim_next(
        self,
        worker_id: str,
        now: datetime,
        lease_expires_at: datetime,
    ) -> IngestionJob | None: ...

    def events(self, job_id: UUID) -> tuple[IngestionJobEvent, ...]: ...

    def append_event(self, event: IngestionJobEvent) -> None: ...


class IngestionJobUnitOfWork(Protocol):
    jobs: IngestionJobRepository

    def __enter__(self) -> "IngestionJobUnitOfWork": ...

    def __exit__(self, exception_type: object, exception: object, traceback: object) -> None: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...


class IngestionJobUnitOfWorkFactory(Protocol):
    def __call__(self) -> IngestionJobUnitOfWork: ...
