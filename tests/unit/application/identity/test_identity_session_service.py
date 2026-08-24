from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from tests.factories import make_episode_ref

from cinegraph.adapters.identity import (
    InMemoryIdentityUnitOfWorkFactory,
    ScryptPasswordHasher,
)
from cinegraph.application.exceptions.errors import (
    EmailAlreadyRegisteredError,
    InvalidCredentialsError,
    SessionInvalidError,
)
from cinegraph.application.models.identity_sessions import (
    AuthenticateAccountCommand,
    ChangePasswordCommand,
    RegisterAccountCommand,
)
from cinegraph.application.service.identity_session_service import (
    IdentitySessionService,
)
from cinegraph.common.error_messages import AuthenticationErrorMessages
from cinegraph.config import DEFAULT_AUTHENTICATION_CONFIGURATION
from cinegraph.domain.enums.enum import AccountStatus, PrincipalKind
from cinegraph.domain.models.access import CorpusAccessScope


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


class FailingTokenGenerator:
    def generate(self) -> str:
        raise RuntimeError("token generation failed")


def make_service():
    factory = InMemoryIdentityUnitOfWorkFactory()
    clock = MutableClock()
    service = IdentitySessionService(
        factory,
        ScryptPasswordHasher(),
        SequenceTokenGenerator(),
        clock,
    )
    return service, factory, factory, clock


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


def test_registration_rolls_back_account_when_session_issuance_fails() -> None:
    factory = InMemoryIdentityUnitOfWorkFactory()
    service = IdentitySessionService(
        factory,
        ScryptPasswordHasher(),
        FailingTokenGenerator(),
        MutableClock(),
    )

    with pytest.raises(RuntimeError, match="token generation failed"):
        service.register(
            RegisterAccountCommand(
                email="rollback@example.com",
                password="correct horse battery staple",
                display_name="Rollback",
            )
        )

    assert factory.get_by_email("rollback@example.com") is None
    assert factory.sessions == ()


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


def test_password_rotation_returns_replacement_grant_and_invalidates_old_sessions() -> None:
    service, _, sessions, _ = make_service()
    registered = service.register(
        RegisterAccountCommand(
            email="viewer@example.com",
            password="correct horse battery staple",
            display_name="Viewer",
        )
    )

    replacement = service.change_password(
        registered.token,
        ChangePasswordCommand(
            current_password="correct horse battery staple",
            new_password="an entirely different password",
        ),
    )

    assert replacement.token != registered.token
    assert replacement.account is not None
    assert service.resolve(replacement.token) == replacement.principal
    with pytest.raises(SessionInvalidError):
        service.resolve(registered.token)
    assert len(sessions.sessions) == 2


def test_guest_request_reuses_active_authenticated_session_without_downgrade() -> None:
    service, _, sessions, _ = make_service()
    authenticated = service.register(
        RegisterAccountCommand(
            email="viewer@example.com",
            password="correct horse battery staple",
            display_name="Viewer",
        )
    )

    guest_request = service.issue_guest(authenticated.token)

    assert guest_request == authenticated
    assert guest_request.principal.kind is PrincipalKind.AUTHENTICATED
    assert len(sessions.sessions) == 1


def test_in_memory_revoke_session_rejects_expired_owned_session() -> None:
    service, factory, _, clock = make_service()
    grant = service.register(
        RegisterAccountCommand(
            email="viewer@example.com",
            password="correct horse battery staple",
            display_name="Viewer",
        )
    )
    assert grant.principal.user_id is not None
    clock.advance(timedelta(days=14))
    session = factory.sessions[0]

    with factory() as unit_of_work:
        assert not unit_of_work.sessions.revoke_session(
            session.session_id,
            grant.principal.user_id,
            grant.principal.profile_id,
            clock.now_utc(),
        )


