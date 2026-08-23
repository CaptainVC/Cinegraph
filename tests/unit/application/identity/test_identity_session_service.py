from datetime import UTC, datetime, timedelta

import pytest
from tests.factories import make_episode_ref

from cinegraph.adapters.identity import (
    InMemorySessionRepository,
    InMemoryUserAccountRepository,
    ScryptPasswordHasher,
)
from cinegraph.application.exceptions.errors import (
    EmailAlreadyRegisteredError,
    InvalidCredentialsError,
    SessionInvalidError,
)
from cinegraph.application.models.identity_sessions import (
    AuthenticateAccountCommand,
    RegisterAccountCommand,
)
from cinegraph.application.service.identity_session_service import (
    IdentitySessionService,
)
from cinegraph.common.error_messages import AuthenticationErrorMessages
from cinegraph.domain.enums.enum import PrincipalKind


class MutableClock:
    def __init__(self) -> None:
        self.value = datetime(2026, 8, 16, 12, 0, tzinfo=UTC)

    def now_utc(self) -> datetime:
        return self.value

    def advance(self, delta: timedelta) -> None:
        self.value += delta


class SequenceTokenGenerator:
    def __init__(self) -> None:
        self.index = 0

    def generate(self) -> str:
        self.index += 1
        return f"opaque-session-token-{self.index:04d}"


def make_service():
    accounts = InMemoryUserAccountRepository()
    sessions = InMemorySessionRepository()
    clock = MutableClock()
    service = IdentitySessionService(
        accounts,
        sessions,
        ScryptPasswordHasher(),
        SequenceTokenGenerator(),
        clock,
    )
    return service, accounts, sessions, clock


def test_registration_normalizes_identity_hashes_password_and_issues_session() -> None:
    service, accounts, sessions, _ = make_service()

    grant = service.register(
        RegisterAccountCommand(
            email="  Viewer@Example.COM ",
            password="correct horse battery staple",
            display_name="Primary viewer",
        )
    )

    account = accounts.get_by_email("viewer@example.com")
    assert account is not None
    assert account.email == "viewer@example.com"
    assert "correct horse" not in account.password_hash
    assert grant.account == account
    assert grant.principal.kind is PrincipalKind.AUTHENTICATED
    assert grant.principal.corpus_access_scope.unrestricted is True
    assert service.resolve(grant.token) == grant.principal
    assert len(sessions.sessions) == 1
    assert sessions.sessions[0].token_sha256 != grant.token
    assert grant.token not in repr(sessions.sessions[0])


def test_duplicate_registration_and_invalid_credentials_use_stable_errors() -> None:
    service, _, _, _ = make_service()
    command = RegisterAccountCommand(
        email="viewer@example.com",
        password="correct horse battery staple",
        display_name="Viewer",
    )
    service.register(command)

    with pytest.raises(
        EmailAlreadyRegisteredError,
        match=AuthenticationErrorMessages.EMAIL_ALREADY_REGISTERED,
    ):
        service.register(command)

    for email in ("viewer@example.com", "missing@example.com"):
        with pytest.raises(
            InvalidCredentialsError,
            match=AuthenticationErrorMessages.INVALID_CREDENTIALS,
        ):
            service.authenticate(
                AuthenticateAccountCommand(email=email, password="wrong password value")
            )


def test_authentication_issues_a_new_token_for_existing_account() -> None:
    service, _, sessions, _ = make_service()
    registered = service.register(
        RegisterAccountCommand(
            email="viewer@example.com",
            password="correct horse battery staple",
            display_name="Viewer",
        )
    )

    authenticated = service.authenticate(
        AuthenticateAccountCommand(
            email="VIEWER@example.com",
            password="correct horse battery staple",
        )
    )

    assert authenticated.token != registered.token
    assert authenticated.principal == registered.principal
    assert len(sessions.sessions) == 2


def test_guest_scope_allows_only_configured_seasons_and_expires() -> None:
    service, _, _, clock = make_service()

    grant = service.issue_guest()

    assert grant.account is None
    assert grant.principal.kind is PrincipalKind.GUEST
    scope = grant.principal.corpus_access_scope
    assert scope.allows_episode(make_episode_ref(season_number=1))
    assert scope.allows_episode(make_episode_ref(season_number=2))
    assert not scope.allows_episode(make_episode_ref(season_number=3))
    assert service.resolve(grant.token) == grant.principal
    clock.advance(timedelta(hours=8))
    with pytest.raises(SessionInvalidError):
        service.resolve(grant.token)


def test_revoked_session_cannot_be_resolved_or_revoked_again() -> None:
    service, _, _, _ = make_service()
    grant = service.issue_guest()

    service.revoke(grant.token)

    with pytest.raises(SessionInvalidError):
        service.resolve(grant.token)
    with pytest.raises(SessionInvalidError):
        service.revoke(grant.token)
