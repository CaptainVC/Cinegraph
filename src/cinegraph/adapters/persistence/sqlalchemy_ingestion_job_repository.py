from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
    select,
    update,
)
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped, Session, mapped_column, sessionmaker

from cinegraph.adapters.persistence.base import PersistenceBase
from cinegraph.domain.enums.enum import (
    IngestionJobEventKind,
    IngestionJobKind,
    IngestionJobStatus,
)
from cinegraph.domain.models.ingestion_job import IngestionJob, IngestionJobEvent


class IngestionJobRow(PersistenceBase):
    __tablename__ = "ingestion_jobs"

    job_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    idempotency_key: Mapped[str] = mapped_column(String(64), nullable=False)
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    series_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    season_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    episode_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    pipeline_revision: Mapped[str] = mapped_column(String(128), nullable=False)
    priority: Mapped[int] = mapped_column(Integer, nullable=False)
    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False)
    lease_owner: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_ingestion_jobs_idempotency_key"),
        CheckConstraint(
            "kind IN ('speaker_review', 'transcript_ingestion', 'vector_index', 'episode_summary', 'series_metadata', 'subtitle_alignment', 'graph_claim_extraction')",
            name="ck_ingestion_jobs_kind_allowed",
        ),
        CheckConstraint(
            "status IN ('pending', 'running', 'succeeded', 'failed', 'cancelled')",
            name="ck_ingestion_jobs_status_allowed",
        ),
        CheckConstraint(
            "length(idempotency_key) = 64 AND length(source_fingerprint) = 64",
            name="ck_ingestion_jobs_sha_lengths",
        ),
        CheckConstraint(
            "season_number IS NULL OR season_number >= 1", name="ck_ingestion_jobs_season_positive"
        ),
        CheckConstraint(
            "episode_number IS NULL OR episode_number >= 1",
            name="ck_ingestion_jobs_episode_positive",
        ),
        CheckConstraint(
            "episode_number IS NULL OR season_number IS NOT NULL",
            name="ck_ingestion_jobs_episode_requires_season",
        ),
        CheckConstraint(
            "priority >= 0 AND priority <= 100", name="ck_ingestion_jobs_priority_bounds"
        ),
        CheckConstraint(
            "max_attempts >= 1 AND max_attempts <= 20", name="ck_ingestion_jobs_max_attempts_bounds"
        ),
        CheckConstraint(
            "attempts >= 0 AND attempts <= max_attempts", name="ck_ingestion_jobs_attempt_bounds"
        ),
        CheckConstraint(
            "status <> 'running' OR attempts >= 1", name="ck_ingestion_jobs_running_attempts"
        ),
        CheckConstraint(
            "status <> 'running' OR started_at IS NOT NULL",
            name="ck_ingestion_jobs_running_started",
        ),
        CheckConstraint(
            "status NOT IN ('succeeded', 'failed') OR attempts >= 1",
            name="ck_ingestion_jobs_terminal_attempts",
        ),
        CheckConstraint(
            "status NOT IN ('succeeded', 'failed') OR started_at IS NOT NULL",
            name="ck_ingestion_jobs_terminal_started",
        ),
        CheckConstraint(
            "(status = 'running' AND lease_owner IS NOT NULL AND lease_expires_at IS NOT NULL) "
            "OR (status <> 'running' AND lease_owner IS NULL AND lease_expires_at IS NULL)",
            name="ck_ingestion_jobs_lease_coherence",
        ),
        CheckConstraint(
            "status NOT IN ('succeeded', 'failed', 'cancelled') OR finished_at IS NOT NULL",
            name="ck_ingestion_jobs_terminal_finished",
        ),
        CheckConstraint(
            "status IN ('succeeded', 'failed', 'cancelled') OR finished_at IS NULL",
            name="ck_ingestion_jobs_nonterminal_unfinished",
        ),
        CheckConstraint(
            "status <> 'succeeded' OR last_error_code IS NULL",
            name="ck_ingestion_jobs_success_without_error",
        ),
        CheckConstraint(
            "status <> 'failed' OR last_error_code IS NOT NULL",
            name="ck_ingestion_jobs_failed_error_required",
        ),
        CheckConstraint(
            "status <> 'cancelled' OR last_error_code IS NULL",
            name="ck_ingestion_jobs_cancelled_without_error",
        ),
        CheckConstraint(
            "last_error_code IS NULL OR status IN ('pending', 'failed')",
            name="ck_ingestion_jobs_error_status_coherence",
        ),
        CheckConstraint(
            "next_attempt_at IS NULL OR status = 'pending'",
            name="ck_ingestion_jobs_next_attempt_status_coherence",
        ),
        CheckConstraint(
            "last_error_code IS NULL OR last_error_code IN ('lease_expired', 'lease_expired_max_attempts', 'source_invalid', 'alignment_failed', 'speaker_review_failed', 'transcript_ingestion_failed', 'vector_index_failed', 'episode_summary_failed', 'series_metadata_failed', 'graph_claim_extraction_failed', 'unknown_retryable')",
            name="ck_ingestion_jobs_error_code_allowed",
        ),
        CheckConstraint(
            "status <> 'pending' OR ((next_attempt_at IS NULL AND last_error_code IS NULL) OR (next_attempt_at IS NOT NULL AND last_error_code IS NOT NULL))",
            name="ck_ingestion_jobs_pending_retry_pair",
        ),
        CheckConstraint(
            "status <> 'pending' OR (attempts = 0 AND next_attempt_at IS NULL AND last_error_code IS NULL) OR (attempts > 0 AND next_attempt_at IS NOT NULL AND last_error_code IS NOT NULL)",
            name="ck_ingestion_jobs_pending_attempt_coherence",
        ),
        CheckConstraint(
            "next_attempt_at IS NULL OR next_attempt_at >= scheduled_at",
            name="ck_ingestion_jobs_pending_schedule",
        ),
        CheckConstraint(
            "started_at IS NULL OR started_at >= created_at",
            name="ck_ingestion_jobs_started_after_created",
        ),
        CheckConstraint(
            "finished_at IS NULL OR finished_at >= created_at",
            name="ck_ingestion_jobs_finished_after_created",
        ),
        CheckConstraint(
            "finished_at IS NULL OR started_at IS NULL OR finished_at >= started_at",
            name="ck_ingestion_jobs_finished_after_started",
        ),
        Index(
            "ix_ingestion_jobs_claim",
            "status",
            "priority",
            "scheduled_at",
            "next_attempt_at",
            "created_at",
        ),
        Index("ix_ingestion_jobs_lease_expiry", "status", "lease_expires_at"),
        Index("ix_ingestion_jobs_scope", "series_id", "season_number", "episode_number"),
    )


