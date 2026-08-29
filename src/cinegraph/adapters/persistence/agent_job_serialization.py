"""Deterministic, non-pickle serialization for durable agent job state."""

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from enum import Enum
from typing import Literal, NoReturn, TypeVar, cast
from uuid import UUID

from cinegraph.application.models.agent_job import (
    AgentJob,
    AgentJobEvent,
    AgentJobEventKind,
    AgentJobStatus,
)
from cinegraph.application.models.series_agent_result import SeriesAgentCitation, SeriesAgentResult
from cinegraph.domain.enums.enum import (
    CorpusAccessMode,
    GraphClaimPolarity,
    GraphEntityKind,
    SpoilerMode,
)
from cinegraph.domain.models.access import CorpusAccessScope, CorpusSeasonAccess
from cinegraph.domain.models.watch_state import EpisodePosition, EpisodeRef

EnumT = TypeVar("EnumT", bound=Enum)


def episode_to_json(value: EpisodeRef) -> dict[str, object]:
    return {
        "series_id": str(value.series_id),
        "season_id": str(value.season_id),
        "episode_id": str(value.episode_id),
        "season_number": value.position.season_number,
        "episode_number": value.position.episode_number,
    }


def episode_from_json(value: object) -> EpisodeRef:
    if not isinstance(value, Mapping):
        raise ValueError("malformed episode state")
    if set(value) != {"series_id", "season_id", "episode_id", "season_number", "episode_number"}:
        raise ValueError("malformed episode state")
    try:
        return EpisodeRef(
            series_id=_uuid(value, "series_id"),
            season_id=_uuid(value, "season_id"),
            episode_id=_uuid(value, "episode_id"),
            position=EpisodePosition(
                _integer(value, "season_number"),
                _integer(value, "episode_number"),
            ),
        )
    except (TypeError, ValueError, KeyError) as error:
        raise ValueError("malformed episode state") from error


def scope_to_json(value: CorpusAccessScope) -> dict[str, object]:
    return {
        "mode": value.mode.value,
        "revision": value.revision,
        "unrestricted": value.unrestricted,
        "allowed_seasons": [
            {"series_id": str(item.series_id), "season_number": item.season_number}
            for item in sorted(
                value.allowed_seasons, key=lambda item: (str(item.series_id), item.season_number)
            )
        ],
    }


def scope_from_json(value: object) -> CorpusAccessScope:
    if not isinstance(value, Mapping):
        raise ValueError("malformed access scope")
    if set(value) != {"mode", "revision", "unrestricted", "allowed_seasons"}:
        raise ValueError("malformed access scope")
    try:
        seasons = value["allowed_seasons"]
        if not isinstance(seasons, list):
            raise ValueError("malformed access scope seasons")
        if any(
            not isinstance(item, Mapping) or set(item) != {"series_id", "season_number"}
            for item in seasons
        ):
            raise ValueError("malformed access scope season")
        parsed = [
            CorpusSeasonAccess(_uuid(item, "series_id"), _integer(item, "season_number"))
            for item in seasons
        ]
        if len(set(parsed)) != len(parsed):
            raise ValueError("duplicate access scope season")
        return CorpusAccessScope(
            mode=CorpusAccessMode(_string(value, "mode")),
            revision=_string(value, "revision"),
            unrestricted=value["unrestricted"]
            if isinstance(value["unrestricted"], bool)
            else (_raise_malformed()),
            allowed_seasons=frozenset(parsed),
        )
    except (TypeError, ValueError, KeyError) as error:
        raise ValueError("malformed access scope") from error


def _string(value: Mapping[str, object], key: str) -> str:
    item = value[key]
    if not isinstance(item, str) or not item:
        raise ValueError("expected nonempty string")
    return item


def result_to_json(value: SeriesAgentResult) -> dict[str, object]:
    return {
        "answer": value.answer,
        "is_safe_refusal": value.is_safe_refusal,
        "used_tools": list(value.used_tools),
        "citations": [
            {
                "kind": item.kind,
                "episode": episode_to_json(item.episode),
                "start_ms": item.start_ms,
                "end_ms": item.end_ms,
                "segment_id": str(item.segment_id) if item.segment_id else None,
                "claim_id": str(item.claim_id) if item.claim_id else None,
                "evidence_id": str(item.evidence_id) if item.evidence_id else None,
                "source_version_id": str(item.source_version_id) if item.source_version_id else None,
                "transcript_chunk_id": str(item.transcript_chunk_id) if item.transcript_chunk_id else None,
                "subject_entity_id": str(item.subject_entity_id) if item.subject_entity_id else None,
                "subject_kind": item.subject_kind.value if item.subject_kind else None,
                "subject_display_name": item.subject_display_name,
                "predicate": item.predicate,
                "object_entity_id": str(item.object_entity_id) if item.object_entity_id else None,
                "object_kind": item.object_kind.value if item.object_kind else None,
                "object_display_name": item.object_display_name,
                "polarity": item.polarity.value if item.polarity else None,
                "hop_distance": item.hop_distance,
                "score": item.score,
            }
            for item in value.citations
        ],
    }


