import pytest

from cinegraph.common.error_messages import GraphErrorMessages
from cinegraph.common.graph_normalization import (
    normalize_graph_display,
    normalize_graph_identity,
    normalize_graph_predicate,
)


def test_display_normalization_applies_nfkc_and_collapses_whitespace() -> None:
    assert normalize_graph_display("  Ａlex\t  Smith  ") == "Alex Smith"


def test_identity_normalization_casefolds_display_normalization() -> None:
    assert normalize_graph_identity("  Straße  ") == "strasse"


def test_predicate_normalization_converts_spaces_to_snake_case() -> None:
    assert normalize_graph_predicate("  Works With ") == "works_with"


@pytest.mark.parametrize("value", ["", "   ", None, 42])
def test_display_and_identity_reject_empty_or_non_string_values(value: object) -> None:
    with pytest.raises(ValueError, match=GraphErrorMessages.NORMALIZATION_INVALID):
        normalize_graph_display(value)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match=GraphErrorMessages.NORMALIZATION_INVALID):
        normalize_graph_identity(value)  # type: ignore[arg-type]


@pytest.mark.parametrize("value", ["has/slash", "has-hyphen", "1starts", "has__gap"])
def test_predicate_rejects_non_lowercase_snake_case(value: str) -> None:
    with pytest.raises(ValueError, match=GraphErrorMessages.CLAIM_PREDICATE_INVALID):
        normalize_graph_predicate(value)