class IngestionJobEventRow(PersistenceBase):
    __tablename__ = "ingestion_job_events"

    event_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    job_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("ingestion_jobs.job_id", name="fk_ingestion_job_events_job"),
        nullable=False,
    )
    sequence_number: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False)
    worker_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    __table_args__ = (
        UniqueConstraint("job_id", "sequence_number", name="uq_ingestion_job_events_sequence"),
        Index("ix_ingestion_job_events_job_order", "job_id", "sequence_number"),
        CheckConstraint("sequence_number >= 1", name="ck_ingestion_job_events_sequence_positive"),
        CheckConstraint("attempt >= 0", name="ck_ingestion_job_events_attempt_nonnegative"),
        CheckConstraint(
            "kind IN ('enqueued', 'claimed', 'heartbeat', 'retried', 'succeeded', 'failed', 'cancelled', 'reclaimed')",
            name="ck_ingestion_job_events_kind_allowed",
        ),
        CheckConstraint(
            "kind NOT IN ('claimed', 'heartbeat', 'succeeded', 'failed', 'retried') OR worker_id IS NOT NULL",
            name="ck_ingestion_job_events_worker_required",
        ),
        CheckConstraint(
            "kind IN ('retried', 'failed', 'reclaimed') AND error_code IS NOT NULL OR kind NOT IN ('retried', 'failed', 'reclaimed') AND error_code IS NULL",
            name="ck_ingestion_job_events_error_coherence",
        ),
        CheckConstraint(
            "error_code IS NULL OR error_code IN ('lease_expired', 'lease_expired_max_attempts', 'source_invalid', 'alignment_failed', 'speaker_review_failed', 'transcript_ingestion_failed', 'vector_index_failed', 'episode_summary_failed', 'series_metadata_failed', 'graph_claim_extraction_failed', 'unknown_retryable')",
            name="ck_ingestion_job_events_error_code_allowed",
        ),
    )


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _job_from_row(row: IngestionJobRow) -> IngestionJob:
    return IngestionJob(
        job_id=row.job_id,
        idempotency_key=row.idempotency_key,
        kind=IngestionJobKind(row.kind),
        status=IngestionJobStatus(row.status),
        series_id=row.series_id,
        season_number=row.season_number,
        episode_number=row.episode_number,
        source_fingerprint=row.source_fingerprint,
        pipeline_revision=row.pipeline_revision,
        priority=row.priority,
        scheduled_at=_utc(row.scheduled_at),
        max_attempts=row.max_attempts,
        attempts=row.attempts,
        lease_owner=row.lease_owner,
        lease_expires_at=_utc(row.lease_expires_at) if row.lease_expires_at else None,
        created_at=_utc(row.created_at),
        started_at=_utc(row.started_at) if row.started_at else None,
        finished_at=_utc(row.finished_at) if row.finished_at else None,
        last_error_code=row.last_error_code,
        next_attempt_at=_utc(row.next_attempt_at) if row.next_attempt_at else None,
    )