def result_from_json(value: object) -> SeriesAgentResult:
    if not isinstance(value, Mapping) or set(value) != {
        "answer",
        "is_safe_refusal",
        "used_tools",
        "citations",
    }:
        raise ValueError("malformed agent result")
    refusal = value["is_safe_refusal"]
    tools = value["used_tools"]
    citations = value["citations"]
    if (
        not isinstance(refusal, bool)
        or not isinstance(tools, list)
        or not isinstance(citations, list)
    ):
        raise ValueError("malformed agent result")
    if any(not isinstance(tool, str) for tool in tools):
        raise ValueError("malformed agent result tools")
    parsed: list[SeriesAgentCitation] = []
    for item in citations:
        legacy_keys = {
            "kind",
            "episode",
            "start_ms",
            "end_ms",
            "segment_id",
            "claim_id",
            "evidence_id",
        }
        modern_keys = legacy_keys | {
            "source_version_id", "transcript_chunk_id", "subject_entity_id", "subject_kind",
            "subject_display_name", "predicate", "object_entity_id", "object_kind",
            "object_display_name", "polarity", "hop_distance", "score",
        }
        if not isinstance(item, Mapping) or (
            set(item) != legacy_keys and set(item) != modern_keys
        ):
            raise ValueError("malformed citation")
        try:
            parsed.append(
                SeriesAgentCitation(
                    kind=cast("Literal['transcript', 'graph']", item["kind"]),
                    episode=episode_from_json(item["episode"]),
                    start_ms=_integer(item, "start_ms"),
                    end_ms=_integer(item, "end_ms"),
                    segment_id=_nullable_uuid(item, "segment_id"),
                    claim_id=_nullable_uuid(item, "claim_id"),
                    evidence_id=_nullable_uuid(item, "evidence_id"),
                    source_version_id=_nullable_uuid_optional(item, "source_version_id"),
                    transcript_chunk_id=_nullable_uuid_optional(item, "transcript_chunk_id"),
                    subject_entity_id=_nullable_uuid_optional(item, "subject_entity_id"),
                    subject_kind=_nullable_enum(item, "subject_kind", GraphEntityKind),
                    subject_display_name=_nullable_string(item, "subject_display_name"),
                    predicate=_nullable_string(item, "predicate"),
                    object_entity_id=_nullable_uuid_optional(item, "object_entity_id"),
                    object_kind=_nullable_enum(item, "object_kind", GraphEntityKind),
                    object_display_name=_nullable_string(item, "object_display_name"),
                    polarity=_nullable_enum(item, "polarity", GraphClaimPolarity),
                    hop_distance=_nullable_integer(item, "hop_distance"),
                    score=_nullable_float(item, "score"),
                )
            )
        except (TypeError, ValueError, KeyError) as error:
            raise ValueError("malformed citation") from error
    answer = value["answer"]
    if answer is not None and not isinstance(answer, str):
        raise ValueError("malformed agent result answer")
    if len({(c.kind, c.segment_id, c.claim_id, c.evidence_id) for c in parsed}) != len(parsed):
        raise ValueError("duplicate citations")
    return SeriesAgentResult(
        answer=answer, is_safe_refusal=refusal, citations=tuple(parsed), used_tools=tuple(tools)
    )


def event_to_json(value: AgentJobEvent) -> dict[str, object]:
    return {
        "event_id": str(value.event_id),
        "job_id": str(value.job_id),
        "sequence": value.sequence,
        "kind": value.kind.value,
        "occurred_at": value.occurred_at.isoformat(),
        "payload": _json_thaw(value.payload),
    }


def event_from_json(value: object) -> AgentJobEvent:
    if not isinstance(value, Mapping) or set(value) != {
        "event_id",
        "job_id",
        "sequence",
        "kind",
        "occurred_at",
        "payload",
    }:
        raise ValueError("malformed agent event")
    try:
        occurred = _datetime(value, "occurred_at")
        return AgentJobEvent(
            event_id=_uuid(value, "event_id"),
            job_id=_uuid(value, "job_id"),
            sequence=_integer(value, "sequence"),
            kind=AgentJobEventKind(_string(value, "kind")),
            occurred_at=occurred,
            payload=value["payload"],
        )
    except (TypeError, ValueError, KeyError) as error:
        raise ValueError("malformed agent event") from error


def _raise_malformed() -> NoReturn:
    raise ValueError("malformed access scope")


def _integer(value: Mapping[str, object], key: str) -> int:
    item = value[key]
    if isinstance(item, bool) or not isinstance(item, int):
        raise ValueError("expected integer")
    return item


def _uuid(value: Mapping[str, object], key: str) -> UUID:
    item = value[key]
    if not isinstance(item, str):
        raise ValueError("expected canonical UUID")
    parsed = UUID(item)
    if str(parsed) != item:
        raise ValueError("expected canonical UUID")
    return parsed


def _datetime(value: Mapping[str, object], key: str) -> datetime:
    item = _string(value, key)
    parsed = datetime.fromisoformat(item)
    if parsed.tzinfo is None or parsed.utcoffset() is None or parsed.tzinfo is not UTC:
        raise ValueError("expected UTC datetime")
    return parsed


