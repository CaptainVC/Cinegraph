class TVMazeProviderError(RuntimeError):
    """The TVmaze response or transport was not usable."""


class TVMazeEpisodeReconciliationError(TVMazeProviderError):
    """TVmaze episodes could not be reconciled to the catalogue exactly."""


class TVMazeShowMismatchError(TVMazeProviderError):
    """The explicit TVmaze show ID resolved to another title."""
