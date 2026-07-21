from datetime import datetime

import httpx

from cinegraph.common.error_messages import SourceErrorMessages
from cinegraph.domain.enums.enum import Language
from cinegraph.domain.exceptions.mediawiki_errors import (
    MediaWikiPageNotFoundError,
    MediaWikiProviderError,
)
from cinegraph.ports.date_time.clock import Clock
from cinegraph.ports.dto.fetched_episode_summary import FetchedEpisodeSummary
from cinegraph.ports.episode_summary.episode_summary_provider import (
    EpisodeSummaryProvider,
)


class MediaWikiEpisodeSummaryProvider(EpisodeSummaryProvider):

    def __init__(
        self,
        client: httpx.Client,
        clock: Clock,
    ) -> None:
        self._client = client
        self._clock = clock


    def fetch(
        self,
        *,
        page_title: str,
        language: Language,
    ) -> FetchedEpisodeSummary:

        if not page_title or page_title.strip() != page_title:
            raise ValueError(
                SourceErrorMessages.MEDIAWIKI_PAGE_TITLE_MUST_BE_TRIMMED
            )

        response = self._client.get(
            f"https://{language.value}.wikipedia.org/w/api.php",
            params={
                "action": "query",
                "format": "json",
                "redirects": "1",
                "prop": "extracts|info|revisions",
                "explaintext": "1",
                "inprop": "url",
                "rvprop": "ids|timestamp",
                "rvlimit": "1",
                "titles": page_title,
            },
        )
        response.raise_for_status()

        try:
            pages = response.json()["query"]["pages"]
            page = next(iter(pages.values()))
        except (KeyError, StopIteration, TypeError) as error:
            raise MediaWikiProviderError(
                SourceErrorMessages.MEDIAWIKI_RESPONSE_MISSING_REQUIRED_DATA
            ) from error

        if "missing" in page:
            raise MediaWikiPageNotFoundError(
                SourceErrorMessages.MEDIAWIKI_PAGE_NOT_FOUND.format(
                    page_title=page_title
                )
            )

        try:
            revision = page["revisions"][0]
            text = page["extract"].strip()
            canonical_url = page["fullurl"]
            revision_id = revision["revid"]
            revision_timestamp = datetime.fromisoformat(
                revision["timestamp"].replace("Z", "+00:00")
            )
        except (KeyError, IndexError, TypeError, ValueError) as error:
            raise MediaWikiProviderError(
                SourceErrorMessages.MEDIAWIKI_RESPONSE_MISSING_REQUIRED_DATA
            ) from error

        if not text:
            raise MediaWikiProviderError(
                SourceErrorMessages.MEDIAWIKI_PAGE_EXTRACT_MUST_BE_TRIMMED
            )

        return FetchedEpisodeSummary(
            page_title=page["title"],
            canonical_url=canonical_url,
            revision_id=revision_id,
            revision_timestamp=revision_timestamp,
            retrieved_at=self._clock.now_utc(),
            text=text,
            language=language,
            attribution="Wikipedia contributors, CC BY-SA",
        )
