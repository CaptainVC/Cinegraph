# Series library and episode detail

> This page override extends `../MASTER.md`. It cannot weaken the master
> accessibility, privacy, performance, or security requirements.

## Purpose and information hierarchy

The library is a focused inspection surface reached from the conversation
workspace. Chat remains the primary task; opening the library must not discard
conversation state, retrieval scope, or the current spoiler boundary.

1. Lead with the selected series poster, title, and the exact seasons available
   to the current session.
2. Present seasons as visible native controls and episodes as a scannable list.
3. Present the selected episode title and position before cast detail.
4. Separate **Series regulars** from **Episode guest credits**. Series-level
   credits must explicitly say that appearance in the selected episode is not
   confirmed.
5. Keep TVmaze attribution and license information visible near the enriched
   data, with provider links opening safely in a new tab.

## Interaction model

- Use a native `dialog` opened by a labelled “Browse episodes” button. Provide
  a visible close control, Escape support, focus containment, and focus return.
- Season controls and episode controls are keyboard reachable native buttons.
  Selection is expressed by text/ARIA state as well as color.
- Choosing an episode updates only the library detail. It must not silently
  change chat scope or spoiler protection.
- Empty metadata, missing poster, image failure, and zero guest-credit states
  each have honest copy. Never invent cast or episode data.
- At desktop widths, use poster/season navigation, episode list, and episode
  detail columns. Collapse to one vertical flow at tablet/mobile widths with no
  horizontal scrolling at 320px or wider.

## Poster and media rules

- The API supplies only a same-origin, entitlement-protected poster URL. Never
  hotlink provider media or relax the application CSP.
- Reserve a 2:3 poster slot before loading. Include intrinsic width and height
  when known, `object-fit: cover`, a meaningful series-specific `alt`, and a
  styled text fallback when the image is absent or fails.
- The poster is supporting content, not the only way to identify a series.
- Provider media is cached outside Git under the configured knowledge root.

## Cast semantics

- Regular credits are labelled “Series regulars” and accompanied by:
  “Show-level credits; appearance in this episode is not confirmed.”
- Guest credits are labelled “Episode guest credits” and apply only to the
  selected episode.
- Each credit presents person name followed by character name. Provider links
  are optional enhancement; names and roles must remain useful without them.
- Long names and character lists wrap. Do not truncate essential content.

## Security and entitlement invariants

- The server filters the catalogue by the session corpus scope before merging
  poster or cast metadata. Guests can discover only Modern Family seasons 1–2.
- A poster request performs the same entitlement check and returns 404 for a
  hidden, missing, unreviewed, or unavailable series asset.
- Remote provider URLs are retained only as reviewed provenance. The browser
  receives same-origin media plus safe canonical attribution links.
- Pending or rejected metadata never enters the runtime catalogue response.

## Required states and checks

- Checks: 375, 768, 1024, and 1440px; keyboard-only; 200% zoom; reduced motion;
  long names; no metadata; no poster; failed poster; no guest cast; many cast
  credits; guest season restriction; authenticated expanded corpus.
- The dialog must retain a usable close route at every size and scroll internally
  without obscuring the focused control.
- Below-the-fold poster loading is lazy and async-decoded; all media reserves
  space to prevent layout shift.
