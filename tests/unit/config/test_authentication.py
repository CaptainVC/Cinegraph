from dataclasses import replace

import pytest

from cinegraph.config.authentication import DEFAULT_AUTHENTICATION_CONFIGURATION


@pytest.mark.parametrize(
    "changes",
    [
        {"session_cookie_path": "/auth"},
        {"session_cookie_same_site": "none"},
        {"production_session_cookie_name": "cinegraph_session"},
        {"production_csrf_cookie_name": "cinegraph_csrf"},
        {"maximum_session_listing": 4},
    ],
)
def test_authentication_configuration_rejects_insecure_or_inconsistent_values(
    changes: dict[str, object],
) -> None:
    with pytest.raises(ValueError):
        replace(DEFAULT_AUTHENTICATION_CONFIGURATION, **changes)


def test_authentication_configuration_exposes_host_cookie_names_and_bounded_caps() -> None:
    configuration = DEFAULT_AUTHENTICATION_CONFIGURATION

    assert configuration.session_cookie_path == "/"
    assert configuration.session_cookie_same_site in {"lax", "strict"}
    assert configuration.production_session_cookie_name.startswith("__Host-")
    assert configuration.production_csrf_cookie_name.startswith("__Host-")
    assert configuration.maximum_session_listing >= configuration.maximum_active_authenticated_sessions
