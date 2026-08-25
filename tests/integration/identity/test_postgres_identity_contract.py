import hashlib
import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import text

from cinegraph.adapters.identity import (
    ScryptPasswordHasher,
    SecureSessionTokenGenerator,
    SqlAlchemyIdentityUnitOfWorkFactory,
    create_identity_engine,
)
from cinegraph.adapters.identity.migration_runner import upgrade_identity_database
from cinegraph.adapters.persistence.sqlalchemy_ingestion_job_repository import (
    SqlAlchemyIngestionJobUnitOfWorkFactory,
)
from cinegraph.application.exceptions.errors import (
    EmailAlreadyRegisteredError,
    SessionInvalidError,
)
from cinegraph.application.models.identity_sessions import (
    AuthenticateAccountCommand,
    RegisterAccountCommand,
)
from cinegraph.application.models.ingestion_job import EnqueueIngestionJob
from cinegraph.application.service.identity_session_service import IdentitySessionService
from cinegraph.application.service.ingestion_job_service import IngestionJobService
from cinegraph.common.error_messages import IngestionJobErrorMessages
from cinegraph.config import CinegraphRuntimeSettings, IngestionJobConfiguration
from cinegraph.domain.enums.enum import IngestionJobKind, IngestionJobStatus
from cinegraph.domain.exceptions.errors import InvalidModelError


def test_postgres_migration_and_identity_roundtrip() -> None:
    url = os.environ.get("CINEGRAPH_TEST_DATABASE_URL")
    if not url:
        pytest.skip("CINEGRAPH_TEST_DATABASE_URL is not configured")
    settings = CinegraphRuntimeSettings(
        _env_file=None,
        environment="development",
        database_url=url,
        qdrant_mode="local",
    )
    upgrade_identity_database(settings)
    engine = create_identity_engine(settings)
    try:
        service = IdentitySessionService(
            SqlAlchemyIdentityUnitOfWorkFactory(engine),
            ScryptPasswordHasher(),
            SecureSessionTokenGenerator(),
            _FixedClock(),
        )
        grant = service.register(
            RegisterAccountCommand(
                email=f"contract-{uuid4()}@example.com",
                password="correct horse battery staple",
                display_name="Postgres contract",
            )
        )
        assert service.resolve(grant.token) == grant.principal
        assert grant.account is not None
        second = service.authenticate(
            AuthenticateAccountCommand(
                email=grant.account.email,
                password="correct horse battery staple",
            )
        )
        assert second.principal.user_id == grant.principal.user_id
        assert second.principal.profile_id == grant.principal.profile_id
        assert second.principal.user_id is not None
        with SqlAlchemyIdentityUnitOfWorkFactory(engine)() as unit_of_work:
            locked_account = unit_of_work.accounts.get_by_user_id(
                grant.account.user_id, for_update=True
            )
            assert locked_account == grant.account
            first_record = unit_of_work.sessions.get_by_token_sha256(
                hashlib.sha256(grant.token.encode("utf-8")).hexdigest()
            )
            second_record = unit_of_work.sessions.get_by_token_sha256(
                hashlib.sha256(second.token.encode("utf-8")).hexdigest()
            )
            assert first_record is not None
            assert second_record is not None
            listed = unit_of_work.sessions.list_active_for_user(
                grant.principal.user_id,
                grant.principal.profile_id,
                _FixedClock().now_utc(),
                20,
            )
            assert len(listed) == 2
            assert not unit_of_work.sessions.revoke_session(
                first_record.session_id,
                uuid4(),
                grant.principal.profile_id,
                _FixedClock().now_utc(),
            )
            assert unit_of_work.sessions.revoke_session(
                first_record.session_id,
                grant.principal.user_id,
                grant.principal.profile_id,
                _FixedClock().now_utc(),
            )
            assert unit_of_work.sessions.revoke_all_for_user(
                grant.principal.user_id,
                grant.principal.profile_id,
                _FixedClock().now_utc(),
            ) == 1
            expired_at = second_record.expires_at + timedelta(seconds=1)
            assert unit_of_work.sessions.revoke_session(
                second_record.session_id,
                grant.principal.user_id,
                grant.principal.profile_id,
                expired_at,
            ) is False
            assert unit_of_work.sessions.revoke_all_for_user(
                grant.principal.user_id,
                grant.principal.profile_id,
                expired_at,
            ) == 0
        with pytest.raises(EmailAlreadyRegisteredError):
            service.register(
                RegisterAccountCommand(
                    email=grant.account.email,
                    password="correct horse battery staple",
                    display_name="Duplicate",
                )
            )
        guest_grant = service.issue_guest()
        guest_principal = service.resolve(guest_grant.token)
        assert {item.season_number for item in guest_principal.corpus_access_scope.allowed_seasons} == {
            1,
            2,
        }
        service.revoke(guest_grant.token)
        with pytest.raises(SessionInvalidError):
            service.resolve(guest_grant.token)
        with engine.connect() as connection:
            assert connection.execute(text("SELECT 1")).scalar_one() == 1
            assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == (
                "0004_agent_jobs"
            )
    finally:
        engine.dispose()


