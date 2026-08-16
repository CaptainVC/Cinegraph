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

The layout includes semantic landmarks, visible focus states, a skip link,
screen-reader status regions, native dialog/form controls, keyboard submission,
responsive navigation, and a reduced-motion mode.
