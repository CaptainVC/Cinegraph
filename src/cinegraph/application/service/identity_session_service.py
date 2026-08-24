import hashlib
import re
from datetime import datetime
from uuid import UUID

from cinegraph.application.exceptions.errors import (
    AccountDisabledError,
    AccountRequiredError,
    EmailAlreadyRegisteredError,
    InvalidCredentialsError,
    SessionInvalidError,
)
from cinegraph.application.models.identity_sessions import (
    AccountSummary,
    AuthenticateAccountCommand,
    ChangePasswordCommand,
    RegisterAccountCommand,
    SessionGrant,
    SessionSummary,
    UpdateDisplayNameCommand,
)
from cinegraph.common.error_messages import AuthenticationErrorMessages
from cinegraph.common.identifiers import IdentifierGenerator
from cinegraph.config import (
    DEFAULT_AUTHENTICATED_CORPUS_ACCESS_SCOPE,
    DEFAULT_AUTHENTICATION_CONFIGURATION,
    DEFAULT_GUEST_CORPUS_ACCESS_SCOPE,
    AuthenticationConfiguration,
)
from cinegraph.domain.enums.enum import AccountStatus, PrincipalKind
from cinegraph.domain.models.identity import (
    SessionPrincipal,
    SessionRecord,
    UserAccount,
)
from cinegraph.ports.date_time.clock import Clock
from cinegraph.ports.identity import (
    DuplicateEmailPersistenceError,
    IdentityUnitOfWork,
    IdentityUnitOfWorkFactory,
    PasswordHasher,
    SessionTokenGenerator,
)


