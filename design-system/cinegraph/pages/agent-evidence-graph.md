# Agent evidence and relationship trail

> This page override extends `../MASTER.md`. It cannot weaken the master
> accessibility, privacy, performance, security, or entitlement requirements.

## Purpose and product boundary

Phase 37 makes the existing LangGraph series agent the browser's primary answer
path. Every completed answer must reveal which governed tools were used and must
connect each selected citation to an inspectable, currently authorized source.
The relationship view explains only claims selected for the current answer; it
is not a general-purpose or model-invented knowledge-graph browser.

The existing synchronous transcript chat remains an API compatibility surface,
but the product composer submits asynchronous agent jobs and follows their
queued, running, terminal, replay, and failure lifecycle.

## Information hierarchy

1. Keep the user's question and grounded answer as the primary reading flow.
2. While work is active, announce honest lifecycle states such as queued,
   searching evidence, reconnecting, or unavailable. Never show fake progress.
3. Below a grounded answer, show an **Evidence trail** summary with human labels
   for the transcript and relationship tools used in that turn.
4. Show transcript citations as episode/timestamp source cards with an excerpt
   only after the server reauthorizes and hydrates it.
5. Show graph citations as semantic subject → predicate → object relationship
   cards, including entity kind, polarity, distance, and supporting moments.
6. Group repeated graph evidence by claim without merging conflicting polarity.
7. Safe refusal contains no answer, graph, or citations. Hydration failure keeps
   the answer but states that source detail is no longer available in this scope.

## Agent job and scope interaction

- Each submission uses a fresh idempotency UUID and an in-memory conversation
  thread UUID. Identity, evidence, job, and graph state never enter browser
  local or session storage.
- The request carries the visible spoiler mode and trusted catalogue boundary,
  while the server derives candidate episodes and intersects them with corpus
  entitlement. The browser never supplies candidate IDs or an access scope.
- A series, spoiler-mode, or boundary change starts a new thread. The server also
  binds a thread to its exact candidate episode set so a reused identifier cannot
  import broader checkpoint history.
- Follow only same-origin status, event, and evidence URLs. Ignore a terminal
  result whose job, thread, series, or active request token does not match the
  current submission.
- Use SSE for replayable lifecycle updates and an owner-scoped status fetch for
  the terminal result. Provide a bounded status-polling fallback when streaming
  is unavailable, and stop all activity on logout, session expiry, or supersession.

## Evidence authorization contract

- Durable agent jobs retain answer text, governed citation locators, bounded
  graph structure, and tool names, but never raw transcript text, prompts,
  runtime context, private paths, or provider payloads.
- A completed-job evidence endpoint resolves only citations already selected in
  that owner-scoped job. Arbitrary client segment, claim, or evidence IDs never
  authorize a read.
- Hydration recompiles visibility from the job's captured candidate set and the
  current principal entitlement, then rechecks rights, active source, approved
  review, index/extraction revision, series, episode, timestamp, and citation ID.
- Unknown, cross-owner, stale, revoked, and mismatched evidence fail closed with
  indistinguishable not-found behavior. Evidence responses are private/no-store.
- Guest results and hydrated evidence are limited to Modern Family seasons 1–2.
  Authenticated-only corpus must not be discoverable through job status, SSE,
  relationship labels, node counts, evidence locations, or errors.

## Relationship presentation

- The semantic ordered relationship list is the accessible source of truth.
  A small dependency-free CSS/inline-SVG connector may reinforce the path but is
  decorative (`aria-hidden="true"`) and may not contain untrusted markup.
- Do not use a force-directed layout, canvas-only graph, drag requirement, graph
  library, remote asset, or animated simulation. Selected agent citations are a
  bounded explanation, not a data-exploration canvas.
- Present subject and object as text-labelled nodes and predicate as a direct
  edge label. Include entity kind and polarity in text; color only reinforces.
- Preserve asserted, negated, and uncertain relationships as distinct claims.
  Do not infer a canonical path from hop distance or imply causality that the
  server did not return.
- Long names, predicates, IDs, and excerpts wrap with `overflow-wrap:anywhere`.
  Never truncate essential evidence or let it create horizontal page scrolling.

## Components and behavior

- Keep evidence inside the assistant message so the answer and its provenance
  remain one reading unit. Use native `details`/`summary` for source expansion.
- Summaries are at least 44px high, have visible open/closed state beyond color,
  and keep keyboard focus unobscured by the composer or sticky chrome.
- Transcript cards expose episode position, timestamp range, and exact excerpt.
  Relationship cards expose the claim and nested supporting episode moments.
- Tool names are mapped through centralized copy to “Transcript search” and
  “Relationship search”; raw internal identifiers are not primary UI language.
- Loading uses one polite status region without moving focus. Completion does
  not auto-focus evidence. The composer regains focus only after the active turn
  is terminal and its result has been rendered.
- Error copy maps stable public failure codes to actionable language and never
  renders raw exceptions. A retry creates a new idempotency key.
- Dynamic model, graph, evidence, and error values use `textContent` and DOM
  construction only. Do not use `innerHTML` or dynamic SVG markup.

## Responsive and performance rules

- Preserve the 840px readable answer measure. Evidence cards stack within that
  column at every breakpoint; a relationship may use a three-part row only when
  content fits without truncation.
- At 375px and 200% zoom, relationship nodes and edge copy stack vertically in
  DOM order and use one page/message scroll route with no horizontal overflow.
- Render only the bounded citations selected by the agent. Hydrate evidence in
  one batch request after terminal status rather than one request per card.
- No new third-party JavaScript or visualization runtime. Animate only opacity
  or transform for 160–220ms, and remove nonessential motion under reduced motion.

## Required states and acceptance checks

- Transcript-only, graph-only, mixed-tool, safe-refusal, no-relationship,
  hydration-unavailable, malformed-result, queued, running, reconnecting,
  timeout, failed-job, unavailable-service, and expired-session states.
- Duplicate citations are suppressed; duplicate claim evidence is grouped;
  conflicting polarities remain visibly separate.
- Guest Season 3/cross-series/cross-user/stale evidence cannot affect labels,
  counts, nodes, edges, locators, or error detail.
- Keyboard-only operation, visible focus, native disclosure semantics, 44px
  targets, reduced motion, 200% zoom, long content, and 375/768/1024/1440px.
- No `innerHTML`, unsafe external navigation, local/session storage, remote
  assets, private transcript content in SSE/audit events, or stale-result races.
