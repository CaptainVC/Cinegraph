import secrets

from cinegraph.config import (
    DEFAULT_AUTHENTICATION_CONFIGURATION,
    AuthenticationConfiguration,
)


class SecureSessionTokenGenerator:
    def __init__(
        self,
        configuration: AuthenticationConfiguration = (
            DEFAULT_AUTHENTICATION_CONFIGURATION
        ),
    ) -> None:
        self._configuration = configuration

    def generate(self) -> str:
        return secrets.token_urlsafe(self._configuration.session_token_bytes)