def _event_from_row(row: IngestionJobEventRow) -> IngestionJobEvent:
    return IngestionJobEvent(
        event_id=row.event_id,
        job_id=row.job_id,
        sequence_number=row.sequence_number,
        kind=IngestionJobEventKind(row.kind),
        occurred_at=_utc(row.occurred_at),
        attempt=row.attempt,
        worker_id=row.worker_id,
        error_code=row.error_code,
    )


class SqlAlchemyIngestionJobRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get_by_idempotency_key(self, key: str) -> IngestionJob | None:
        row = self._session.scalar(
            select(IngestionJobRow).where(IngestionJobRow.idempotency_key == key)
        )
        return _job_from_row(row) if row else None

    def get(self, job_id: UUID) -> IngestionJob | None:
        row = self._session.get(IngestionJobRow, job_id)
        return _job_from_row(row) if row else None

    def add(self, job: IngestionJob) -> IngestionJob:
        try:
            with self._session.begin_nested():
                self._session.add(_row_from_job(job))
                self._session.flush()
        except IntegrityError as error:
            if not _is_idempotency_conflict(error):
                raise
            winner = self.get_by_idempotency_key(job.idempotency_key)
            if winner is None:
                raise
            return winner
        return job

    def save_owned(
        self,
        job: IngestionJob,
        worker_id: str,
        now: datetime,
        expected_lease_expires_at: datetime,
    ) -> None:
        result = self._session.execute(
            update(IngestionJobRow)
            .where(
                IngestionJobRow.job_id == job.job_id,
                IngestionJobRow.status == IngestionJobStatus.RUNNING.value,
                IngestionJobRow.lease_owner == worker_id,
                IngestionJobRow.lease_expires_at == _utc(expected_lease_expires_at),
                IngestionJobRow.lease_expires_at > _utc(now),
            )
            .values(**_row_values(job))
            .execution_options(synchronize_session=False)
        )
        if getattr(result, "rowcount", 0) != 1:
            raise ValueError("The worker lease is no longer valid for this ingestion job.")
        self._session.flush()

    def save_pending(self, job: IngestionJob, now: datetime) -> None:
        result = self._session.execute(
            update(IngestionJobRow)
            .where(
                IngestionJobRow.job_id == job.job_id,
                IngestionJobRow.status == IngestionJobStatus.PENDING.value,
            )
            .values(**_row_values(job))
            .execution_options(synchronize_session=False)
        )
        if getattr(result, "rowcount", 0) != 1:
            raise ValueError("The pending ingestion job changed before cancellation.")
        self._session.flush()

    def save_reclaimed(
        self, job: IngestionJob, now: datetime, expected_lease_expires_at: datetime
    ) -> None:
        result = self._session.execute(
            update(IngestionJobRow)
            .where(
                IngestionJobRow.job_id == job.job_id,
                IngestionJobRow.status == IngestionJobStatus.RUNNING.value,
                IngestionJobRow.lease_expires_at == _utc(expected_lease_expires_at),
                IngestionJobRow.lease_expires_at <= _utc(now),
            )
            .values(**_row_values(job))
            .execution_options(synchronize_session=False)
        )
        if getattr(result, "rowcount", 0) != 1:
            raise ValueError("The ingestion job lease changed before reclaim.")
        self._session.flush()

    def claim_next(
        self, worker_id: str, now: datetime, lease_expires_at: datetime
    ) -> IngestionJob | None:
        now = _utc(now)
        statement = (
            select(IngestionJobRow)
            .where(
                IngestionJobRow.status == IngestionJobStatus.PENDING.value,
                IngestionJobRow.scheduled_at <= now,
                (
                    IngestionJobRow.next_attempt_at.is_(None)
                    | (IngestionJobRow.next_attempt_at <= now)
                ),
            )
            .order_by(
                IngestionJobRow.priority.desc(),
                IngestionJobRow.scheduled_at,
                IngestionJobRow.created_at,
                IngestionJobRow.job_id,
            )
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        row = self._session.scalar(statement)
        if row is None:
            return None
        job = _job_from_row(row).claim(worker_id, now, lease_expires_at)
        result = self._session.execute(
            update(IngestionJobRow)
            .where(
                IngestionJobRow.job_id == job.job_id,
                IngestionJobRow.status == IngestionJobStatus.PENDING.value,
                IngestionJobRow.scheduled_at <= now,
                (
                    IngestionJobRow.next_attempt_at.is_(None)
                    | (IngestionJobRow.next_attempt_at <= now)
                ),
            )
            .values(**_row_values(job))
            .execution_options(synchronize_session=False)
        )
        if getattr(result, "rowcount", 0) != 1:
            return None
        self._session.flush()
        return job

    def events(self, job_id: UUID) -> tuple[IngestionJobEvent, ...]:
        rows = self._session.scalars(
            select(IngestionJobEventRow)
            .where(IngestionJobEventRow.job_id == job_id)
            .order_by(IngestionJobEventRow.sequence_number)
        )
        return tuple(_event_from_row(row) for row in rows)

    def append_event(self, event: IngestionJobEvent) -> None:
        self._session.add(
            IngestionJobEventRow(
                event_id=event.event_id,
                job_id=event.job_id,
                sequence_number=event.sequence_number,
                kind=event.kind.value,
                occurred_at=_utc(event.occurred_at),
                attempt=event.attempt,
                worker_id=event.worker_id,
                error_code=event.error_code,
            )
        )
        self._session.flush()


class SqlAlchemyIngestionJobUnitOfWork:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory
        self._session: Session | None = None
        self.jobs: SqlAlchemyIngestionJobRepository

    def __enter__(self) -> "SqlAlchemyIngestionJobUnitOfWork":
        if self._session is not None:
            raise RuntimeError("Ingestion job unit of work cannot be re-entered.")
        self._session = self._session_factory()
        self._session.begin()
        self.jobs = SqlAlchemyIngestionJobRepository(self._session)
        return self

    def __exit__(self, exception_type: object, exception: object, traceback: object) -> None:
        if self._session is None:
            return
        try:
            if self._session.in_transaction():
                self._session.rollback()
        finally:
            self._session.close()
            self._session = None

    def commit(self) -> None:
        if self._session is None:
            raise RuntimeError("Ingestion job unit of work is not active.")
        self._session.commit()

    def rollback(self) -> None:
        if self._session is not None:
            self._session.rollback()


class SqlAlchemyIngestionJobUnitOfWorkFactory:
    def __init__(self, engine: Engine) -> None:
        self._session_factory = sessionmaker(bind=engine, autobegin=False, expire_on_commit=False)

    def __call__(self) -> SqlAlchemyIngestionJobUnitOfWork:
        return SqlAlchemyIngestionJobUnitOfWork(self._session_factory)


def _row_from_job(job: IngestionJob) -> IngestionJobRow:
    return IngestionJobRow(**_row_values(job))


def _row_values(job: IngestionJob) -> dict[str, object]:
    return {
        "job_id": job.job_id,
        "idempotency_key": job.idempotency_key,
        "kind": job.kind.value,
        "status": job.status.value,
        "series_id": job.series_id,
        "season_number": job.season_number,
        "episode_number": job.episode_number,
        "source_fingerprint": job.source_fingerprint,
        "pipeline_revision": job.pipeline_revision,
        "priority": job.priority,
        "scheduled_at": _utc(job.scheduled_at),
        "max_attempts": job.max_attempts,
        "attempts": job.attempts,
        "lease_owner": job.lease_owner,
        "lease_expires_at": _utc(job.lease_expires_at) if job.lease_expires_at else None,
        "created_at": _utc(job.created_at),
        "started_at": _utc(job.started_at) if job.started_at else None,
        "finished_at": _utc(job.finished_at) if job.finished_at else None,
        "last_error_code": job.last_error_code,
        "next_attempt_at": _utc(job.next_attempt_at) if job.next_attempt_at else None,
    }


def _is_idempotency_conflict(error: IntegrityError) -> bool:
    diagnostic = getattr(getattr(error, "orig", None), "diag", None)
    constraint_name = getattr(diagnostic, "constraint_name", None)
    if constraint_name == "uq_ingestion_jobs_idempotency_key":
        return True
    message = str(getattr(error, "orig", error)).lower()
    return (
        "uq_ingestion_jobs_idempotency_key" in message
        or "unique constraint failed: ingestion_jobs.idempotency_key" in message
        or "ingestion_jobs_pkey" in message
        or "unique constraint failed: ingestion_jobs.job_id" in message
    )
