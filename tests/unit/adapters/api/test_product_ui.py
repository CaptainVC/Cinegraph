import re
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
    assert "function readCookie(name)" in script.text
    assert 'readCookie("__Host-cinegraph_csrf") || readCookie("cinegraph_csrf")' in script.text
    assert "localStorage" not in script.text
    assert "sessionStorage" not in script.text
    assert icon.status_code == 200
    assert icon.headers["content-type"].startswith("image/svg+xml")


def test_product_shell_exposes_keyboard_and_drawer_contracts(tmp_path: Path) -> None:
    context, _ = make_context(tmp_path)
    with TestClient(create_app(context)) as client:
        page = client.get("/").text
        stylesheet = client.get("/assets/app.css").text
        script = client.get("/assets/app.js").text

    # Keep these checks semantic rather than formatting-sensitive: the source
    # contract protects the static shell even though the interaction runs in
    # the browser, outside FastAPI's Python test process.
    assert 'role="tablist"' in page
    assert 'role="tab"' in page
    assert re.search(r'id="mobile-scope-button"[^>]+aria-expanded="false"', page)
    assert 'id="scope-close-button"' in page
    assert 'id="scope-backdrop"' in page and 'tabindex="-1"' in page
    assert 'id="messages"' in page and 'aria-live="polite"' in page
    assert 'id="library-open-button"' in page
    assert "Browse episodes" in page
    assert 'id="library-dialog"' in page
    assert 'aria-labelledby="library-title"' in page
    assert 'id="library-close-button"' in page
    assert 'id="library-poster"' in page
    assert 'loading="lazy"' in page
    assert 'decoding="async"' in page
    assert 'id="library-season-list"' in page
    assert 'id="library-episode-list"' in page
    assert 'Series regulars' in page
    assert 'Episode guest credits' in page
    assert 'Show-level credits; appearance in this episode is not confirmed.' in page
    assert 'No episode guest credits are available.' in page

    assert "const UI_COPY = Object.freeze" in script
    assert "const AUTH_MODES = Object.freeze" in script
    assert "function handleAuthTabKeydown" in script
    for key in ("ArrowLeft", "ArrowRight", "Home", "End"):
        assert f'event.key === "{key}"' in script
    assert 'setAttribute("aria-selected", String(loginSelected))' in script
    assert "tabIndex = loginSelected ? 0 : -1" in script
    assert "focusAuthPanel" in script

    assert "function setScopeOpen" in script
    assert "function setElementIsolation" in script
    assert "function scopeFocusableElements" in script
    assert 'querySelector(".scope-backdrop")' in script
    assert 'event.key === "Escape"' in script
    assert 'event.key === "Tab"' in script
    assert 'classList.toggle("scope-scroll-locked", isOpen)' in script
    assert "setElementIsolation(elements.topbar, isOpen)" in script
    assert "setElementIsolation(elements.conversationPanel, isOpen)" in script
    assert 'setAttribute("role", "dialog")' in script
    assert 'setAttribute("aria-modal", "true")' in script
    assert 'setAttribute("aria-expanded", String(isOpen))' in script
    assert "const SCOPE_DRAWER_MEDIA = window.matchMedia(SCOPE_DRAWER_QUERY)" in script
    assert "SCOPE_DRAWER_MEDIA.matches" in script
    assert "instanceof HTMLElement" in script
    assert "function openLibrary" in script
    assert "function closeLibrary" in script
    assert "function libraryFocusableElements" in script
    assert "safeSameOriginMediaUrl" in script
    assert 'url.protocol === "https:"' in script
    assert "Reviewed cast metadata is not available for this series yet." in script
    assert 'target = "_blank"' in script
    assert 'rel = "noopener noreferrer"' in script
    assert 'textContent = credit.character_name' in script
    assert "selectedLibrarySeason" in script
    assert "selectedLibraryEpisodeId" in script
    assert '.library-season-button[aria-pressed="true"]' in script
    assert '.library-episode-button[aria-pressed="true"]' in script
    assert "overscroll-behavior: contain" in stylesheet
    assert "elements.libraryDialog.addEventListener(\"cancel\"" in script
    assert "elements.libraryDialog.addEventListener(\"close\"" in script
    assert "innerHTML" not in script

    assert 'setAttribute("aria-busy", String(busy))' in script
    assert "button.disabled = busy" in script
    assert "button.textContent =" not in script
    assert "(prefers-reduced-motion: reduce)" in script
    assert 'behavior: reducedMotion ? "auto" : "smooth"' in script

    # Identity never moves into browser persistence, including new UI state.
    assert "localStorage" not in script
    assert "sessionStorage" not in script


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
    assert response.headers["Permissions-Policy"] == ("camera=(), microphone=(), geolocation=()")
    assert response.headers["Cross-Origin-Opener-Policy"] == "same-origin"
