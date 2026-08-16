import pytest

from cinegraph.adapters.identity import ScryptPasswordHasher
from cinegraph.common.error_messages import AuthenticationErrorMessages


def test_password_hashes_use_distinct_salts_and_verify_in_constant_format() -> None:
    hasher = ScryptPasswordHasher()
    password = "correct horse battery staple"

    first = hasher.hash_password(password)
    second = hasher.hash_password(password)

    assert first.startswith("scrypt$")
    assert first != second
    assert password not in first
    assert hasher.verify_password(password, first) is True
    assert hasher.verify_password("wrong password", first) is False


@pytest.mark.parametrize("password", ["short", "x" * 1025])
def test_password_length_bounds_are_enforced(password: str) -> None:
    with pytest.raises(
        ValueError,
        match=AuthenticationErrorMessages.PASSWORD_LENGTH_MUST_BE_VALID,
    ):
        ScryptPasswordHasher().hash_password(password)


@pytest.mark.parametrize("encoded", ["", "argon2$bad", "scrypt$bad"])
def test_malformed_stored_hash_fails_verification_without_raising(encoded: str) -> None:
    assert ScryptPasswordHasher().verify_password("valid password value", encoded) is False
