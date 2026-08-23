from datetime import UTC, datetime
from pathlib import Path

import pytest

from cinegraph.adapters.identity import (
    ScryptPasswordHasher,
    SqliteIdentityRepositories,
)
from cinegraph.application.exceptions.errors import SessionInvalidError
from cinegraph.application.models.identity_sessions import RegisterAccountCommand
from cinegraph.application.service.identity_session_service import (
    IdentitySessionService,
)


class FixedClock:
    def now_utc(self) -> datetime:
        return datetime(2026, 8, 16, 12, 0, tzinfo=UTC)


class FixedTokenGenerator:
    def generate(self) -> str:
        return "persistent-opaque-session-token"


def make_service(repository: SqliteIdentityRepositories) -> IdentitySessionService:
    return IdentitySessionService(
        repository,
        repository,
        ScryptPasswordHasher(),
        FixedTokenGenerator(),
        FixedClock(),
    )


def test_accounts_sessions_and_revocation_survive_repository_restart(
    tmp_path: Path,
) -> None:
    path = tmp_path / "identity.sqlite3"
    first_repository = SqliteIdentityRepositories(path)
    first_service = make_service(first_repository)
    grant = first_service.register(
        RegisterAccountCommand(
            email="viewer@example.com",
            password="correct horse battery staple",
            display_name="Viewer",
        )
    )
    first_repository.close()

    second_repository = SqliteIdentityRepositories(path)
    second_service = make_service(second_repository)
    try:
        account = second_repository.get_by_email("viewer@example.com")
        assert account == grant.account
        assert second_service.resolve(grant.token) == grant.principal
        second_service.revoke(grant.token)
    finally:
        second_repository.close()

    third_repository = SqliteIdentityRepositories(path)
    third_service = make_service(third_repository)
    try:
        with pytest.raises(SessionInvalidError):
            third_service.resolve(grant.token)
    finally:
        third_repository.close()
