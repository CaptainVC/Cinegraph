import os
from datetime import UTC, datetime
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
from cinegraph.application.exceptions.errors import (
    EmailAlreadyRegisteredError,
    SessionInvalidError,
)
from cinegraph.application.models.identity_sessions import RegisterAccountCommand
from cinegraph.application.service.identity_session_service import IdentitySessionService
from cinegraph.config import CinegraphRuntimeSettings


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
                "0001_identity_schema"
            )
    finally:
        engine.dispose()


class _FixedClock:
    def now_utc(self) -> datetime:
        return datetime(2026, 8, 16, 12, 0, tzinfo=UTC)
