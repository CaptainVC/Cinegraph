from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    UniqueConstraint,
    Uuid,
    select,
)
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped, Session, mapped_column, relationship
from sqlalchemy.orm.session import sessionmaker

from cinegraph.adapters.persistence.base import PersistenceBase
from cinegraph.common.error_messages import AuthenticationErrorMessages
from cinegraph.domain.enums.enum import AccountStatus, CorpusAccessMode, PrincipalKind
from cinegraph.domain.models.access import CorpusAccessScope, CorpusSeasonAccess
from cinegraph.domain.models.identity import SessionPrincipal, SessionRecord, UserAccount
from cinegraph.ports.identity import DuplicateEmailPersistenceError

IdentityBase = PersistenceBase


class UserAccountRow(PersistenceBase):
    __tablename__ = "user_accounts"

    user_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    profile_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(512), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    __table_args__ = (
        UniqueConstraint("profile_id", name="uq_user_accounts_profile_id"),
        UniqueConstraint("email", name="uq_user_accounts_email"),
        UniqueConstraint("user_id", "profile_id", name="uq_user_accounts_user_profile"),
        CheckConstraint("status IN ('active', 'disabled')", name="ck_user_accounts_status"),
    )


class SessionRow(IdentityBase):
    __tablename__ = "sessions"

    session_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    token_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    principal_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    profile_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    user_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    access_mode: Mapped[str] = mapped_column(String(32), nullable=False)
    access_revision: Mapped[str] = mapped_column(String(128), nullable=False)
    unrestricted: Mapped[bool] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    entitlements: Mapped[list[SessionEntitlementRow]] = relationship(
        back_populates="session",
        cascade="all, delete-orphan",
        order_by="(SessionEntitlementRow.series_id, SessionEntitlementRow.season_number)",
    )
    __table_args__ = (
        ForeignKeyConstraint(
            ["user_id", "profile_id"],
            ["user_accounts.user_id", "user_accounts.profile_id"],
            name="fk_sessions_user_profile",
        ),
        UniqueConstraint("token_sha256", name="uq_sessions_token_sha256"),
        CheckConstraint(
            "((principal_kind = 'guest' AND user_id IS NULL AND access_mode = 'guest' AND unrestricted IS FALSE) "
            "OR (principal_kind = 'authenticated' AND user_id IS NOT NULL AND access_mode = 'authenticated' AND unrestricted IS TRUE))",
            name="ck_sessions_principal_coherence",
        ),
        CheckConstraint("expires_at > created_at", name="ck_sessions_expiry_after_creation"),
        CheckConstraint(
            "revoked_at IS NULL OR (revoked_at >= created_at AND revoked_at <= expires_at)",
            name="ck_sessions_revocation_lifecycle",
        ),
        Index("ix_sessions_expires_at", "expires_at"),
        Index("ix_sessions_profile_id", "profile_id"),
    )


class SessionEntitlementRow(IdentityBase):
    __tablename__ = "session_entitlements"

    session_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("sessions.session_id", ondelete="CASCADE"), primary_key=True
    )
    series_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    season_number: Mapped[int] = mapped_column(primary_key=True)
    session: Mapped[SessionRow] = relationship(back_populates="entitlements")
    __table_args__ = (
        CheckConstraint(
            "season_number >= 1",
            name="ck_session_entitlements_positive_season",
        ),
        Index(
            "ix_session_entitlements_series_season",
            "series_id",
            "season_number",
        ),
    )


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _account_from_row(row: UserAccountRow) -> UserAccount:
    return UserAccount(
        user_id=row.user_id,
        profile_id=row.profile_id,
        email=row.email,
        display_name=row.display_name,
        password_hash=row.password_hash,
        status=AccountStatus(row.status),
        created_at=_utc(row.created_at),
    )


def _session_from_row(row: SessionRow) -> SessionRecord:
    principal = SessionPrincipal(
        kind=PrincipalKind(row.principal_kind),
        profile_id=row.profile_id,
        user_id=row.user_id,
        corpus_access_scope=CorpusAccessScope(
            mode=CorpusAccessMode(row.access_mode),
            revision=row.access_revision,
            allowed_seasons=frozenset(
                CorpusSeasonAccess(
                    series_id=entitlement.series_id,
                    season_number=entitlement.season_number,
                )
                for entitlement in row.entitlements
            ),
            unrestricted=row.unrestricted,
        ),
    )
    return SessionRecord(
        session_id=row.session_id,
        token_sha256=row.token_sha256,
        principal=principal,
        created_at=_utc(row.created_at),
        expires_at=_utc(row.expires_at),
        revoked_at=_utc(row.revoked_at) if row.revoked_at is not None else None,
    )


class SqlAlchemyUserAccountRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get_by_email(self, normalized_email: str) -> UserAccount | None:
        row = self._session.scalar(
            select(UserAccountRow).where(UserAccountRow.email == normalized_email)
        )
        return _account_from_row(row) if row is not None else None

    def add(self, account: UserAccount) -> None:
        self._session.add(
            UserAccountRow(
                user_id=account.user_id,
                profile_id=account.profile_id,
                email=account.email,
                display_name=account.display_name,
                password_hash=account.password_hash,
                status=account.status.value,
                created_at=_utc(account.created_at),
            )
        )
        try:
            self._session.flush()
        except IntegrityError as error:
            if _is_email_unique_conflict(error):
                raise DuplicateEmailPersistenceError(
                    AuthenticationErrorMessages.EMAIL_ALREADY_REGISTERED
                ) from error
            raise


class SqlAlchemySessionRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get_by_token_sha256(self, token_sha256: str) -> SessionRecord | None:
        row = self._session.scalar(
            select(SessionRow).where(SessionRow.token_sha256 == token_sha256)
        )
        return _session_from_row(row) if row is not None else None

    def save(self, session: SessionRecord) -> None:
        row = self._session.get(SessionRow, session.session_id)
        values = {
            "token_sha256": session.token_sha256,
            "principal_kind": session.principal.kind.value,
            "profile_id": session.principal.profile_id,
            "user_id": session.principal.user_id,
            "access_mode": session.principal.corpus_access_scope.mode.value,
            "access_revision": session.principal.corpus_access_scope.revision,
            "unrestricted": session.principal.corpus_access_scope.unrestricted,
            "created_at": _utc(session.created_at),
            "expires_at": _utc(session.expires_at),
            "revoked_at": (_utc(session.revoked_at) if session.revoked_at is not None else None),
        }
        if row is None:
            row = SessionRow(session_id=session.session_id, **values)
            row.entitlements = [
                SessionEntitlementRow(
                    session_id=session.session_id,
                    series_id=entitlement.series_id,
                    season_number=entitlement.season_number,
                )
                for entitlement in sorted(session.principal.corpus_access_scope.allowed_seasons)
            ]
            self._session.add(row)
        else:
            for key, value in values.items():
                setattr(row, key, value)
            row.entitlements = [
                SessionEntitlementRow(
                    session_id=session.session_id,
                    series_id=entitlement.series_id,
                    season_number=entitlement.season_number,
                )
                for entitlement in sorted(session.principal.corpus_access_scope.allowed_seasons)
            ]


class SqlAlchemyIdentityUnitOfWork:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory
        self._session: Session | None = None
        self.accounts: SqlAlchemyUserAccountRepository
        self.sessions: SqlAlchemySessionRepository

    def __enter__(self) -> "SqlAlchemyIdentityUnitOfWork":
        self._session = self._session_factory()
        self._session.begin()
        self.accounts = SqlAlchemyUserAccountRepository(self._session)
        self.sessions = SqlAlchemySessionRepository(self._session)
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
            raise RuntimeError("Identity unit of work is not active.")
        try:
            self._session.commit()
        except IntegrityError:
            self._session.rollback()
            raise

    def rollback(self) -> None:
        if self._session is not None:
            self._session.rollback()


class SqlAlchemyIdentityUnitOfWorkFactory:
    def __init__(self, engine: Engine) -> None:
        self._session_factory = sessionmaker(
            bind=engine,
            autobegin=False,
            expire_on_commit=False,
            autoflush=True,
        )

    def __call__(self) -> SqlAlchemyIdentityUnitOfWork:
        return SqlAlchemyIdentityUnitOfWork(self._session_factory)

def _is_email_unique_conflict(error: IntegrityError) -> bool:
    constraint_name = getattr(error.orig, "constraint_name", None)
    if constraint_name is None:
        diagnostic = getattr(error.orig, "diag", None)
        constraint_name = getattr(diagnostic, "constraint_name", None)
    if constraint_name in {"uq_user_accounts_email", "user_accounts_email_key"}:
        return True
    message = str(error.orig).lower()
    return "user_accounts.email" in message or "uq_user_accounts_email" in message
