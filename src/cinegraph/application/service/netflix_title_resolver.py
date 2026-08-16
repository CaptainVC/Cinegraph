import re
import unicodedata
from dataclasses import dataclass

from cinegraph.application.models.netflix_history import (
    NetflixEpisodeCandidate,
    NetflixTitleResolution,
    NetflixViewingHistoryRow,
)
from cinegraph.config.netflix_history import (
    NETFLIX_LONG_EPISODE_PATTERN,
    NETFLIX_SEASON_EPISODE_TITLE_PATTERN,
    NETFLIX_SEASON_TITLE_PATTERN,
    NETFLIX_SERIES_TITLE_PATTERN,
)
from cinegraph.domain.enums.enum import NetflixTitleResolutionStatus
from cinegraph.domain.models.catalogue.catalogue_manifest import CatalogueManifest
from cinegraph.domain.models.watch_state import EpisodePosition, EpisodeRef


@dataclass(frozen=True, slots=True)
class _CatalogueEpisode:
    candidate: NetflixEpisodeCandidate
    normalized_series: str
    normalized_title: str


class NetflixTitleResolver:
    def __init__(self, catalogue: CatalogueManifest) -> None:
        self._episodes = tuple(
            _CatalogueEpisode(
                candidate=NetflixEpisodeCandidate(
                    episode=EpisodeRef(
                        series_id=series.series_id,
                        season_id=season.season_id,
                        episode_id=episode.episode_id,
                        position=EpisodePosition(
                            season.season_number,
                            episode.episode_number,
                        ),
                    ),
                    series_name=series.series_name,
                    season_number=season.season_number,
                    episode_number=episode.episode_number,
                    episode_title=episode.episode_title,
                    reason="deterministic catalogue match",
                ),
                normalized_series=self._normalize(series.series_name),
                normalized_title=self._normalize(episode.episode_title or ""),
            )
            for series in catalogue.series
            for season in series.seasons
            for episode in season.episodes
        )

    def resolve(self, row: NetflixViewingHistoryRow) -> NetflixTitleResolution:
        candidates = self._resolve_candidates(row.title)
        status = (
            NetflixTitleResolutionStatus.MATCHED
            if len(candidates) == 1
            else NetflixTitleResolutionStatus.AMBIGUOUS
            if candidates
            else NetflixTitleResolutionStatus.UNMATCHED
        )
        return NetflixTitleResolution(row, status, candidates)

    def _resolve_candidates(self, title: str) -> tuple[NetflixEpisodeCandidate, ...]:
        for pattern, matcher in (
            (NETFLIX_SEASON_EPISODE_TITLE_PATTERN, self._match_numbered),
            (NETFLIX_LONG_EPISODE_PATTERN, self._match_numbered),
            (NETFLIX_SEASON_TITLE_PATTERN, self._match_season_title),
            (NETFLIX_SERIES_TITLE_PATTERN, self._match_series_title),
        ):
            match = re.fullmatch(pattern, title, flags=re.IGNORECASE)
            if match is not None:
                return matcher(match.groupdict())
        normalized_title = self._normalize(title)
        return self._candidates(
            episode
            for episode in self._episodes
            if episode.normalized_title == normalized_title
        )

    def _match_numbered(
        self, values: dict[str, str | None]
    ) -> tuple[NetflixEpisodeCandidate, ...]:
        series = self._normalize(values["series"] or "")
        season_number = int(values["season"] or 0)
        episode_number = int(values["episode"] or 0)
        return self._candidates(
            episode
            for episode in self._episodes
            if episode.normalized_series == series
            and episode.candidate.season_number == season_number
            and episode.candidate.episode_number == episode_number
        )

    def _match_season_title(
        self, values: dict[str, str | None]
    ) -> tuple[NetflixEpisodeCandidate, ...]:
        series = self._normalize(values["series"] or "")
        title = self._normalize(values["title"] or "")
        season_number = int(values["season"] or 0)
        return self._candidates(
            episode
            for episode in self._episodes
            if episode.normalized_series == series
            and episode.candidate.season_number == season_number
            and episode.normalized_title == title
        )

    def _match_series_title(
        self, values: dict[str, str | None]
    ) -> tuple[NetflixEpisodeCandidate, ...]:
        series = self._normalize(values["series"] or "")
        title = self._normalize(values["title"] or "")
        return self._candidates(
            episode
            for episode in self._episodes
            if episode.normalized_series == series
            and episode.normalized_title == title
        )

    @staticmethod
    def _candidates(episodes) -> tuple[NetflixEpisodeCandidate, ...]:
        return tuple(
            episode.candidate
            for episode in sorted(
                episodes,
                key=lambda value: (
                    value.candidate.series_name.casefold(),
                    value.candidate.season_number,
                    value.candidate.episode_number,
                ),
            )
        )

    @staticmethod
    def _normalize(value: str) -> str:
        normalized = unicodedata.normalize("NFKC", value).casefold()
        return " ".join(re.sub(r"[^\w]+", " ", normalized).split())
