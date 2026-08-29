from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from tests.factories import DEFAULT_SERIES_ID
from tests.unit.adapters.api.test_fastapi_app import make_context, make_series_metadata

from cinegraph.adapters.api.fastapi_app import create_app
from cinegraph.adapters.api.schemas import ClientConfigurationResponse
from cinegraph.config import DEFAULT_API_CONFIGURATION


def test_client_config_is_public_and_contains_validated_runtime_integers(tmp_path: Path) -> None:
    context, _ = make_context(tmp_path)
    with TestClient(create_app(context)) as client:
        response = client.get("/client-config")
        prefixed_response = client.get("/api/v1/client-config")

    assert response.status_code == 200
    payload = response.json()
    assert prefixed_response.json() == payload
    assert set(payload) == {
        "api_prefix",
        "agent_poll_interval_ms",
        "agent_job_deadline_ms",
    }
    assert payload["api_prefix"] == "/api/v1"
    assert all(
        isinstance(payload[key], int) and payload[key] > 0
        for key in ("agent_poll_interval_ms", "agent_job_deadline_ms")
    )


def test_app_preserves_injected_identity_prefix_and_poster_url(tmp_path: Path) -> None:
    snapshot = make_series_metadata(tmp_path)
    context, _ = make_context(
        tmp_path,
        series_metadata={DEFAULT_SERIES_ID: snapshot},
    )
    configuration = replace(
        DEFAULT_API_CONFIGURATION,
        api_prefix="/internal/api",
        title="Cinegraph internal",
        version="38.0",
    )
    app = create_app(context, api_configuration=configuration)
    with TestClient(app) as client:
        shell_response = client.get("/client-config")
        response = client.get("/internal/api/client-config")
        old_path = client.get("/api/v1/client-config")
        guest = client.post("/internal/api/auth/guest")
        catalogue = client.get("/internal/api/catalogue")

    assert app.title == "Cinegraph internal"
    assert app.version == "38.0"
    assert response.status_code == 200
    assert shell_response.json()["api_prefix"] == "/internal/api"
    assert response.json() == shell_response.json()
    assert old_path.status_code == 404
    assert guest.status_code == 200
    assert catalogue.json()["series"][0]["poster"]["url"] == (
        f"/internal/api/series/{DEFAULT_SERIES_ID}/poster"
    )


def test_client_config_schema_rejects_coercible_non_integers() -> None:
    try:
        ClientConfigurationResponse(
            api_prefix="/api/v1",
            agent_poll_interval_ms=1.5,
            agent_job_deadline_ms=2_000,
        )
    except ValidationError:
        return
    raise AssertionError("client runtime schema must reject fractional values")


@pytest.mark.parametrize(
    "api_prefix",
    [
        "api/v1",
        "/api/v1/",
        "//api/v1",
        "/assets",
        "/assets/api",
        "/health",
        "/client-config",
        "/.",
        "/..",
        "/foo/./bar",
        "/foo/../bar",
    ],
)
def test_api_configuration_rejects_noncanonical_prefixes(api_prefix: str) -> None:
    with pytest.raises(ValueError, match="API prefix"):
        replace(DEFAULT_API_CONFIGURATION, api_prefix=api_prefix)
