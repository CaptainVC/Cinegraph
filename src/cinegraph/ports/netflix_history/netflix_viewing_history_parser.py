from typing import Protocol

from cinegraph.application.models.netflix_history import (
    NetflixHistoryUpload,
    ParsedNetflixViewingHistory,
)


class NetflixViewingHistoryParser(Protocol):
    def parse(self, upload: NetflixHistoryUpload) -> ParsedNetflixViewingHistory: ...
