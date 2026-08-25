"""Durable SQL repository for asynchronous agent jobs."""

from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from uuid import UUID, uuid5

import sqlalchemy as sa
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import DBAPIError, IntegrityError, OperationalError
from sqlalchemy.orm import Mapped, Session, mapped_column, sessionmaker

from cinegraph.adapters.persistence.agent_job_serialization import (
    episode_from_json,
    episode_to_json,
    result_from_json,
    result_to_json,
    scope_from_json,
    scope_to_json,
)
from cinegraph.adapters.persistence.base import PersistenceBase
from cinegraph.application.models.agent_job import (
    AgentJob,
    AgentJobEvent,
    AgentJobEventKind,
    AgentJobStatus,
)
from cinegraph.application.models.agent_runtime import ALLOWED_AGENT_JOB_FAILURE_CODES
from cinegraph.application.models.series_agent_result import SeriesAgentResult
from cinegraph.application.serialization.agent_job_payload import result_event_payload
from cinegraph.common.error_messages import AgentJobErrorMessages
from cinegraph.ports.agent_jobs.agent_job_repository import (
    AgentJobIdempotencyConflictError,
    AgentJobTransitionError,
    AgentJobUnavailableError,
)

_FAILURE_CODE_SQL = ",".join(f"'{code}'" for code in sorted(ALLOWED_AGENT_JOB_FAILURE_CODES))


class AgentJobRow(PersistenceBase):
    __tablename__ = "agent_jobs"
    job_id: Mapped[UUID] = mapped_column(sa.Uuid(as_uuid=True), primary_key=True)
    owner_profile_id: Mapped[UUID] = mapped_column(sa.Uuid(as_uuid=True), nullable=False)
    thread_id: Mapped[UUID] = mapped_column(sa.Uuid(as_uuid=True), nullable=False)
    series_id: Mapped[UUID] = mapped_column(sa.Uuid(as_uuid=True), nullable=False)
    question_json: Mapped[dict] = mapped_column(sa.JSON(none_as_null=True), nullable=False)
    candidate_episodes_json: Mapped[list] = mapped_column(
        sa.JSON(none_as_null=True), nullable=False
    )
    corpus_access_scope_json: Mapped[dict] = mapped_column(
        sa.JSON(none_as_null=True), nullable=False
    )
    permission_scope_revision: Mapped[str] = mapped_column(sa.String(128), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(sa.String(36), nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    request_id: Mapped[str | None] = mapped_column(sa.String(64))
    created_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    status: Mapped[str] = mapped_column(sa.String(24), nullable=False)
    result_json: Mapped[dict | None] = mapped_column(sa.JSON(none_as_null=True))
    error_code: Mapped[str | None] = mapped_column(sa.String(80))
    __table_args__ = (
        sa.UniqueConstraint("owner_profile_id", "idempotency_key", name="uq_agent_jobs_owner_key"),
        sa.CheckConstraint(
            "status IN ('queued','running','succeeded','safe_refusal','failed')",
            name="ck_agent_jobs_status_allowed",
        ),
        sa.CheckConstraint(
            "length(idempotency_key) = 36 AND length(request_fingerprint) = 64",
            name="ck_agent_jobs_identity_nonempty",
        ),
        sa.CheckConstraint(
            "(status = 'queued' AND started_at IS NULL AND finished_at IS NULL AND result_json IS NULL AND error_code IS NULL) OR (status = 'running' AND started_at IS NOT NULL AND finished_at IS NULL AND result_json IS NULL AND error_code IS NULL) OR (status IN ('succeeded','safe_refusal') AND started_at IS NOT NULL AND finished_at IS NOT NULL AND result_json IS NOT NULL AND error_code IS NULL) OR (status = 'failed' AND finished_at IS NOT NULL AND result_json IS NULL AND error_code IS NOT NULL)",
            name="ck_agent_jobs_state_coherent",
        ),
        sa.CheckConstraint(
            "started_at IS NULL OR started_at >= created_at",
            name="ck_agent_jobs_started_after_created",
        ),
        sa.CheckConstraint(
            "finished_at IS NULL OR finished_at >= created_at",
            name="ck_agent_jobs_finished_after_created",
        ),
        sa.CheckConstraint(
            "finished_at IS NULL OR started_at IS NULL OR finished_at >= started_at",
            name="ck_agent_jobs_finished_after_started",
        ),
        sa.CheckConstraint(
            f"error_code IS NULL OR error_code IN ({_FAILURE_CODE_SQL})",
            name="ck_agent_jobs_error_code_allowed",
        ),
        sa.Index("ix_agent_jobs_owner_status_created", "owner_profile_id", "status", "created_at"),
        sa.Index("ix_agent_jobs_status_created", "status", "created_at"),
    )


class AgentJobEventRow(PersistenceBase):
    __tablename__ = "agent_job_events"
    event_id: Mapped[UUID] = mapped_column(sa.Uuid(as_uuid=True), primary_key=True)
    job_id: Mapped[UUID] = mapped_column(sa.Uuid(as_uuid=True), nullable=False)
    sequence: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    kind: Mapped[str] = mapped_column(sa.String(24), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)
    payload_json: Mapped[dict] = mapped_column(sa.JSON(none_as_null=True), nullable=False)
    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["job_id"], ["agent_jobs.job_id"], ondelete="CASCADE", name="fk_agent_job_events_job"
        ),
        sa.UniqueConstraint("job_id", "sequence", name="uq_agent_job_events_sequence"),
        sa.CheckConstraint("sequence >= 1", name="ck_agent_job_events_sequence_positive"),
        sa.CheckConstraint(
            "kind IN ('queued','running','succeeded','safe_refusal','failed')",
            name="ck_agent_job_events_kind_allowed",
        ),
        sa.Index("ix_agent_job_events_job_sequence", "job_id", "sequence"),
    )


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _job(row: AgentJobRow) -> AgentJob:
    if (
        not isinstance(row.question_json, dict)
        or set(row.question_json) != {"question"}
        or not isinstance(row.question_json["question"], str)
    ):
        raise ValueError("malformed persisted agent job")
    if not isinstance(row.candidate_episodes_json, list):
        raise ValueError("malformed persisted agent job")
    candidates = tuple(episode_from_json(x) for x in row.candidate_episodes_json)
    result = None if row.result_json is None else result_from_json(row.result_json)
    if result is not None and any(c.episode not in candidates for c in result.citations):
        raise ValueError("malformed persisted agent job")
    return AgentJob(
        job_id=row.job_id,
        owner_profile_id=row.owner_profile_id,
        thread_id=row.thread_id,
        series_id=row.series_id,
        question=row.question_json["question"],
        candidate_episodes=candidates,
        corpus_access_scope=scope_from_json(row.corpus_access_scope_json),
        permission_scope_revision=row.permission_scope_revision,
        idempotency_key=row.idempotency_key,
        request_fingerprint=row.request_fingerprint,
        request_id=row.request_id,
        created_at=_utc(row.created_at),
        status=AgentJobStatus(row.status),
        started_at=_utc(row.started_at) if row.started_at else None,
        finished_at=_utc(row.finished_at) if row.finished_at else None,
        result=result,
        error_code=row.error_code,
    )


