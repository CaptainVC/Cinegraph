# Cinegraph product UI

The responsive web interface is served by the FastAPI application at `/`. Its
HTML, CSS, JavaScript, and SVG mark live inside the Python package and use no
third-party runtime assets. This keeps the first product surface same-origin and
compatible with the strict content-security policy.

The browser never receives or persists a session token. Guest, registration, and
login responses set the existing HTTP-only cookie, and frontend requests use
same-origin credentials. Text from users, models, errors, catalogue records, and
citations is inserted with `textContent`, not interpreted as HTML.

## User flow

1. The landing screen probes readiness and offers one-click guest entry.
2. Guest entry exposes only the server-filtered Modern Family season 1 and 2
   catalogue. Authenticated users receive their server-assigned catalogue scope.
3. A user can search the open scope, or select strict/sequential protection and
   a trusted catalogue episode boundary.
4. Chat results display an answer and expandable timestamped transcript evidence.
   A safe refusal is rendered explicitly when grounded evidence is unavailable.
5. Sign-in, account creation, and session termination share the same application
   shell and sanitized API error contracts.

## Episode library

The workspace header's **Browse episodes** action opens a native modal that keeps
the conversation, retrieval scope, and spoiler boundary intact. The surface is
limited to the series and seasons returned by the current session catalogue. It
shows the series poster when the API provides a same-origin, entitlement-
protected media URL, with a reserved 2:3 fallback slot when the asset is missing
or fails to load.

Season and episode controls update only the library detail. A selected episode
shows its position and title, then separates show-level **Series regulars** from
episode-specific **Episode guest credits**. Regular credits carry the explicit
disclaimer: “Show-level credits; appearance in this episode is not confirmed.”
Missing metadata and zero-credit states use honest text instead of invented
content. Provider attribution and optional canonical links are rendered as
safe external links with `noopener noreferrer`.

The browser rejects non-same-origin poster URLs and never receives raw provider
media URLs. Poster media is lazy and asynchronously decoded, and all catalogue
and cast values are inserted through DOM text APIs. The modal has a visible
44px close route, Escape support, focus return, keyboard containment, internal
scrolling, and responsive one-column behavior below the desktop layout.

The layout includes semantic landmarks, visible focus states, a skip link,
screen-reader status regions, native dialog/form controls, keyboard submission,
responsive navigation, and a reduced-motion mode.
