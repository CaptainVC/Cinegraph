import base64
import hashlib
import hmac
import secrets

from cinegraph.common.error_messages import AuthenticationErrorMessages
from cinegraph.config import (
    DEFAULT_AUTHENTICATION_CONFIGURATION,
    AuthenticationConfiguration,
)


class ScryptPasswordHasher:
    def __init__(
        self,
        configuration: AuthenticationConfiguration = (
            DEFAULT_AUTHENTICATION_CONFIGURATION
        ),
    ) -> None:
        self._configuration = configuration

    def hash_password(self, password: str) -> str:
        self._validate_password(password)
        salt = secrets.token_bytes(self._configuration.password_salt_bytes)
        digest = self._derive(password, salt)
        return "$".join(
            (
                "scrypt",
                str(self._configuration.scrypt_cost),
                str(self._configuration.scrypt_block_size),
                str(self._configuration.scrypt_parallelization),
                base64.urlsafe_b64encode(salt).decode("ascii"),
                base64.urlsafe_b64encode(digest).decode("ascii"),
            )
        )

    def verify_password(self, password: str, encoded_hash: str) -> bool:
        try:
            algorithm, cost, block_size, parallelization, salt_value, digest_value = (
                encoded_hash.split("$")
            )
            if algorithm != "scrypt":
                return False
            salt = base64.urlsafe_b64decode(salt_value.encode("ascii"))
            expected = base64.urlsafe_b64decode(digest_value.encode("ascii"))
            derived = hashlib.scrypt(
                password.encode("utf-8"),
                salt=salt,
                n=int(cost),
                r=int(block_size),
                p=int(parallelization),
                dklen=len(expected),
            )
        except (TypeError, ValueError):
            return False
        return hmac.compare_digest(derived, expected)

    def _derive(self, password: str, salt: bytes) -> bytes:
        return hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=self._configuration.scrypt_cost,
            r=self._configuration.scrypt_block_size,
            p=self._configuration.scrypt_parallelization,
            dklen=32,
        )

    def _validate_password(self, password: str) -> None:
        if (
            not isinstance(password, str)
            or len(password) < self._configuration.minimum_password_length
            or len(password) > self._configuration.maximum_password_length
        ):
            raise ValueError(
                AuthenticationErrorMessages.PASSWORD_LENGTH_MUST_BE_VALID
            )
