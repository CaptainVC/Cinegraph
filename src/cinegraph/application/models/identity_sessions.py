from dataclasses import dataclass
from datetime import datetime

from cinegraph.domain.models.identity import SessionPrincipal, UserAccount


@dataclass(frozen=True, slots=True)
class RegisterAccountCommand:
    email: str
    password: str
    display_name: str


@dataclass(frozen=True, slots=True)
class AuthenticateAccountCommand:
    email: str
    password: str


@dataclass(frozen=True, slots=True)
class SessionGrant:
    token: str
    principal: SessionPrincipal
    expires_at: datetime
    account: UserAccount | None