class IdentitySessionService:
    def __init__(
        self,
        unit_of_work_factory: IdentityUnitOfWorkFactory,
        password_hasher: PasswordHasher,
        token_generator: SessionTokenGenerator,
        clock: Clock,
        configuration: AuthenticationConfiguration = (
            DEFAULT_AUTHENTICATION_CONFIGURATION
        ),
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._password_hasher = password_hasher
        self._token_generator = token_generator
        self._clock = clock
        self._configuration = configuration
        self._dummy_password_hash = password_hasher.hash_password(
            "x" * configuration.minimum_password_length
        )

    def register(self, command: RegisterAccountCommand) -> SessionGrant:
        email = self._normalize_email(command.email)
        self._validate_password(command.password)
        self._validate_display_name(command.display_name)
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
            with self._unit_of_work_factory() as unit_of_work:
                if unit_of_work.accounts.get_by_email(email) is not None:
                    raise EmailAlreadyRegisteredError()
                self._revoke_current_if_valid(command.current_session_token, unit_of_work, now)
                unit_of_work.accounts.add(account)
                grant = self._issue_authenticated(account, now, unit_of_work)
                unit_of_work.commit()
                return grant
        except DuplicateEmailPersistenceError as error:
            raise EmailAlreadyRegisteredError() from error

    def authenticate(self, command: AuthenticateAccountCommand) -> SessionGrant:
        email = self._normalize_email(command.email)
        self._validate_password(command.password, minimum=1)
        with self._unit_of_work_factory() as unit_of_work:
            account = unit_of_work.accounts.get_by_email(email)
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
        with self._unit_of_work_factory() as unit_of_work:
            # The credential lookup above avoids holding a lock while doing
            # password work. Re-read and lock the owner row before changing
            # session state so concurrent logins cannot both exceed the cap.
            locked_account = unit_of_work.accounts.get_by_user_id(
                account.user_id, for_update=True
            )
            if (
                locked_account is None
                or locked_account.profile_id != account.profile_id
            ):
                raise SessionInvalidError()
            if locked_account.status is not AccountStatus.ACTIVE:
                raise AccountDisabledError()
            if not self._password_hasher.verify_password(
                command.password, locked_account.password_hash
            ):
                raise InvalidCredentialsError()
            now = self._clock.now_utc()
            self._revoke_current_if_valid(command.current_session_token, unit_of_work, now)
            grant = self._issue_authenticated(locked_account, now, unit_of_work)
            unit_of_work.commit()
            return grant

    def issue_guest(self, current_session_token: str | None = None) -> SessionGrant:
        if current_session_token:
            try:
                digest = self._token_digest(current_session_token)
            except SessionInvalidError:
                digest = ""
            with self._unit_of_work_factory() as unit_of_work:
                existing = unit_of_work.sessions.get_by_token_sha256(digest)
                now = self._clock.now_utc()
                if existing is not None and self._is_active(existing, now):
                    account = (
                        unit_of_work.accounts.get_by_user_id(
                            existing.principal.user_id
                        )
                        if existing.principal.user_id is not None
                        else None
                    )
                    if existing.principal.kind is PrincipalKind.AUTHENTICATED:
                        # Never turn an authenticated browser session into a
                        # guest session.  A stale authenticated policy is
                        # rejected fail-closed instead of being downgraded.
                        if (
                            account is None
                            or account.profile_id != existing.principal.profile_id
                            or account.status is not AccountStatus.ACTIVE
                            or not self._scope_matches_policy(existing.principal)
                        ):
                            raise SessionInvalidError()
                        return SessionGrant(
                            current_session_token,
                            existing.principal,
                            existing.expires_at,
                            account,
                        )
                    unit_of_work.sessions.save(existing.revoke(now))
                    grant = self._issue_guest(now, unit_of_work)
                    unit_of_work.commit()
                    return grant
        now = self._clock.now_utc()
        with self._unit_of_work_factory() as unit_of_work:
            grant = self._issue_guest(now, unit_of_work)
            unit_of_work.commit()
            return grant

    def current_account(self, token: str) -> AccountSummary:
        principal = self.resolve(token)
        if principal.kind is not PrincipalKind.AUTHENTICATED or principal.user_id is None:
            raise AccountRequiredError()
        with self._unit_of_work_factory() as unit_of_work:
            account = unit_of_work.accounts.get_by_user_id(principal.user_id)
        if (
            account is None
            or account.profile_id != principal.profile_id
            or account.status is not AccountStatus.ACTIVE
        ):
            raise SessionInvalidError()
        return self._account_summary(account)

    def update_display_name(self, token: str, command: UpdateDisplayNameCommand) -> AccountSummary:
        self._validate_display_name(command.display_name)
        principal = self.resolve(token)
        if principal.kind is not PrincipalKind.AUTHENTICATED or principal.user_id is None:
            raise AccountRequiredError()
        with self._unit_of_work_factory() as unit_of_work:
            account = unit_of_work.accounts.get_by_user_id(
                principal.user_id, for_update=True
            )
            if (
                account is None
                or account.profile_id != principal.profile_id
                or account.status is not AccountStatus.ACTIVE
            ):
                raise SessionInvalidError()
            self._require_active_presented_session(
                token, principal, unit_of_work, self._clock.now_utc()
            )
            updated = account.with_display_name(command.display_name)
            unit_of_work.accounts.save(updated)
            unit_of_work.commit()
            return self._account_summary(updated)

    def change_password(
        self,
        token: str,
        command: ChangePasswordCommand,
    ) -> SessionGrant:
        """Change the password and return the replacement authenticated session.

        Password rotation invalidates every prior session, including the caller's
        session.  Returning the newly issued grant makes that atomic security
        transition explicit to adapters, which can set both replacement cookies
        together with the response.
        """
        principal = self.resolve(token)
        if principal.kind is not PrincipalKind.AUTHENTICATED or principal.user_id is None:
            raise AccountRequiredError()
        self._validate_password(command.current_password, minimum=1)
        self._validate_password(command.new_password)
        if command.current_password == command.new_password:
            raise ValueError(AuthenticationErrorMessages.PASSWORD_MUST_DIFFER)
        with self._unit_of_work_factory() as unit_of_work:
            # Lock before verifying the old hash. Otherwise concurrent
            # password changes can both verify the same password and race to
            # overwrite one another in SQL.
            account = unit_of_work.accounts.get_by_user_id(
                principal.user_id, for_update=True
            )
            if (
                account is None
                or account.profile_id != principal.profile_id
                or account.status is not AccountStatus.ACTIVE
            ):
                raise SessionInvalidError()
            self._require_active_presented_session(
                token, principal, unit_of_work, self._clock.now_utc()
            )
            if not self._password_hasher.verify_password(
                command.current_password, account.password_hash
            ):
                raise InvalidCredentialsError()
            replacement_hash = self._password_hasher.hash_password(command.new_password)
            updated_account = account.with_password_hash(replacement_hash)
            unit_of_work.accounts.save(updated_account)
            now = self._clock.now_utc()
            unit_of_work.sessions.revoke_all_for_user(principal.user_id, principal.profile_id, now)
            grant = self._issue_authenticated(updated_account, now, unit_of_work)
            unit_of_work.commit()
            return grant

    def list_sessions(self, token: str) -> tuple[SessionSummary, ...]:
        principal = self.resolve(token)
        if principal.kind is not PrincipalKind.AUTHENTICATED or principal.user_id is None:
            raise AccountRequiredError()
        now = self._clock.now_utc()
        with self._unit_of_work_factory() as unit_of_work:
            sessions = unit_of_work.sessions.list_active_for_user(
                principal.user_id, principal.profile_id, now, self._configuration.maximum_session_listing
            )
        current_digest = self._token_digest(token)
        return tuple(
            SessionSummary(s.session_id, s.created_at, s.expires_at, s.token_sha256 == current_digest)
            for s in sessions
        )

    def revoke_session(self, token: str, session_id: UUID) -> bool:
        principal = self.resolve(token)
        if principal.kind is not PrincipalKind.AUTHENTICATED or principal.user_id is None:
            raise AccountRequiredError()
        with self._unit_of_work_factory() as unit_of_work:
            revoked = unit_of_work.sessions.revoke_session(
                session_id, principal.user_id, principal.profile_id, self._clock.now_utc()
            )
            if revoked:
                unit_of_work.commit()
            return revoked

    def revoke_all(self, token: str) -> None:
        principal = self.resolve(token)
        if principal.kind is not PrincipalKind.AUTHENTICATED or principal.user_id is None:
            raise AccountRequiredError()
        with self._unit_of_work_factory() as unit_of_work:
            account = unit_of_work.accounts.get_by_user_id(
                principal.user_id, for_update=True
            )
            if (
                account is None
                or account.profile_id != principal.profile_id
                or account.status is not AccountStatus.ACTIVE
            ):
                raise SessionInvalidError()
            self._require_active_presented_session(
                token, principal, unit_of_work, self._clock.now_utc()
            )
            unit_of_work.sessions.revoke_all_for_user(
                principal.user_id, principal.profile_id, self._clock.now_utc()
            )
            unit_of_work.commit()

    def resolve_grant(self, token: str) -> SessionGrant:
        """Resolve a live token into its complete, adapter-safe session view.

        The token is retained only in the returned in-process grant so the
        caller can rotate cookies. It is deliberately not part of any API
        response or persisted record.
        """
        digest = self._token_digest(token)
        with self._unit_of_work_factory() as unit_of_work:
            session = unit_of_work.sessions.get_by_token_sha256(digest)
            now = self._clock.now_utc()
            if (
                session is None
                or not self._is_active(session, now)
                or not self._scope_matches_policy(session.principal)
            ):
                raise SessionInvalidError()
            account: UserAccount | None = None
            if session.principal.kind is PrincipalKind.AUTHENTICATED:
                if session.principal.user_id is None:
                    raise SessionInvalidError()
                account = unit_of_work.accounts.get_by_user_id(
                    session.principal.user_id
                )
                if (
                    account is None
                    or account.profile_id != session.principal.profile_id
                    or account.status is not AccountStatus.ACTIVE
                ):
                    raise SessionInvalidError()
            return SessionGrant(
                token=token,
                principal=session.principal,
                expires_at=session.expires_at,
                account=account,
            )

    def resolve(self, token: str) -> SessionPrincipal:
        return self.resolve_grant(token).principal

    def revoke(self, token: str) -> None:
        digest = self._token_digest(token)
        with self._unit_of_work_factory() as unit_of_work:
            session = unit_of_work.sessions.get_by_token_sha256(digest)
            now = self._clock.now_utc()
            if session is None or not self._is_active(session, now):
                raise SessionInvalidError()
            unit_of_work.sessions.save(session.revoke(now))
            unit_of_work.commit()

    def _issue_authenticated(
        self,
        account: UserAccount,
        now: datetime,
        unit_of_work: IdentityUnitOfWork,
    ) -> SessionGrant:
        # Account ownership is the serialization point for session issuance.
        # In-memory UoWs already hold their process lock; SQL adapters map the
        # explicit flag to SELECT ... FOR UPDATE.
        locked_account = unit_of_work.accounts.get_by_user_id(
            account.user_id, for_update=True
        )
        if (
            locked_account is None
            or locked_account.profile_id != account.profile_id
            or locked_account.status is not AccountStatus.ACTIVE
        ):
            raise SessionInvalidError()
        account = locked_account
        principal = SessionPrincipal(
            kind=PrincipalKind.AUTHENTICATED,
            profile_id=account.profile_id,
            user_id=account.user_id,
            corpus_access_scope=DEFAULT_AUTHENTICATED_CORPUS_ACCESS_SCOPE,
        )
        active = unit_of_work.sessions.list_active_for_user(
            account.user_id,
            account.profile_id,
            now,
            None,
        )
        for stale in active[self._configuration.maximum_active_authenticated_sessions - 1 :]:
            unit_of_work.sessions.save(stale.revoke(now))
        return self._issue(
            principal,
            now,
            now + self._configuration.authenticated_session_ttl,
            account=account,
            unit_of_work=unit_of_work,
        )

    def _issue(
        self,
        principal: SessionPrincipal,
        created_at: datetime,
        expires_at: datetime,
        account: UserAccount | None,
        unit_of_work: IdentityUnitOfWork,
    ) -> SessionGrant:
        token = self._token_generator.generate()
        session = SessionRecord(
            session_id=IdentifierGenerator.new_id(),
            token_sha256=self._token_digest(token),
            principal=principal,
            created_at=created_at,
            expires_at=expires_at,
        )
        unit_of_work.sessions.save(session)
        return SessionGrant(
            token=token,
            principal=principal,
            expires_at=expires_at,
            account=account,
        )

    def _issue_guest(self, now: datetime, unit_of_work: IdentityUnitOfWork) -> SessionGrant:
        principal = SessionPrincipal(
            kind=PrincipalKind.GUEST,
            profile_id=IdentifierGenerator.new_id(),
            corpus_access_scope=DEFAULT_GUEST_CORPUS_ACCESS_SCOPE,
        )
        return self._issue(
            principal, now, now + self._configuration.guest_session_ttl, None, unit_of_work
        )

    def _revoke_current_if_valid(
        self, token: str | None, unit_of_work: IdentityUnitOfWork, now: datetime
    ) -> None:
        if not token:
            return
        try:
            digest = self._token_digest(token)
        except SessionInvalidError:
            return
        session = unit_of_work.sessions.get_by_token_sha256(digest)
        if session is not None and self._is_active(session, now):
            unit_of_work.sessions.save(session.revoke(now))

    def _require_active_presented_session(
        self,
        token: str,
        principal: SessionPrincipal,
        unit_of_work: IdentityUnitOfWork,
        now: datetime,
    ) -> None:
        """Re-check the caller's token inside a locked mutation transaction."""
        session = unit_of_work.sessions.get_by_token_sha256(self._token_digest(token))
        if (
            session is None
            or not self._is_active(session, now)
            or session.principal != principal
            or not self._scope_matches_policy(session.principal)
        ):
            raise SessionInvalidError()

    @staticmethod
    def _is_active(session: SessionRecord | None, now: datetime) -> bool:
        return session is not None and session.revoked_at is None and now < session.expires_at

    @staticmethod
    def _scope_matches_policy(principal: SessionPrincipal) -> bool:
        if principal.kind is PrincipalKind.GUEST:
            return principal.corpus_access_scope == DEFAULT_GUEST_CORPUS_ACCESS_SCOPE
        return principal.corpus_access_scope == DEFAULT_AUTHENTICATED_CORPUS_ACCESS_SCOPE

    @staticmethod
    def _account_summary(account: UserAccount) -> AccountSummary:
        return AccountSummary(
            account.user_id, account.profile_id, account.email, account.display_name,
            account.status.value, account.created_at
        )

    def _normalize_email(self, value: str) -> str:
        normalized = value.strip().casefold() if isinstance(value, str) else ""
        if (
            not (
                self._configuration.minimum_email_length
                <= len(normalized)
                <= self._configuration.maximum_email_length
            )
            or re.fullmatch(self._configuration.email_pattern, normalized) is None
        ):
            raise ValueError(AuthenticationErrorMessages.EMAIL_ADDRESS_MUST_BE_VALID)
        return normalized

    def _validate_password(self, value: str, *, minimum: int | None = None) -> None:
        minimum_length = (
            self._configuration.minimum_password_length
            if minimum is None
            else minimum
        )
        if (
            not isinstance(value, str)
            or len(value) < minimum_length
            or len(value) > self._configuration.maximum_password_length
        ):
            raise ValueError(AuthenticationErrorMessages.PASSWORD_LENGTH_MUST_BE_VALID)

    def _validate_display_name(self, value: str) -> None:
        if (
            not isinstance(value, str)
            or value.strip() != value
            or not (
                self._configuration.minimum_display_name_length
                <= len(value)
                <= self._configuration.maximum_display_name_length
            )
        ):
            raise ValueError(AuthenticationErrorMessages.DISPLAY_NAME_MUST_BE_TRIMMED)

    @staticmethod
    def _token_digest(token: str) -> str:
        if not isinstance(token, str) or not token or token.strip() != token:
            raise SessionInvalidError()
        return hashlib.sha256(token.encode("utf-8")).hexdigest()
