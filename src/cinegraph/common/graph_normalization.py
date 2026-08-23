import re
import unicodedata

from cinegraph.common.error_messages import GraphErrorMessages


def normalize_graph_display(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError(GraphErrorMessages.NORMALIZATION_INVALID)
    normalized = " ".join(unicodedata.normalize("NFKC", value).split())
    if not normalized:
        raise ValueError(GraphErrorMessages.NORMALIZATION_INVALID)
    return normalized


def normalize_graph_identity(value: str) -> str:
    return normalize_graph_display(value).casefold()


def normalize_graph_predicate(value: str) -> str:
    normalized = normalize_graph_identity(value).replace(" ", "_")
    if re.fullmatch(r"[a-z][a-z0-9]*(?:_[a-z0-9]+)*", normalized) is None:
        raise ValueError(GraphErrorMessages.CLAIM_PREDICATE_INVALID)
    return normalized
