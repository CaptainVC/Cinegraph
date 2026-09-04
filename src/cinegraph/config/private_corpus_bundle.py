"""Application-facing re-export of the stdlib-only bundle policy."""

from cinegraph.common.private_corpus_policy import (
    DEFAULT_PRIVATE_CORPUS_BUNDLE_CONFIGURATION,
    PrivateCorpusBundleConfiguration,
)

__all__ = [
    "DEFAULT_PRIVATE_CORPUS_BUNDLE_CONFIGURATION",
    "PrivateCorpusBundleConfiguration",
]
