from collections.abc import Iterable
from uuid import UUID

from cinegraph.common.error_messages import WatchErrorMessages
from cinegraph.domain.models.watch_state.episode_watch_state import EpisodeRef


class InMemorySeasonEpisodeCatalog:
    # Index episode references by their series and season identifiers.
    def __init__(self, episode_refs: Iterable[EpisodeRef] = ()) -> None:
        episode_refs_by_season: dict[tuple[UUID, UUID], list[EpisodeRef]] = {}
        for episode_ref in episode_refs:
            key = (episode_ref.series_id, episode_ref.season_id)
            episode_refs_by_season.setdefault(key, []).append(episode_ref)

        self._episode_refs_by_season: dict[
            tuple[UUID, UUID], tuple[EpisodeRef, ...]
        ] = {}
        for key, season_episode_refs in episode_refs_by_season.items():
            episode_ids = {episode_ref.episode_id for episode_ref in season_episode_refs}
            if len(episode_ids) != len(season_episode_refs):
                raise ValueError(
                    WatchErrorMessages.SEASON_CATALOG_CANNOT_HAVE_DUPLICATE_EPISODE_IDS
                )
            self._episode_refs_by_season[key] = tuple(
                sorted(season_episode_refs, key=lambda episode_ref: episode_ref.position)
            )

    # Return the episode references catalogued for a series season, if indexed.
    def get_episode_refs(
        self,
        series_id: UUID,
        season_id: UUID,
    ) -> tuple[EpisodeRef, ...] | None:
        return self._episode_refs_by_season.get((series_id, season_id))
