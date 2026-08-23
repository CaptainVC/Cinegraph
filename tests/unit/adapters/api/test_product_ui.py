from pathlib import Path

from fastapi.testclient import TestClient
from tests.unit.adapters.api.test_fastapi_app import make_context

from cinegraph.adapters.api.fastapi_app import create_app


def test_product_shell_and_assets_are_served_same_origin(tmp_path: Path) -> None:
    context, _ = make_context(tmp_path)
    with TestClient(create_app(context)) as client:
        page = client.get("/")
        stylesheet = client.get("/assets/app.css")
        script = client.get("/assets/app.js")
        icon = client.get("/assets/favicon.svg")

    assert page.status_code == 200
    assert page.headers["content-type"].startswith("text/html")
    assert 'id="guest-start-button"' in page.text
    assert 'id="workspace-view"' in page.text
    assert 'aria-live="polite"' in page.text
    assert '<script defer src="/assets/app.js"></script>' in page.text
    assert stylesheet.status_code == 200
    assert "prefers-reduced-motion" in stylesheet.text
    assert script.status_code == 200
    assert 'credentials: "same-origin"' in script.text
    assert "/api/v1/auth/guest" in script.text
    assert "/api/v1/chat" in script.text
    assert "localStorage" not in script.text
    assert "sessionStorage" not in script.text
    assert icon.status_code == 200
    assert icon.headers["content-type"].startswith("image/svg+xml")


def test_ui_responses_receive_a_strict_same_origin_content_policy(
    tmp_path: Path,
) -> None:
    context, _ = make_context(tmp_path)
    with TestClient(create_app(context)) as client:
        response = client.get("/")

    policy = response.headers["Content-Security-Policy"]
    assert "default-src 'self'" in policy
    assert "script-src 'self'" in policy
    assert "object-src 'none'" in policy
    assert "frame-ancestors 'none'" in policy
    assert response.headers["Permissions-Policy"] == (
        "camera=(), microphone=(), geolocation=()"
    )
    assert response.headers["Cross-Origin-Opener-Policy"] == "same-origin"
