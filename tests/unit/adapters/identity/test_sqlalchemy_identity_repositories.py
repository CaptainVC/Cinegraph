import hashlib
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError

from cinegraph.adapters.date_time.system_clock import SystemClock
from cinegraph.adapters.identity import (
    ScryptPasswordHasher,
    SecureSessionTokenGenerator,
    SqlAlchemyIdentityUnitOfWorkFactory,
    create_identity_engine,
)
from cinegraph.adapters.identity.migration_runner import (
    downgrade_identity_database,
    upgrade_identity_database,
)
from cinegraph.adapters.identity.sqlalchemy_identity_repositories import (
    IdentityBase,
    _is_email_unique_conflict,
)
from cinegraph.application.exceptions.errors import (
    EmailAlreadyRegisteredError,
    SessionInvalidError,
)
from cinegraph.application.models.identity_sessions import RegisterAccountCommand
from cinegraph.application.service.identity_session_service import IdentitySessionService
from cinegraph.config import CinegraphRuntimeSettings
from cinegraph.domain.enums.enum import AccountStatus
from cinegraph.domain.models.identity import UserAccount


def _settings(path: Path) -> CinegraphRuntimeSettings:
    return CinegraphRuntimeSettings(_env_file=None, identity_database_path=path)


def _service(settings: CinegraphRuntimeSettings) -> tuple[IdentitySessionService, object]:
    upgrade_identity_database(settings)
    engine = create_identity_engine(settings)
    service = IdentitySessionService(
        SqlAlchemyIdentityUnitOfWorkFactory(engine),
        ScryptPasswordHasher(),
        SecureSessionTokenGenerator(),
        SystemClock(),
    )
    return service, engine


def test_sqlalchemy_identity_roundtrip_and_restart_preserves_revocation(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path / "identity.sqlite3")
    service, first_engine = _service(settings)
    grant = service.register(
        RegisterAccountCommand(
            email=" Viewer@Example.COM ",
            password="correct horse battery staple",
            display_name="Viewer",
        )
    )
    assert service.resolve(grant.token) == grant.principal
    first_engine.dispose()

    second_service, second_engine = _service(settings)
    try:
        assert second_service.resolve(grant.token) == grant.principal
        second_service.revoke(grant.token)
        with pytest.raises(SessionInvalidError):
            second_service.resolve(grant.token)
    finally:
        second_engine.dispose()

    third_service, third_engine = _service(settings)
    try:
        with pytest.raises(SessionInvalidError):
            third_service.resolve(grant.token)
    finally:
        third_engine.dispose()


def test_sqlalchemy_guest_entitlements_are_normalized_and_ordered(tmp_path: Path) -> None:
    service, engine = _service(_settings(tmp_path / "identity.sqlite3"))
    try:
        grant = service.issue_guest()
        with SqlAlchemyIdentityUnitOfWorkFactory(engine)() as unit_of_work:
            stored = unit_of_work.sessions.get_by_token_sha256(
                hashlib.sha256(grant.token.encode("utf-8")).hexdigest()
            )
        assert stored is not None
        assert tuple(
            (item.series_id, item.season_number)
            for item in sorted(stored.principal.corpus_access_scope.allowed_seasons)
        ) == tuple(
            (item.series_id, item.season_number)
            for item in sorted(grant.principal.corpus_access_scope.allowed_seasons)
        )
    finally:
        engine.dispose()
def test_sqlalchemy_uow_rollback_does_not_persist_partial_account(tmp_path: Path) -> None:
    settings = _settings(tmp_path / "identity.sqlite3")
    upgrade_identity_database(settings)
    engine = create_identity_engine(settings)
    factory = SqlAlchemyIdentityUnitOfWorkFactory(engine)
    try:
        with pytest.raises(RuntimeError):
            with factory() as unit_of_work:
                account = unit_of_work.accounts
                account.add(
                    UserAccount(
                        user_id=uuid4(),
                        profile_id=uuid4(),
                        email="rollback@example.com",
                        display_name="Rollback",
                        password_hash="hash",
                        status=AccountStatus.ACTIVE,
                        created_at=datetime(2026, 8, 16, tzinfo=UTC),
                    )
                )
                raise RuntimeError("abort command")
        with factory() as unit_of_work:
            assert unit_of_work.accounts.get_by_email("rollback@example.com") is None
    finally:
        engine.dispose()


def test_sqlalchemy_duplicate_email_maps_only_email_conflict(tmp_path: Path) -> None:
    settings = _settings(tmp_path / "identity.sqlite3")
    service, engine = _service(settings)
    try:
        command = RegisterAccountCommand(
            email="viewer@example.com",
            password="correct horse battery staple",
            display_name="Viewer",
        )
        service.register(command)
        with pytest.raises(EmailAlreadyRegisteredError):
            service.register(command)
    finally:
        engine.dispose()


def test_identity_migrations_upgrade_and_downgrade_without_create_all(tmp_path: Path) -> None:
    settings = _settings(tmp_path / "identity.sqlite3")
    upgrade_identity_database(settings)
    engine = create_identity_engine(settings)
    try:
        assert "sessions" in inspect(engine).get_table_names()
    finally:
        engine.dispose()
    downgrade_identity_database(settings)
    engine = create_identity_engine(settings)
    try:
        assert "sessions" not in inspect(engine).get_table_names()
    finally:
        engine.dispose()


def test_checked_in_migration_matches_identity_adapter_metadata(tmp_path: Path) -> None:
    settings = _settings(tmp_path / "identity.sqlite3")
    upgrade_identity_database(settings)
    engine = create_identity_engine(settings)
    try:
        with engine.connect() as connection:
            context = MigrationContext.configure(connection)
            assert compare_metadata(context, IdentityBase.metadata) == []
    finally:
        engine.dispose()


def test_postgres_constraint_diagnostic_maps_only_named_email_conflict() -> None:
    class Diagnostic:
        constraint_name = "uq_user_accounts_email"

    class PsycopgLikeError(Exception):
        diag = Diagnostic()

    email_error = IntegrityError("insert", {}, PsycopgLikeError())
    assert _is_email_unique_conflict(email_error)

    class OtherDiagnostic:
        constraint_name = "uq_user_accounts_profile_id"

    class OtherPsycopgLikeError(Exception):
        diag = OtherDiagnostic()

    profile_error = IntegrityError("insert", {}, OtherPsycopgLikeError())
    assert not _is_email_unique_conflict(profile_error)
