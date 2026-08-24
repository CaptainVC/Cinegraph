from dataclasses import dataclass
from datetime import timedelta
from typing import Literal

AUTHENTICATION_UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
TRUSTED_SAME_ORIGIN_SEC_FETCH_SITES = frozenset({"same-origin"})
CookieSameSite = Literal["lax", "strict"]


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
    session_cookie_same_site: CookieSameSite
    minimum_email_length: int = 3
    maximum_email_length: int = 320
    minimum_display_name_length: int = 1
    maximum_display_name_length: int = 100
    maximum_active_authenticated_sessions: int = 5
    maximum_session_listing: int = 20
    csrf_header_name: str = "X-CSRF-Token"
    csrf_cookie_name: str = "cinegraph_csrf"
    csrf_token_bytes: int = 32
    production_session_cookie_name: str = "__Host-cinegraph_session"
    production_csrf_cookie_name: str = "__Host-cinegraph_csrf"
    unsafe_methods: frozenset[str] = AUTHENTICATION_UNSAFE_METHODS
    trusted_same_origin_sec_fetch_sites: frozenset[str] = (
        TRUSTED_SAME_ORIGIN_SEC_FETCH_SITES
    )

    def __post_init__(self) -> None:
        positive = (
            self.minimum_password_length,
            self.maximum_password_length,
            self.minimum_email_length,
            self.maximum_email_length,
            self.minimum_display_name_length,
            self.maximum_display_name_length,
            self.maximum_active_authenticated_sessions,
            self.maximum_session_listing,
            self.csrf_token_bytes,
        )
        if any(isinstance(value, bool) or value < 1 for value in positive):
            raise ValueError("Authentication bounds must be positive integers.")
        if self.minimum_password_length > self.maximum_password_length:
            raise ValueError("Password bounds are invalid.")
        if self.minimum_email_length > self.maximum_email_length:
            raise ValueError("Email bounds are invalid.")
        if self.minimum_display_name_length > self.maximum_display_name_length:
            raise ValueError("Display-name bounds are invalid.")
        if not self.csrf_header_name or not self.csrf_cookie_name:
            raise ValueError("CSRF names must be non-empty.")
        if self.session_cookie_path != "/":
            raise ValueError("Authentication cookies must use Path=/.")
        if self.session_cookie_same_site not in {"lax", "strict"}:
            raise ValueError("Authentication cookies require SameSite=Lax or Strict.")
        if self.maximum_session_listing < self.maximum_active_authenticated_sessions:
            raise ValueError(
                "Session listing bound must cover the active-session cap."
            )
        if not self.production_session_cookie_name.startswith("__Host-"):
            raise ValueError("Production session cookie must use the __Host- prefix.")
        if not self.production_csrf_cookie_name.startswith("__Host-"):
            raise ValueError("Production CSRF cookie must use the __Host- prefix.")

    def session_cookie_name_for(self, production: bool) -> str:
        return (
            self.production_session_cookie_name
            if production
            else self.session_cookie_name
        )

    def csrf_cookie_name_for(self, production: bool) -> str:
        return (
            self.production_csrf_cookie_name if production else self.csrf_cookie_name
        )


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
