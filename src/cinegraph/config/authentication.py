from dataclasses import dataclass
from datetime import timedelta


@dataclass(frozen=True, slots=True)
class AuthenticationConfiguration:
    minimum_password_length: int
    maximum_password_length: int
    scrypt_cost: int
    scrypt_block_size: int
    scrypt_parallelization: int
    password_salt_bytes: int
    session_token_bytes: int
    authenticated_session_ttl: timedelta
    guest_session_ttl: timedelta
    email_pattern: str
    session_cookie_name: str
    session_cookie_path: str
    session_cookie_same_site: str


DEFAULT_AUTHENTICATION_CONFIGURATION = AuthenticationConfiguration(
    minimum_password_length=12,
    maximum_password_length=1024,
    scrypt_cost=2**14,
    scrypt_block_size=8,
    scrypt_parallelization=1,
    password_salt_bytes=16,
    session_token_bytes=32,
    authenticated_session_ttl=timedelta(days=14),
    guest_session_ttl=timedelta(hours=8),
    email_pattern=r"^[^\s@]+@[^\s@]+\.[^\s@]+$",
    session_cookie_name="cinegraph_session",
    session_cookie_path="/",
    session_cookie_same_site="lax",
)
