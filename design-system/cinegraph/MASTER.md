# Cinegraph design system

> Page-specific rules in `pages/` may refine this file but cannot weaken its
> accessibility, privacy, performance, or security requirements.

**Product:** Cinegraph

**Direction:** cinematic intelligence workspace

**Design dials:** variance 6/10 · motion 5/10 · density 6/10

**Implementation:** semantic HTML, modern CSS, and dependency-free JavaScript

## Product principles

1. **Evidence is the interface.** Answers, transcript moments, cast, and graph
   relationships must feel connected and inspectable, never like decorative AI.
2. **Cinematic, not theatrical.** Use deep ink surfaces, soft projected light,
   precise typography, and a restrained mint/violet/gold spectrum. Avoid neon,
   glass everywhere, or entertainment-site clutter.
3. **Progressive disclosure.** Make the next action obvious, keep advanced scope
   controls nearby, and reveal evidence detail on demand.
4. **Honest state.** Loading, guest limits, spoiler boundaries, failures, and
   source provenance must be visible in language as well as color.
5. **Fast by default.** No remote fonts, UI libraries, trackers, or animation
   runtimes. Preserve the same-origin CSP and reserve space for future media.

## Tokens

Components must consume semantic CSS custom properties; raw colors belong only
in `:root`. The app is intentionally dark-only for this release.

| Role | Value | CSS token |
|---|---:|---|
| Canvas | `#070A12` | `--color-canvas` |
| Canvas elevated | `#0B0F19` | `--color-canvas-elevated` |
| Surface | `#111725` | `--color-surface` |
| Raised surface | `#171F30` | `--color-surface-raised` |
| Interactive surface | `#1D2638` | `--color-surface-interactive` |
| Primary text | `#F4F7FC` | `--color-text` |
| Secondary text | `#A9B4C8` | `--color-text-muted` |
| Tertiary text | `#8491A8` | `--color-text-subtle` |
| Hairline | `rgba(199, 211, 234, .13)` | `--color-line` |
| Strong line | `rgba(199, 211, 234, .24)` | `--color-line-strong` |
| Brand/action | `#76E6C2` | `--color-accent` |
| On brand/action | `#06110D` | `--color-on-accent` |
| Relationship | `#A99AF4` | `--color-relationship` |
| Provenance | `#F1C979` | `--color-provenance` |
| Destructive | `#FF9A9A` | `--color-danger` |
| Focus | `#B9FFF0` | `--color-focus` |

Use a 4px spacing base: `4, 8, 12, 16, 20, 24, 32, 40, 48, 64, 80`.
Use radii `8, 12, 16, 24, 999px`; reserve the pill radius for badges and compact
actions. Use borders and tonal contrast before shadow. Modal/shell shadows may
use `0 24px 80px rgba(0, 0, 0, .38)`.

## Typography

- UI/display: `Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont,
  "Segoe UI", sans-serif`. Do not fetch Inter; the system stack is the fallback.
- Editorial accent: `Iowan Old Style, Baskerville, Georgia, serif`, limited to
  large landing statements or quotations.
- Body: 15–17px, line-height 1.55–1.75. Metadata: at least 12px.
- Headings use the UI family in the workspace and may use editorial accents on
  the landing page. Never use all-caps for more than short metadata labels.
- Use `clamp()` for fluid display type and avoid truncating user or evidence text.

## Layout

- Content widths: marketing `1180px`, workspace `1480px`, readable answer `840px`.
- Breakpoints are content-driven, with required checks at 375, 768, 1024, and
  1440px. No horizontal page scroll at 320px or wider.
- Desktop workspace: stable scope rail plus a flexible primary pane. Tablet and
  mobile: the rail becomes a labelled, dismissible drawer with backdrop.
- Account for sticky chrome using `scroll-padding`; focused elements must remain
  visible. Use safe-area insets for mobile bottom controls.
- Poster/media slots must declare aspect ratio and dimensions before loading.

## Components and states

- Pointer targets are at least 44px where space allows and never below WCAG's
  24px minimum. Keep at least 8px between adjacent compact controls.
- All controls have visible hover, active, disabled, busy, and `:focus-visible`
  states. Focus uses a solid 2px ring with a 3px offset.
- Use native buttons, links, details, dialog, forms, and headings. Icon-only
  buttons require an accessible name; decorative SVGs use `aria-hidden="true"`.
- Buttons may rise by at most 1px on hover; no scale or layout-shifting effects.
- Forms keep persistent visible labels, autocomplete, paste, inline errors, and
  busy labels. Authentication must remain password-manager friendly.
- Status and access limits use icon/text plus color. Dynamic status announcements
  use one contextual `aria-live` or `role=status` region without moving focus.
- Dialog tabs implement roving focus and ArrowLeft/ArrowRight/Home/End keys.
- A mobile drawer closes on Escape, backdrop activation, or successful scope
  selection, returns focus to its trigger, and never traps focus behind itself.

## Motion

- Standard transitions: 160–220ms; panel entrance: at most 280ms. Animate only
  opacity and transform. Loading indicators may loop; decorative motion may not.
- Use CSS only. Do not add GSAP or another runtime.
- Under `prefers-reduced-motion: reduce`, remove nonessential transitions,
  transforms, smooth scrolling, and looping decorative effects.

## Accessibility and quality gates

- WCAG 2.2 AA target: text contrast ≥4.5:1, large text ≥3:1, non-text state and
  focus contrast ≥3:1; meaning never conveyed by color alone.
- Logical tab/read order, skip link, sequential headings, complete keyboard use,
  labelled controls, and descriptive image alternatives are mandatory.
- Avoid emoji/symbol glyphs as UI icons. Use a consistent, inline SVG icon set.
- No third-party scripts or remote assets. No local/session storage for identity.
- Test 375, 768, 1024, and 1440px; keyboard-only; reduced motion; 200% zoom;
  long content; empty/loading/error states; and slow/unavailable network states.

## Anti-patterns

No purple-gradient template aesthetic, indiscriminate glassmorphism, hidden
navigation, hover-only actions, auto-rotating content, parallax, fake metrics,
unlabelled icon buttons, raw transcript dumps, or decorative graphs without an
evidence purpose.