def test_authenticated_session_cap_is_deterministic_and_does_not_cap_guests() -> None:
    factory = InMemoryIdentityUnitOfWorkFactory()
    clock = MutableClock()
    configuration = replace(
        DEFAULT_AUTHENTICATION_CONFIGURATION,
        maximum_active_authenticated_sessions=2,
    )
    service = IdentitySessionService(
        factory,
        ScryptPasswordHasher(),
        SequenceTokenGenerator(),
        clock,
        configuration,
    )
    first = service.register(
        RegisterAccountCommand(
            email="viewer@example.com",
            password="correct horse battery staple",
            display_name="Viewer",
        )
    )
    second = service.authenticate(
        AuthenticateAccountCommand(
            email="viewer@example.com",
            password="correct horse battery staple",
        )
    )
    third = service.authenticate(
        AuthenticateAccountCommand(
            email="viewer@example.com",
            password="correct horse battery staple",
        )
    )
    guest_one = service.issue_guest()
    guest_two = service.issue_guest()

    assert any(item.current for item in service.list_sessions(third.token))
    assert len(service.list_sessions(third.token)) == 2
    assert (
        len(
            tuple(
                session
                for session in factory.sessions
                if session.principal.kind is PrincipalKind.GUEST
            )
        )
        == 2
    )
    prior_session_validity = []
    for token in (first.token, second.token):
        try:
            service.resolve(token)
        except SessionInvalidError:
            prior_session_validity.append(False)
        else:
            prior_session_validity.append(True)
    assert prior_session_validity.count(False) == 1
    assert service.resolve(guest_one.token) == guest_one.principal
    assert service.resolve(guest_two.token) == guest_two.principal


def test_authenticated_resolution_fails_closed_for_missing_disabled_or_mismatched_account() -> None:
    service, factory, _, _ = make_service()
    grant = service.register(
        RegisterAccountCommand(
            email="viewer@example.com",
            password="correct horse battery staple",
            display_name="Viewer",
        )
    )
    account = factory.get_by_email("viewer@example.com")
    assert account is not None

    with factory() as unit_of_work:
        unit_of_work.accounts.save(replace(account, status=AccountStatus.DISABLED))
        unit_of_work.commit()
    with pytest.raises(SessionInvalidError):
        service.resolve(grant.token)

    with pytest.raises(SessionInvalidError):
        service.issue_guest(grant.token)

    factory._accounts_by_email.clear()
    with pytest.raises(SessionInvalidError):
        service.resolve(grant.token)


def test_resolution_and_guest_reuse_fail_closed_for_stale_scopes() -> None:
    service, factory, _, _ = make_service()
    guest = service.issue_guest()
    guest_record = factory.sessions[0]
    stale_guest = replace(
        guest_record,
        principal=replace(
            guest_record.principal,
            corpus_access_scope=replace(
                guest_record.principal.corpus_access_scope,
                revision="stale-guest-revision",
            ),
        ),
    )
    with factory() as unit_of_work:
        unit_of_work.sessions.save(stale_guest)
        unit_of_work.commit()
    with pytest.raises(SessionInvalidError):
        service.resolve(guest.token)

    authenticated = service.register(
        RegisterAccountCommand(
            email="auth@example.com",
            password="correct horse battery staple",
            display_name="Authenticated",
        )
    )
    authenticated_record = next(
        session
        for session in factory.sessions
        if session.token_sha256 != guest_record.token_sha256
    )
    stale_authenticated = replace(
        authenticated_record,
        principal=replace(
            authenticated_record.principal,
            corpus_access_scope=CorpusAccessScope(
                mode=authenticated_record.principal.corpus_access_scope.mode,
                revision="stale-auth-revision",
                allowed_seasons=frozenset(),
                unrestricted=True,
            ),
        ),
    )
    with factory() as unit_of_work:
        unit_of_work.sessions.save(stale_authenticated)
        unit_of_work.commit()
    with pytest.raises(SessionInvalidError):
        service.resolve(authenticated.token)
    with pytest.raises(SessionInvalidError):
        service.issue_guest(authenticated.token)


def test_identity_service_enforces_injected_configuration_bounds() -> None:
    factory = InMemoryIdentityUnitOfWorkFactory()
    configuration = replace(
        DEFAULT_AUTHENTICATION_CONFIGURATION,
        maximum_password_length=16,
        maximum_display_name_length=5,
        maximum_email_length=10,
    )
    service = IdentitySessionService(
        factory,
        ScryptPasswordHasher(),
        SequenceTokenGenerator(),
        MutableClock(),
        configuration,
    )

    with pytest.raises(ValueError, match="Password length"):
        service.register(
            RegisterAccountCommand(
                email="a@b.co",
                password="a" * 17,
                display_name="Short",
            )
        )
    with pytest.raises(ValueError, match="Display name"):
        service.register(
            RegisterAccountCommand(
                email="a@b.co",
                password="short password",
                display_name="Too long",
            )
        )
    with pytest.raises(ValueError, match="Email address"):
        service.register(
            RegisterAccountCommand(
                email="viewer@example.com",
                password="short password",
                display_name="Short",
            )
        )