class _FixedClock:
    def now_utc(self) -> datetime:
        return datetime(2026, 8, 16, 12, 0, tzinfo=UTC)


def test_postgres_ingestion_claims_leases_retries_and_events() -> None:
    url = os.environ.get("CINEGRAPH_TEST_DATABASE_URL")
    if not url:
        pytest.skip("CINEGRAPH_TEST_DATABASE_URL is not configured")
    settings = CinegraphRuntimeSettings(_env_file=None, database_url=url, qdrant_mode="local")
    upgrade_identity_database(settings)
    engine = create_identity_engine(settings)
    clock = _MutableClock()
    configuration = IngestionJobConfiguration(
        lease_duration=timedelta(minutes=1),
        heartbeat_extension=timedelta(minutes=1),
        retry_base_delay=timedelta(seconds=1),
        retry_max_delay=timedelta(seconds=2),
        default_max_attempts=2,
        claim_batch_size=1,
    )
    service = IngestionJobService(SqlAlchemyIngestionJobUnitOfWorkFactory(engine), clock, configuration)
    try:
        command = EnqueueIngestionJob(
            kind=IngestionJobKind.SPEAKER_REVIEW,
            series_id=uuid4(),
            season_number=1,
            episode_number=1,
            source_fingerprint=("b" * 64),
            pipeline_revision="postgres-contract-v1",
        )
        first = service.enqueue(command)
        assert service.enqueue(command).job_id == first.job_id
        claimed = service.claim_next("worker-a")
        assert claimed is not None
        assert service.claim_next("worker-b") is None
        assert service.heartbeat(first.job_id, "worker-a").status is IngestionJobStatus.RUNNING
        with pytest.raises(InvalidModelError, match=IngestionJobErrorMessages.STALE_LEASE):
            service.succeed(first.job_id, "worker-b")
        retried = service.fail_or_retry(first.job_id, "worker-a", "speaker_review_failed")
        assert retried.status is IngestionJobStatus.PENDING
        assert retried.next_attempt_at is not None
        clock.value = retried.next_attempt_at + timedelta(seconds=1)
        assert service.claim_next("worker-b") is not None
        failed = service.fail_or_retry(first.job_id, "worker-b", "speaker_review_failed")
        assert failed.status is IngestionJobStatus.FAILED
        assert [event.sequence_number for event in service.events(first.job_id)] == [1, 2, 3, 4, 5, 6]
    finally:
        engine.dispose()


class _MutableClock:
    def __init__(self) -> None:
        self.value = datetime(2026, 8, 16, 12, 0, tzinfo=UTC)

    def now_utc(self) -> datetime:
        return self.value
