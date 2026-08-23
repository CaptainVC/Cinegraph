from uuid import UUID

from tests.factories import make_episode_ref

from cinegraph.adapters.qdrant.retrieval_scope_filter import (
    compile_retrieval_scope_filter,
)
from cinegraph.config.transcript_chunking import TRANSCRIPT_INDEX_REVISION
from cinegraph.domain.enums.enum import (
    RightsStatus,
    SourceReviewStatus,
    SourceVersionStatus,
)
from cinegraph.domain.retrieval.retrieval_scope import (
    EpisodeVisibilityScope,
    RetrievalScope,
)

SERIES_ID = UUID("00000000-0000-0000-0000-000000000011")


def serialized_filter(scope: RetrievalScope) -> dict:
    compiled_filter = compile_retrieval_scope_filter(scope)
    assert compiled_filter is not None
    return compiled_filter.model_dump(exclude_none=True)


def test_empty_scope_is_no_query_signal() -> None:
    scope = RetrievalScope(series_id=SERIES_ID, episode_scopes=())

    assert compile_retrieval_scope_filter(scope) is None


def test_full_scope_has_episode_match_without_end_range() -> None:
    episode = make_episode_ref(series_id=SERIES_ID, episode_id=UUID(int=1))
    result = serialized_filter(
        RetrievalScope(
            series_id=SERIES_ID,
            episode_scopes=(EpisodeVisibilityScope(episode, None),),
        )
    )

    visibility = next(item for item in result["must"] if "should" in item)["should"][0]
    assert visibility == {
        "must": [
            {
                "key": "episode_id",
                "match": {"value": str(episode.episode_id)},
            }
        ]
    }


def test_partial_scope_pairs_episode_id_with_inclusive_cutoff() -> None:
    episode = make_episode_ref(series_id=SERIES_ID, episode_id=UUID(int=2))
    result = serialized_filter(
        RetrievalScope(
            series_id=SERIES_ID,
            episode_scopes=(EpisodeVisibilityScope(episode, 32_000),),
        )
    )

    visibility = next(item for item in result["must"] if "should" in item)["should"][0]
    assert visibility == {
        "must": [
            {
                "key": "episode_id",
                "match": {"value": str(episode.episode_id)},
            },
            {"key": "end_ms", "range": {"lte": 32_000}},
        ]
    }


def test_mixed_scopes_preserve_input_order_in_visibility_should() -> None:
    full_episode = make_episode_ref(series_id=SERIES_ID, episode_id=UUID(int=3))
    partial_episode = make_episode_ref(series_id=SERIES_ID, episode_id=UUID(int=4))
    result = serialized_filter(
        RetrievalScope(
            series_id=SERIES_ID,
            episode_scopes=(
                EpisodeVisibilityScope(full_episode, None),
                EpisodeVisibilityScope(partial_episode, 64_000),
            ),
        )
    )

    visibility = next(item for item in result["must"] if "should" in item)["should"]
    assert [item["must"][0]["match"]["value"] for item in visibility] == [
        str(full_episode.episode_id),
        str(partial_episode.episode_id),
    ]
    assert "range" not in visibility[0]["must"][0]
    assert visibility[1]["must"][1] == {
        "key": "end_ms",
        "range": {"lte": 64_000},
    }


def test_outer_conditions_require_series_active_and_reviewed_values() -> None:
    episode = make_episode_ref(series_id=SERIES_ID, episode_id=UUID(int=5))

    result = serialized_filter(
        RetrievalScope(
            series_id=SERIES_ID,
            episode_scopes=(EpisodeVisibilityScope(episode, None),),
        )
    )

    assert result["must"][:3] == [
        {"key": "series_id", "match": {"value": str(SERIES_ID)}},
        {
            "key": "source_status",
            "match": {"value": SourceVersionStatus.ACTIVE.value},
        },
        {
            "key": "review_status",
            "match": {
                "any": [
                    SourceReviewStatus.AUTOMATED_REVIEWED.value,
                    SourceReviewStatus.HYBRID_REVIEWED.value,
                    SourceReviewStatus.REVIEWED.value,
                ]
            },
        },
    ]
    assert result["must"][3:5] == [
        {
            "key": "rights_status",
            "match": {"value": RightsStatus.ALLOWED.value},
        },
        {
            "key": "index_revision",
            "match": {"value": TRANSCRIPT_INDEX_REVISION},
        },
    ]


def test_uuid_match_values_are_serialized_as_strings() -> None:
    episode = make_episode_ref(series_id=SERIES_ID, episode_id=UUID(int=6))

    result = serialized_filter(
        RetrievalScope(
            series_id=SERIES_ID,
            episode_scopes=(EpisodeVisibilityScope(episode, None),),
        )
    )

    assert result["must"][0]["match"]["value"] == str(SERIES_ID)
    visibility = next(item for item in result["must"] if "should" in item)
    assert visibility["should"][0]["must"][0]["match"]["value"] == str(episode.episode_id)
