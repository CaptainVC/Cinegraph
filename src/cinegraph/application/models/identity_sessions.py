from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from cinegraph.common.error_messages import AuthenticationErrorMessages
from cinegraph.config import DEFAULT_AUTHENTICATION_CONFIGURATION
from cinegraph.domain.models.identity import SessionPrincipal, UserAccount


@dataclass(frozen=True, slots=True)
class RegisterAccountCommand:
    email: str
    password: str
    display_name: str
    current_session_token: str | None = None

    def __post_init__(self) -> None:
        _validate_email(self.email)
        _validate_password(self.password)
        _validate_display_name(self.display_name)


@dataclass(frozen=True, slots=True)
class AuthenticateAccountCommand:
    email: str
    password: str
    current_session_token: str | None = None

    def __post_init__(self) -> None:
        _validate_email(self.email)
        _validate_password(self.password, minimum=1)


@dataclass(frozen=True, slots=True)
class SessionGrant:
    token: str
    principal: SessionPrincipal
    expires_at: datetime
    account: UserAccount | None


@dataclass(frozen=True, slots=True)
class UpdateDisplayNameCommand:
    display_name: str

    def __post_init__(self) -> None:
        _validate_display_name(self.display_name)


@dataclass(frozen=True, slots=True)
class ChangePasswordCommand:
    current_password: str
    new_password: str

    def __post_init__(self) -> None:
        _validate_password(self.current_password, minimum=1)
        _validate_password(self.new_password)
        if self.current_password == self.new_password:
            raise ValueError(AuthenticationErrorMessages.PASSWORD_MUST_DIFFER)


@dataclass(frozen=True, slots=True)
class AccountSummary:
    user_id: UUID
    profile_id: UUID
    email: str
    display_name: str
    status: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class SessionSummary:
    session_id: UUID
    created_at: datetime
    expires_at: datetime
    current: bool


def _validate_email(value: str) -> None:
    import re

    if not isinstance(value, str):
        raise ValueError(AuthenticationErrorMessages.EMAIL_ADDRESS_MUST_BE_VALID)
    normalized = value.strip().casefold()
    if (
        not (
            DEFAULT_AUTHENTICATION_CONFIGURATION.minimum_email_length
            <= len(normalized)
            <= DEFAULT_AUTHENTICATION_CONFIGURATION.maximum_email_length
        )
        or re.fullmatch(DEFAULT_AUTHENTICATION_CONFIGURATION.email_pattern, normalized)
        is None
    ):
        raise ValueError(AuthenticationErrorMessages.EMAIL_ADDRESS_MUST_BE_VALID)


def _validate_password(value: str, *, minimum: int | None = None) -> None:
    minimum_length = (
        DEFAULT_AUTHENTICATION_CONFIGURATION.minimum_password_length
        if minimum is None
        else minimum
    )
    if (
        not isinstance(value, str)
        or len(value) < minimum_length
        or len(value) > DEFAULT_AUTHENTICATION_CONFIGURATION.maximum_password_length
    ):
        raise ValueError(AuthenticationErrorMessages.PASSWORD_LENGTH_MUST_BE_VALID)


def _validate_display_name(value: str) -> None:
    if (
        not isinstance(value, str)
        or value.strip() != value
        or not (
            DEFAULT_AUTHENTICATION_CONFIGURATION.minimum_display_name_length
            <= len(value)
            <= DEFAULT_AUTHENTICATION_CONFIGURATION.maximum_display_name_length
        )
    ):
        raise ValueError(AuthenticationErrorMessages.DISPLAY_NAME_MUST_BE_TRIMMED)
