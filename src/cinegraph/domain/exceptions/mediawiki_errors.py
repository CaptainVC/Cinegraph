class MediaWikiProviderError(RuntimeError):
    """Raised when a MediaWiki response is invalid or unavailable."""


class MediaWikiPageNotFoundError(LookupError):
    """Raised when MediaWiki cannot resolve the requested page."""