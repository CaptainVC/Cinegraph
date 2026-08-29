"""Construct the bounded watch state used by request-scoped retrieval."""

from collections.abc import Collection
from uuid import UUID

from cinegraph.common.error_messages import AgentJobErrorMessages
from cinegraph.domain.enums.enum import SpoilerMode
from cinegraph.domain.models.watch_state import EpisodeRef, ProfileWatchState, SeriesWatchState


def build_bounded_watch_state(
    profile_id: UUID,
    profile_name: str,
    series_id: UUID,
    episode_refs: Collection[EpisodeRef],
    spoiler_mode: SpoilerMode,
    safe_through_episode_id: UUID | None,
) -> ProfileWatchState:
    """Build one immutable policy from the server-owned episode candidates."""
    episodes = tuple(episode_refs)
    if spoiler_mode is SpoilerMode.RELAXED:
        if safe_through_episode_id is not None:
            raise ValueError(AgentJobErrorMessages.SPOILER_BOUNDARY_INVALID)
        return ProfileWatchState(
            profile_id=profile_id,
            profile_name=profile_name,
            spoiler_mode=SpoilerMode.RELAXED,
        )
    boundary = next(
        (episode for episode in episodes if episode.episode_id == safe_through_episode_id),
        None,
    )
    if boundary is None:
        raise ValueError(AgentJobErrorMessages.SPOILER_BOUNDARY_INVALID)
    return ProfileWatchState(
        profile_id=profile_id,
        profile_name=profile_name,
        series_watch_states=(
            SeriesWatchState(
                series_id=series_id,
                manually_allowed_episodes=(
                    frozenset(
                        episode for episode in episodes if episode.position <= boundary.position
                    )
                    if spoiler_mode is SpoilerMode.STRICT
                    else frozenset()
                ),
                sequential_safe_boundary=(
                    boundary if spoiler_mode is SpoilerMode.SEQUENTIAL else None
                ),
            ),
        ),
        spoiler_mode=spoiler_mode,
    )
