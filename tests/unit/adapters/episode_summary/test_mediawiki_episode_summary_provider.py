from datetime import UTC, datetime

import httpx
import pytest

from cinegraph.adapters.episode_summary.mediawiki_episode_summary_provider import (
    MediaWikiEpisodeSummaryProvider,
)
from cinegraph.domain.enums.enum import Language
from cinegraph.domain.exceptions.mediawiki_errors import (
    MediaWikiPageNotFoundError,
    MediaWikiProviderError,
)


FIXED_TIME = datetime(2026, 7, 21, 12, 0, tzinfo=UTC)
PAGE_TITLE = "Pilot (Modern Family)"
CANONICAL_URL = "https://en.wikipedia.org/wiki/Pilot_(Modern_Family)"


class FixedClock:
    def now_utc(self) -> datetime:
        return FIXED_TIME


def build_provider(handler) -> MediaWikiEpisodeSummaryProvider:
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return MediaWikiEpisodeSummaryProvider(client=client, clock=FixedClock())


def valid_page_payload() -> dict:
    return {
        "query": {
            "pages": {
                "123": {
                    "pageid": 123,
                    "title": PAGE_TITLE,
                    "fullurl": CANONICAL_URL,
                    "extract": "A high-level episode plot summary.",
                    "revisions": [
                        {
                            "revid": 456,
                            "timestamp": "2026-07-21T10:00:00Z",
                        }
                    ],
                }
            }
        }
    }


def test_fetches_versioned_episode_summary_with_provenance() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://en.wikipedia.org/w/api.php?action=query&format=json&redirects=1&prop=extracts%7Cinfo%7Crevisions&explaintext=1&inprop=url&rvprop=ids%7Ctimestamp&rvlimit=1&titles=Pilot+%28Modern+Family%29"
        return httpx.Response(200, json=valid_page_payload())

    summary = build_provider(handler).fetch(
        page_title=PAGE_TITLE,
        language=Language.ENGLISH,
    )

    assert summary.page_title == PAGE_TITLE
    assert summary.canonical_url == CANONICAL_URL
    assert summary.revision_id == 456
    assert summary.revision_timestamp == datetime(2026, 7, 21, 10, 0, tzinfo=UTC)
    assert summary.retrieved_at == FIXED_TIME
    assert summary.text == "A high-level episode plot summary."
    assert summary.language is Language.ENGLISH
    assert summary.attribution == "Wikipedia contributors, CC BY-SA"


def test_rejects_untrimmed_page_title() -> None:
    provider = build_provider(lambda request: httpx.Response(500))

    with pytest.raises(ValueError, match="page title"):
        provider.fetch(
            page_title=f" {PAGE_TITLE}",
            language=Language.ENGLISH,
        )


def test_raises_not_found_for_missing_page() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"query": {"pages": {"-1": {"missing": "", "title": PAGE_TITLE}}}},
        )

    with pytest.raises(MediaWikiPageNotFoundError, match="was not found"):
        build_provider(handler).fetch(
            page_title=PAGE_TITLE,
            language=Language.ENGLISH,
        )


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"query": {"pages": {"123": {"title": PAGE_TITLE}}}},
        {
            "query": {
                "pages": {
                    "123": {
                        "title": PAGE_TITLE,
                        "fullurl": CANONICAL_URL,
                        "extract": "",
                        "revisions": [
                            {"revid": 456, "timestamp": "2026-07-21T10:00:00Z"}
                        ],
                    }
                }
            }
        },
    ],
)
def test_rejects_missing_or_blank_required_mediawiki_data(payload: dict) -> None:
    provider = build_provider(lambda request: httpx.Response(200, json=payload))

    with pytest.raises(MediaWikiProviderError):
        provider.fetch(
            page_title=PAGE_TITLE,
            language=Language.ENGLISH,
        )


def test_propagates_http_status_error() -> None:
    provider = build_provider(lambda request: httpx.Response(503))

    with pytest.raises(httpx.HTTPStatusError):
        provider.fetch(
            page_title=PAGE_TITLE,
            language=Language.ENGLISH,
        )