def _nullable_uuid(value: Mapping[str, object], key: str) -> UUID | None:
    item = value[key]
    if item is None:
        return None
    return _uuid(value, key)


def _nullable_uuid_optional(value: Mapping[str, object], key: str) -> UUID | None:
    return None if key not in value else _nullable_uuid(value, key)


def _nullable_string(value: Mapping[str, object], key: str) -> str | None:
    item = value.get(key)
    if item is None:
        return None
    if not isinstance(item, str):
        raise ValueError("expected nullable string")
    return item


def _nullable_integer(value: Mapping[str, object], key: str) -> int | None:
    item = value.get(key)
    if item is None:
        return None
    return _integer(value, key)


def _nullable_float(value: Mapping[str, object], key: str) -> float | None:
    item = value.get(key)
    if item is None:
        return None
    if isinstance(item, bool) or not isinstance(item, (int, float)):
        raise ValueError("expected nullable float")
    return float(item)


def _nullable_enum(
    value: Mapping[str, object], key: str, enum_type: type[EnumT]
) -> EnumT | None:
    item = value.get(key)
    if item is None:
        return None
    return enum_type(item)


def job_to_json(value: AgentJob) -> dict[str, object]:
    return {
        "job_id": str(value.job_id),
        "owner_profile_id": str(value.owner_profile_id),
        "thread_id": str(value.thread_id),
        "series_id": str(value.series_id),
        "question": value.question,
        "candidate_episodes": [episode_to_json(e) for e in value.candidate_episodes],
        "corpus_access_scope": scope_to_json(value.corpus_access_scope),
        "permission_scope_revision": value.permission_scope_revision,
        "idempotency_key": value.idempotency_key,
        "request_fingerprint": value.request_fingerprint,
        "request_id": value.request_id,
        "spoiler_mode": value.spoiler_mode.value,
        "safe_through_episode_id": (
            str(value.safe_through_episode_id) if value.safe_through_episode_id else None
        ),
        "created_at": value.created_at.isoformat(),
        "status": value.status.value,
        "started_at": value.started_at.isoformat() if value.started_at else None,
        "finished_at": value.finished_at.isoformat() if value.finished_at else None,
        "result": result_to_json(value.result) if value.result else None,
        "error_code": value.error_code,
    }


def job_from_json(value: object) -> AgentJob:
    keys = {
        "job_id",
        "owner_profile_id",
        "thread_id",
        "series_id",
        "question",
        "candidate_episodes",
        "corpus_access_scope",
        "permission_scope_revision",
        "idempotency_key",
        "request_fingerprint",
        "request_id",
        "created_at",
        "status",
        "started_at",
        "finished_at",
        "result",
        "error_code",
    }
    legacy_keys = keys
    keys = keys | {"spoiler_mode", "safe_through_episode_id"}
    if (
        not isinstance(value, Mapping)
        or (set(value) != legacy_keys and set(value) != keys)
        or not isinstance(value["candidate_episodes"], list)
    ):
        raise ValueError("malformed agent job")
    candidates = tuple(episode_from_json(item) for item in value["candidate_episodes"])
    if len({e.episode_id for e in candidates}) != len(candidates):
        raise ValueError("duplicate candidates")

    def dt(key: str) -> datetime | None:
        item = value[key]
        return None if item is None else _datetime(value, key)

    try:
        scope = scope_from_json(value["corpus_access_scope"])
        result = None if value["result"] is None else result_from_json(value["result"])
        if result is not None and any(
            citation.episode not in candidates for citation in result.citations
        ):
            raise ValueError("result citation references an unknown candidate")
        return AgentJob(
            job_id=_uuid(value, "job_id"),
            owner_profile_id=_uuid(value, "owner_profile_id"),
            thread_id=_uuid(value, "thread_id"),
            series_id=_uuid(value, "series_id"),
            question=_string(value, "question"),
            candidate_episodes=candidates,
            corpus_access_scope=scope,
            permission_scope_revision=_string(value, "permission_scope_revision"),
            idempotency_key=_string(value, "idempotency_key"),
            request_fingerprint=_string(value, "request_fingerprint"),
            request_id=None if value["request_id"] is None else _string(value, "request_id"),
            spoiler_mode=SpoilerMode(value.get("spoiler_mode", SpoilerMode.RELAXED.value)),
            safe_through_episode_id=(
                _uuid(value, "safe_through_episode_id")
                if value.get("safe_through_episode_id") is not None
                else None
            ),
            created_at=_datetime(value, "created_at"),
            status=AgentJobStatus(_string(value, "status")),
            started_at=dt("started_at"),
            finished_at=dt("finished_at"),
            result=result,
            error_code=None if value["error_code"] is None else _string(value, "error_code"),
        )
    except (TypeError, ValueError, KeyError) as error:
        raise ValueError("malformed agent job") from error


def _json_thaw(value: object) -> object:
    """Project frozen event payloads into JSON-native containers."""

    if isinstance(value, Mapping):
        return {str(key): _json_thaw(child) for key, child in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_json_thaw(child) for child in value]
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    raise ValueError("event payload is not JSON serializable")
