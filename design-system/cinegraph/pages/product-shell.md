# Product shell — landing, authentication, and conversation workspace

This page refines `../MASTER.md` for the Phase 35 shell. Later library, episode,
cast, evidence, and graph pages extend these foundations rather than replace them.

## Information architecture

1. A compact global header establishes product identity, service state, and
   session action. It must not imply pages that do not exist yet.
2. The signed-out landing pairs the core promise with a credible product preview:
   a question, grounded answer, and explicit transcript evidence. Primary CTA is
   guest access; account creation is secondary. Guest scope says exactly
   “Modern Family · Seasons 1–2”.
3. The signed-in/guest workspace gives the conversation the largest area. Corpus
   and spoiler controls form the secondary scope rail. Evidence remains attached
   to its answer.
4. Authentication stays a native modal dialog and never interrupts guest access.

## Visual direction

- Treat the canvas as a dark projection room. A restrained radial wash may sit
  behind the hero and shell, but controls use opaque, legible surfaces.
- Landing typography can pair a disciplined sans-serif with one editorial serif
  phrase. Workspace typography is sans-serif for scanning.
- Use an abstract evidence-path motif built from CSS or inline SVG; do not ship a
  fake show poster or remote media in this phase.
- Cards are differentiated by border, surface, and hierarchy—not excessive blur.

## Phase 35 acceptance contract

- Preserve every existing API path, DOM hook used by `app.js`, CSRF behavior,
  same-origin credentials, CSP, and guest/auth/chat flow.
- Centralize literal interface copy and configuration in immutable JavaScript
  objects when it is dynamic; design values live in CSS tokens.
- Replace glyph icons/arrows with a small accessible inline-SVG system.
- Add a semantic, responsive preview and stronger landing hierarchy without fake
  claims, fake customer logos, or fake usage data.
- Make the mobile scope drawer modal in behavior: backdrop, Escape close, trigger
  state, focus return, and inert/scroll-safe background behavior.
- Auth tabs support pointer and keyboard navigation with correct selected panel
  state. Dialog close and outside-click behavior remain available.
- Loading controls keep stable dimensions and expose busy state to assistive tech.
- Add unit contract tests for new semantic landmarks, accessibility hooks, and
  privacy/security invariants. Existing tests stay green.
