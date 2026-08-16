import hashlib
import re
from datetime import datetime

from cinegraph.application.exceptions.errors import (
    AccountDisabledError,
    EmailAlreadyRegisteredError,
    InvalidCredentialsError,
    SessionInvalidError,
)
from cinegraph.application.models.identity_sessions import (
    AuthenticateAccountCommand,
    RegisterAccountCommand,
    SessionGrant,
)
from cinegraph.common.error_messages import AuthenticationErrorMessages
from cinegraph.common.identifiers import IdentifierGenerator
from cinegraph.config import (
    DEFAULT_AUTHENTICATION_CONFIGURATION,
    DEFAULT_GUEST_CORPUS_ACCESS_SCOPE,
    AuthenticationConfiguration,
)
from cinegraph.domain.enums.enum import (
    AccountStatus,
    CorpusAccessMode,
    PrincipalKind,
)
from cinegraph.domain.models.access import CorpusAccessScope
from cinegraph.domain.models.identity import SessionPrincipal, SessionRecord, UserAccount
from cinegraph.ports.date_time.clock import Clock
from cinegraph.ports.identity import (
    PasswordHasher,
    SessionRepository,
    SessionTokenGenerator,
    UserAccountRepository,
)


class IdentitySessionService:
    def __init__(
        self,
        accounts: UserAccountRepository,
        sessions: SessionRepository,
        password_hasher: PasswordHasher,
        token_generator: SessionTokenGenerator,
        clock: Clock,
        configuration: AuthenticationConfiguration = (
            DEFAULT_AUTHENTICATION_CONFIGURATION
        ),
    ) -> None:
        self._accounts = accounts
        self._sessions = sessions
        self._password_hasher = password_hasher
        self._token_generator = token_generator
        self._clock = clock
        self._configuration = configuration
        self._dummy_password_hash = password_hasher.hash_password(
            "x" * configuration.minimum_password_length
        )

    def register(self, command: RegisterAccountCommand) -> SessionGrant:
        email = self._normalize_email(command.email)
        if self._accounts.get_by_email(email) is not None:
            raise EmailAlreadyRegisteredError()
        password_hash = self._password_hasher.hash_password(command.password)
        now = self._clock.now_utc()
        account = UserAccount(
            user_id=IdentifierGenerator.new_id(),
            profile_id=IdentifierGenerator.new_id(),
            email=email,
            display_name=command.display_name,
            password_hash=password_hash,
            status=AccountStatus.ACTIVE,
            created_at=now,
        )
        try:
            self._accounts.add(account)
        except ValueError as error:
            raise EmailAlreadyRegisteredError() from error
        return self._issue_authenticated(account, now)

    def authenticate(self, command: AuthenticateAccountCommand) -> SessionGrant:
        email = self._normalize_email(command.email)
        account = self._accounts.get_by_email(email)
        password_hash = (
            account.password_hash if account is not None else self._dummy_password_hash
        )
        verified = self._password_hasher.verify_password(
            command.password,
            password_hash,
        )
        if account is None or not verified:
            raise InvalidCredentialsError()
        if account.status is not AccountStatus.ACTIVE:
            raise AccountDisabledError()
        return self._issue_authenticated(account, self._clock.now_utc())

    def issue_guest(self) -> SessionGrant:
        now = self._clock.now_utc()
        principal = SessionPrincipal(
            kind=PrincipalKind.GUEST,
            profile_id=IdentifierGenerator.new_id(),
            corpus_access_scope=DEFAULT_GUEST_CORPUS_ACCESS_SCOPE,
        )
        return self._issue(
            principal,
            now,
            now + self._configuration.guest_session_ttl,
            account=None,
        )

    def resolve(self, token: str) -> SessionPrincipal:
        digest = self._token_digest(token)
        session = self._sessions.get_by_token_sha256(digest)
        now = self._clock.now_utc()
        if (
            session is None
            or session.revoked_at is not None
            or now >= session.expires_at
        ):
            raise SessionInvalidError()
        return session.principal

    def revoke(self, token: str) -> None:
        digest = self._token_digest(token)
        session = self._sessions.get_by_token_sha256(digest)
        now = self._clock.now_utc()
        if session is None or session.revoked_at is not None or now >= session.expires_at:
            raise SessionInvalidError()
        self._sessions.save(session.revoke(now))

    def _issue_authenticated(
        self,
        account: UserAccount,
        now: datetime,
    ) -> SessionGrant:
        principal = SessionPrincipal(
            kind=PrincipalKind.AUTHENTICATED,
            profile_id=account.profile_id,
            user_id=account.user_id,
            corpus_access_scope=CorpusAccessScope(
                mode=CorpusAccessMode.AUTHENTICATED,
                revision="authenticated-session-v1",
                allowed_seasons=frozenset(),
                unrestricted=True,
            ),
        )
        return self._issue(
            principal,
            now,
            now + self._configuration.authenticated_session_ttl,
            account=account,
        )

    def _issue(
        self,
        principal: SessionPrincipal,
        created_at: datetime,
        expires_at: datetime,
        account: UserAccount | None,
    ) -> SessionGrant:
        token = self._token_generator.generate()
        session = SessionRecord(
            session_id=IdentifierGenerator.new_id(),
            token_sha256=self._token_digest(token),
            principal=principal,
            created_at=created_at,
            expires_at=expires_at,
        )
        self._sessions.save(session)
        return SessionGrant(
            token=token,
            principal=principal,
            expires_at=expires_at,
            account=account,
        )

    def _normalize_email(self, value: str) -> str:
        normalized = value.strip().casefold() if isinstance(value, str) else ""
        if re.fullmatch(self._configuration.email_pattern, normalized) is None:
            raise ValueError(AuthenticationErrorMessages.EMAIL_ADDRESS_MUST_BE_VALID)
        return normalized

    @staticmethod
    def _token_digest(token: str) -> str:
        if not isinstance(token, str) or not token or token.strip() != token:
            raise SessionInvalidError()
        return hashlib.sha256(token.encode("utf-8")).hexdigest()