def _event(row: AgentJobEventRow) -> AgentJobEvent:
    from cinegraph.adapters.persistence.agent_job_serialization import event_from_json

    return event_from_json(
        {
            "event_id": str(row.event_id),
            "job_id": str(row.job_id),
            "sequence": row.sequence,
            "kind": row.kind,
            "occurred_at": _utc(row.occurred_at).isoformat(),
            "payload": row.payload_json,
        }
    )


class SqlAlchemyAgentJobRepository:
    def __init__(self, engine: sa.Engine, clock: Callable[[], datetime] | None = None) -> None:
        self._session_factory = sessionmaker(bind=engine, expire_on_commit=False)
        self._clock = clock or (lambda: datetime.now(UTC))

    def create(self, job: AgentJob) -> tuple[AgentJob, bool]:
        try:
            with self._session_factory.begin() as session:
                row = AgentJobRow(
                    job_id=job.job_id,
                    owner_profile_id=job.owner_profile_id,
                    thread_id=job.thread_id,
                    series_id=job.series_id,
                    question_json={"question": job.question},
                    candidate_episodes_json=[episode_to_json(x) for x in job.candidate_episodes],
                    corpus_access_scope_json=scope_to_json(job.corpus_access_scope),
                    permission_scope_revision=job.permission_scope_revision,
                    idempotency_key=job.idempotency_key,
                    request_fingerprint=job.request_fingerprint,
                    request_id=job.request_id,
                    created_at=_utc(job.created_at),
                    status=job.status.value,
                    started_at=None,
                    finished_at=None,
                    result_json=None,
                    error_code=None,
                )
                session.add(row)
                session.flush()
                self._append(
                    session, job, AgentJobEventKind.QUEUED, {"status": "queued"}, 1, job.created_at
                )
                return job, True
        except IntegrityError as error:
            diagnostic = str(getattr(error, "orig", error)).lower()
            if (
                "owner_profile_id" not in diagnostic
                and "idempotency_key" not in diagnostic
                and "agent_jobs.job_id" not in diagnostic
                and "agent_jobs_pkey" not in diagnostic
            ):
                raise AgentJobUnavailableError(AgentJobErrorMessages.SYSTEM_UNAVAILABLE) from error
            with self._session_factory() as session:
                existing = session.scalar(
                    sa.select(AgentJobRow).where(
                        AgentJobRow.owner_profile_id == job.owner_profile_id,
                        AgentJobRow.idempotency_key == job.idempotency_key,
                    )
                )
                if existing is None:
                    raise AgentJobUnavailableError(AgentJobErrorMessages.SYSTEM_UNAVAILABLE)
                if existing.request_fingerprint != job.request_fingerprint:
                    raise AgentJobIdempotencyConflictError(
                        AgentJobErrorMessages.IDEMPOTENCY_CONFLICT
                    )
                try:
                    return _job(existing), False
                except (TypeError, ValueError, KeyError) as corrupt:
                    raise AgentJobUnavailableError(
                        AgentJobErrorMessages.SYSTEM_UNAVAILABLE
                    ) from corrupt
        except (OperationalError, DBAPIError, ValueError, TypeError, KeyError) as error:
            raise AgentJobUnavailableError(AgentJobErrorMessages.SYSTEM_UNAVAILABLE) from error

    create_or_get = create

    def get(self, job_id: UUID, owner_profile_id: UUID | None = None) -> AgentJob | None:
        try:
            with self._session_factory() as session:
                row = session.scalar(
                    sa.select(AgentJobRow).where(
                        AgentJobRow.job_id == job_id,
                        *(
                            ()
                            if owner_profile_id is None
                            else (AgentJobRow.owner_profile_id == owner_profile_id,)
                        ),
                    )
                )
                return _job(row) if row else None
        except (OperationalError, DBAPIError, ValueError, TypeError, KeyError) as error:
            raise AgentJobUnavailableError(AgentJobErrorMessages.SYSTEM_UNAVAILABLE) from error

    def _transition(
        self,
        job_id: UUID,
        owner: UUID | None,
        action: str,
        result: SeriesAgentResult | None = None,
        error_code: str | None = None,
    ) -> AgentJob | None:
        try:
            with self._session_factory.begin() as session:
                row = session.scalar(
                    sa.select(AgentJobRow)
                    .where(
                        AgentJobRow.job_id == job_id,
                        *(() if owner is None else (AgentJobRow.owner_profile_id == owner,)),
                    )
                    .with_for_update()
                )
                if row is None:
                    return None
                try:
                    prior = _job(row)
                except (TypeError, ValueError, KeyError) as corrupt:
                    raise AgentJobUnavailableError(
                        AgentJobErrorMessages.SYSTEM_UNAVAILABLE
                    ) from corrupt
                now = self._clock()
                if now.tzinfo != UTC or now.utcoffset() is None:
                    raise ValueError("repository clock must return UTC")
                payload: Mapping[str, object]
                expected_status: str
                if action == "claim":
                    expected_status = "queued"
                    if prior.status.value == "running":
                        return None
                    if prior.status.terminal:
                        raise AgentJobTransitionError(
                            AgentJobErrorMessages.REPOSITORY_TERMINAL_CLAIM
                        )
                    updated = prior.start(now)
                    kind = AgentJobEventKind.RUNNING
                    payload = {"status": "running"}
                elif action == "complete":
                    expected_status = "running"
                    if result is None:
                        raise AgentJobTransitionError(
                            AgentJobErrorMessages.REPOSITORY_COMPLETE_STATE
                        )
                    if prior.status.value != "running":
                        raise AgentJobTransitionError(
                            AgentJobErrorMessages.REPOSITORY_COMPLETE_STATE
                        )
                    updated = prior.complete(result, now)
                    kind = (
                        AgentJobEventKind.SAFE_REFUSAL
                        if updated.status.value == "safe_refusal"
                        else AgentJobEventKind.SUCCEEDED
                    )
                    payload = result_event_payload(result)
                elif action == "fail":
                    expected_status = "running"
                    if prior.status.value != "running":
                        raise AgentJobTransitionError(AgentJobErrorMessages.REPOSITORY_FAIL_STATE)
                    updated = prior.fail(error_code or AgentJobErrorMessages.EXECUTION_FAILED, now)
                    kind = AgentJobEventKind.FAILED
                    payload = {"status": "failed", "error_code": updated.error_code or ""}
                else:
                    expected_status = "queued"
                    if prior.status.value != "queued":
                        raise AgentJobTransitionError(AgentJobErrorMessages.REPOSITORY_REJECT_STATE)
                    updated = prior.reject(
                        error_code or AgentJobErrorMessages.DISPATCH_UNAVAILABLE, now
                    )
                    kind = AgentJobEventKind.FAILED
                    payload = {"status": "failed", "error_code": updated.error_code or ""}
                changed = session.execute(
                    sa.update(AgentJobRow)
                    .where(
                        AgentJobRow.job_id == job_id,
                        AgentJobRow.status == expected_status,
                    )
                    .values(
                        status=updated.status.value,
                        started_at=updated.started_at,
                        finished_at=updated.finished_at,
                        result_json=result_to_json(updated.result) if updated.result else None,
                        error_code=updated.error_code,
                    )
                )
                if not isinstance(changed, CursorResult) or changed.rowcount != 1:
                    return None if action == "claim" else None
                events = list(
                    session.scalars(
                        sa.select(AgentJobEventRow)
                        .where(AgentJobEventRow.job_id == job_id)
                        .order_by(AgentJobEventRow.sequence)
                    )
                )
                if not events or [e.sequence for e in events] != list(range(1, len(events) + 1)):
                    raise AgentJobUnavailableError(AgentJobErrorMessages.SYSTEM_UNAVAILABLE)
                next_sequence = len(events) + 1
                previous_time = _utc(events[-1].occurred_at)
                occurred = max(_utc(now), previous_time)
                self._append(
                    session,
                    updated,
                    kind,
                    payload,
                    next_sequence,
                    occurred,
                )
                return updated
        except (OperationalError, DBAPIError, IntegrityError) as error:
            raise AgentJobUnavailableError(AgentJobErrorMessages.SYSTEM_UNAVAILABLE) from error

    def claim_with_event(
        self, job_id: UUID, owner_profile_id: UUID | None = None
    ) -> AgentJob | None:
        return self._transition(job_id, owner_profile_id, "claim")

    def complete_with_event(
        self, job_id: UUID, result: SeriesAgentResult, owner_profile_id: UUID | None = None
    ) -> AgentJob | None:
        return self._transition(job_id, owner_profile_id, "complete", result=result)

    def fail_with_event(
        self, job_id: UUID, error_code: str, owner_profile_id: UUID | None = None
    ) -> AgentJob | None:
        return self._transition(job_id, owner_profile_id, "fail", error_code=error_code)

    def reject_with_event(
        self, job_id: UUID, error_code: str, owner_profile_id: UUID | None = None
    ) -> AgentJob | None:
        return self._transition(job_id, owner_profile_id, "reject", error_code=error_code)

    def list_events_after(
        self, job_id: UUID, sequence: int = 0, owner_profile_id: UUID | None = None
    ) -> tuple[AgentJobEvent, ...]:
        if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 0:
            raise ValueError(AgentJobErrorMessages.REPOSITORY_CURSOR)
        try:
            with self._session_factory() as session:
                owned = sa.select(AgentJobRow.job_id).where(AgentJobRow.job_id == job_id)
                if owner_profile_id is not None:
                    owned = owned.where(AgentJobRow.owner_profile_id == owner_profile_id)
                if session.scalar(owned) is None:
                    return ()
                rows = list(
                    session.scalars(
                        sa.select(AgentJobEventRow)
                        .where(
                            AgentJobEventRow.job_id == job_id, AgentJobEventRow.sequence > sequence
                        )
                        .order_by(AgentJobEventRow.sequence)
                    )
                )
                if [r.sequence for r in rows] != list(
                    range(sequence + 1, sequence + 1 + len(rows))
                ):
                    raise ValueError("corrupt event sequence")
                events = tuple(_event(r) for r in rows)
                if events and any(event.job_id != job_id for event in events):
                    raise ValueError("corrupt event job")
                return events
        except (OperationalError, DBAPIError, ValueError, TypeError, KeyError) as error:
            raise AgentJobUnavailableError(AgentJobErrorMessages.SYSTEM_UNAVAILABLE) from error

    events_after = list_events_after

    @staticmethod
    def _append(
        session: Session,
        job: AgentJob,
        kind: AgentJobEventKind,
        payload: Mapping[str, object],
        sequence: int,
        occurred: datetime,
    ) -> None:
        session.add(
            AgentJobEventRow(
                event_id=uuid5(job.job_id, f"agent-job-event:{sequence}"),
                job_id=job.job_id,
                sequence=sequence,
                kind=kind.value,
                occurred_at=_utc(occurred),
                payload_json=payload,
            )
        )
        session.flush()
