# Cinegraph product UI

The responsive web interface is served by the FastAPI application at `/`. Its
HTML, CSS, JavaScript, and SVG mark live inside the Python package and use no
third-party runtime assets. This keeps the first product surface same-origin and
compatible with the strict content-security policy.

The browser never receives or persists a session token. Guest, registration, and
login responses set the existing HTTP-only cookie, and frontend requests use
same-origin credentials. Text from users, models, errors, catalogue records, and
citations is inserted with `textContent`, not interpreted as HTML.

Before enabling authentication or guest actions, the shell loads the public,
same-origin `/client-config` bootstrap contract. It returns the configured API prefix
plus strict positive-integer timing values derived from the server's centralized
agent-job limits, including transport grace. Every browser API and agent-evidence URL
is built and validated against that prefix, so a non-default deployment cannot drift
from the server routes. Missing, extra, malformed, or unreachable runtime
configuration fails closed and leaves entry controls disabled. The same response is
also exposed below the configured API prefix for non-browser clients.

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

## Agent research and evidence trail

Composer submissions use the durable `<api-prefix>/agent/jobs` flow. Each request gets
an in-memory thread UUID and idempotency UUID; neither is persisted in browser
storage. The browser follows same-origin lifecycle events over `EventSource`,
then uses bounded status polling when events are unavailable or reconnecting. A
terminal status is fetched before the result is rendered, and its optional
`evidence_url` is fetched once as a same-origin batch. Active request, job,
thread, series, and scope revisions are correlated so stale responses cannot
overwrite a newer conversation state. Streams and timers close on logout, scope
changes, and session reset.

Agent status, events, and evidence URLs are accepted only when they are
same-origin, credential-free, fragment-free, query-free, and match the
canonical UUID returned for that job. Evidence hydration is an authorization
gate: the envelope must contain the matching `job_id`, one unique item for every
result citation, and a non-empty bounded excerpt for each item. Any mismatch
withholds the answer and reports that authorized evidence is unavailable.

Assistant answers expose an accessible **Evidence trail**. Transcript items show
episode position, timestamp, and a hydrated excerpt. Graph items preserve each
claim's subject, predicate, object, entity kinds, textual polarity, hop distance,
support count, and nested supporting moments. Conflicting polarities remain
separate claims. Internal tool names are translated to human labels, and legacy
or unavailable graph metadata receives an explicit honest state. All dynamic
content is created with DOM APIs and `textContent`; no graph runtime, unsafe URL,
browser storage, or raw HTML interpolation is used.

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

## Browser release contract

The `e2e` test suite launches the packaged FastAPI shell on an ephemeral localhost
origin and drives real headless Chromium. API boundaries are intercepted with
deterministic synthetic responses, so the gate requires no model key, database,
Qdrant instance, account, or private corpus. It verifies guest S1/S2 entitlement,
agent success and safe refusal, transcript and graph evidence hydration, fail-closed
evidence validation, SSE-to-poll fallback, text-only rendering of hostile content,
keyboard drawer behavior, and mobile horizontal-overflow protection.

Ordinary coverage runs exclude the `e2e` marker. The dedicated CI gate installs
Chromium and runs the browser suite without coverage instrumentation. On failure it
retains a screenshot and Playwright trace under `test-results/e2e`; the harness uses
synthetic fixtures exclusively and disables source capture in those traces.
