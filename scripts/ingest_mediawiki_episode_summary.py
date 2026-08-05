from __future__ import annotations

import argparse
from uuid import UUID

import httpx

from cinegraph.adapters.date_time.system_clock import SystemClock
from cinegraph.adapters.episode_summary.mediawiki_episode_summary_provider import (
    MediaWikiEpisodeSummaryProvider,
)
from cinegraph.adapters.repository.in_memory.in_memory_episode_summary_ingestion_repository import (
    InMemoryEpisodeSummaryIngestionRepository,
)
from cinegraph.application.service.ingest_episode_summary_service import (
    IngestEpisodeSummaryService,
)
from cinegraph.common.identifiers import IdentifierGenerator
from cinegraph.domain.enums.enum import (
    Language,
    RightsStatus,
    SourceKind,
)
from cinegraph.domain.models.source.source_document import SourceDocument
from cinegraph.domain.models.watch_state import EpisodePosition, EpisodeRef
from cinegraph.ingestion.episode_summary.ingest_episode_summary import (
    IngestEpisodeSummaryCommand,
)


WIKIPEDIA_ORIGIN = "wikipedia"


def parse_uuid(value: str) -> UUID:
    try:
        return UUID(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError(
            f"Expected a UUID, received: {value}"
        ) from error


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Ingest one MediaWiki episode summary into CineGraph."
    )
    parser.add_argument("--series-id", required=True, type=parse_uuid)
    parser.add_argument("--season-id", required=True, type=parse_uuid)
    parser.add_argument("--episode-id", required=True, type=parse_uuid)
    parser.add_argument("--season-number", required=True, type=int)
    parser.add_argument("--episode-number", required=True, type=int)
    parser.add_argument("--page-title", required=True)
    parser.add_argument(
        "--rights-status",
        required=True,
        choices=[status.value for status in RightsStatus],
    )
    parser.add_argument(
        "--user-agent",
        required=True,
        help="A descriptive User-Agent with a contact method for MediaWiki.",
    )
    return parser


def main() -> None:

    # Parse command-line arguments and set up the ingestion service
    arguments = build_parser().parse_args()
    language = Language.ENGLISH
    rights_status = RightsStatus(arguments.rights_status)

    episode = EpisodeRef(
        series_id=arguments.series_id,
        season_id=arguments.season_id,
        episode_id=arguments.episode_id,
        position=EpisodePosition(
            season_number=arguments.season_number,
            episode_number=arguments.episode_number,
        ),
    )

    source_document = SourceDocument(
        source_document_id=(
            IdentifierGenerator.episode_summary_source_document_id(
                episode.episode_id,
                language,
                WIKIPEDIA_ORIGIN,
            )
        ),
        title=(
            "Wikipedia episode summary for "
            f"S{arguments.season_number:02}E{arguments.episode_number:02}"
        ),
        kind=SourceKind.EPISODE_PLOT,
        origin=WIKIPEDIA_ORIGIN,
    )

    command = IngestEpisodeSummaryCommand(
        source_document=source_document,
        page_title=arguments.page_title,
        episode=episode,
        language=language,
        rights_status=rights_status,
    )

    with httpx.Client(
        timeout=httpx.Timeout(10.0, connect=5.0),
        headers={"User-Agent": arguments.user_agent},
    ) as client:
        service = IngestEpisodeSummaryService(
            provider=MediaWikiEpisodeSummaryProvider(
                client=client,
                clock=SystemClock(),
            ),
            repository=InMemoryEpisodeSummaryIngestionRepository(),
        )

        result = service.execute(command)

    print(f"source_version_id={result.source_version.source_version_id}")
    print(f"content_hash={result.source_version.content_hash}")
    print(f"was_already_ingested={result.was_already_ingested}")

    if result.summary is not None:
        print(f"summary_id={result.summary.summary_id}")
        print(f"revision_id={result.summary.revision_id}")
        print(f"canonical_url={result.summary.canonical_url}")


if __name__ == "__main__":
    main()