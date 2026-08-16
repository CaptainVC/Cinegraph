import re
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from cinegraph.common.error_messages import AuthenticationErrorMessages
from cinegraph.config import DEFAULT_AUTHENTICATION_CONFIGURATION
from cinegraph.domain.enums.enum import AccountStatus
from cinegraph.domain.exceptions.errors import InvalidModelError


@dataclass(frozen=True, slots=True)
class UserAccount:
    user_id: UUID
    profile_id: UUID
    email: str
    display_name: str
    password_hash: str
    status: AccountStatus
    created_at: datetime

    def __post_init__(self) -> None:
        if (
            not isinstance(self.email, str)
            or self.email != self.email.casefold()
            or re.fullmatch(
                DEFAULT_AUTHENTICATION_CONFIGURATION.email_pattern,
                self.email,
            )
            is None
        ):
            raise InvalidModelError(
                AuthenticationErrorMessages.EMAIL_ADDRESS_MUST_BE_VALID
            )
        if (
            not isinstance(self.display_name, str)
            or not self.display_name
            or self.display_name.strip() != self.display_name
        ):
            raise InvalidModelError(
                AuthenticationErrorMessages.DISPLAY_NAME_MUST_BE_TRIMMED
            )
        if not self.password_hash or self.password_hash.strip() != self.password_hash:
            raise InvalidModelError(
                AuthenticationErrorMessages.PASSWORD_HASH_MUST_BE_VALID
            )
