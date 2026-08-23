
from collections.abc import Collection

from cinegraph.domain.enums.enum import SpoilerMode
from cinegraph.domain.models.watch_state.profile_watch_state import (
    EpisodeRef,
    ProfileWatchState,
)


class SpoilerPolicy:

    # Return whether every evidence episode is accessible under the profile's spoiler mode.
    def can_access(
            self,
            evidence_episode_refs: Collection[EpisodeRef],
            watch_state: ProfileWatchState | None,
    ) -> bool:
        evidence = frozenset(evidence_episode_refs)

        if not evidence:
            return False
        return evidence == self.accessible_episode_refs(
            evidence_episode_refs=evidence,
            watch_state=watch_state,
        )

    # Return the subset of evidence episodes currently accessible to the profile.
    def accessible_episode_refs(
            self,
            evidence_episode_refs: Collection[EpisodeRef],
            watch_state: ProfileWatchState | None,
    ) -> frozenset[EpisodeRef]:
        if watch_state is None:
            return frozenset()
        return frozenset(
            episode_ref
            for episode_ref in evidence_episode_refs
            if self._can_access_episode(episode_ref, watch_state)
        )

    # Return a partial-watch cutoff when an episode is not fully accessible.
    def partial_safe_until_ms_for(
            self,
            episode_ref: EpisodeRef,
            watch_state: ProfileWatchState | None,
    ) -> int | None:
        if watch_state is None:
            return None
        if self._can_access_episode(episode_ref, watch_state):
            return None

        series_watch_state = watch_state.series_watch_state_for(episode_ref.series_id)
        if series_watch_state is None:
            return None
        return series_watch_state.safe_until_ms_for(episode_ref)

    # Apply relaxed, completed, manual, strict, or sequential access rules to one episode.
    def _can_access_episode(
            self,
            episode_ref: EpisodeRef,
            watch_state: ProfileWatchState,
    ) -> bool:
        if watch_state.spoiler_mode is SpoilerMode.RELAXED:
            return True

        series_watch_state = watch_state.series_watch_state_for(episode_ref.series_id)
        if series_watch_state is None:
            return False

        if series_watch_state.is_fully_watched(episode_ref):
            return True
        if episode_ref in series_watch_state.manually_allowed_episodes:
            return True
        if watch_state.spoiler_mode is SpoilerMode.STRICT:
            return False

        boundary = series_watch_state.sequential_safe_boundary
        return boundary is not None and episode_ref.position <= boundary.position
