"""Immutable application resources for the asynchronous series agent."""

import re
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from math import isfinite
from types import MappingProxyType
from typing import Mapping, TypeAlias
from uuid import UUID

from cinegraph.application.models.agent_runtime import ALLOWED_AGENT_JOB_FAILURE_CODES
from cinegraph.application.models.series_agent_result import SeriesAgentResult
from cinegraph.common.error_messages import AgentJobErrorMessages
from cinegraph.domain.enums.enum import SpoilerMode
from cinegraph.domain.models.access import CorpusAccessScope
from cinegraph.domain.models.watch_state import EpisodeRef

JsonScalar: TypeAlias = str | int | float | bool | None
JsonValue: TypeAlias = JsonScalar | tuple["JsonValue", ...] | Mapping[str, "JsonValue"]
JsonPayload: TypeAlias = Mapping[str, JsonValue]
JsonObject: TypeAlias = dict[str, JsonValue]


class AgentJobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    SAFE_REFUSAL = "safe_refusal"
    FAILED = "failed"

    @property
    def terminal(self) -> bool:
        return self in {self.SUCCEEDED, self.SAFE_REFUSAL, self.FAILED}


class AgentJobEventKind(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    SAFE_REFUSAL = "safe_refusal"
    FAILED = "failed"


def _utc(value: datetime, label: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None or value.tzinfo != UTC:
        raise ValueError(AgentJobErrorMessages.UTC_REQUIRED.format(label=label))


@dataclass(frozen=True, slots=True)
class AgentJob:
    job_id: UUID
    owner_profile_id: UUID
    thread_id: UUID
    series_id: UUID
    question: str
    candidate_episodes: tuple[EpisodeRef, ...]
    corpus_access_scope: CorpusAccessScope
    permission_scope_revision: str
    idempotency_key: str
    request_fingerprint: str
    created_at: datetime
    status: AgentJobStatus = AgentJobStatus.QUEUED
    started_at: datetime | None = None
    finished_at: datetime | None = None
    result: SeriesAgentResult | None = None
    error_code: str | None = None
    # Operational correlation only; deliberately excluded from the canonical
    # idempotency fingerprint and all public serializers.
    request_id: str | None = None
    spoiler_mode: SpoilerMode = SpoilerMode.RELAXED
    safe_through_episode_id: UUID | None = None

    @property
    def profile_id(self) -> UUID:
        return self.owner_profile_id

    @property
    def idempotency_digest(self) -> str:
        return self.request_fingerprint

    def __post_init__(self) -> None:
        if any(
            not isinstance(value, UUID)
            for value in (
                self.job_id,
                self.owner_profile_id,
                self.thread_id,
                self.series_id,
            )
        ):
            raise ValueError(AgentJobErrorMessages.JOB_IDS)
        if not isinstance(self.status, AgentJobStatus):
            raise ValueError(AgentJobErrorMessages.JOB_STATUS)
        if not isinstance(self.spoiler_mode, SpoilerMode):
            raise ValueError(AgentJobErrorMessages.JOB_STATUS)
        if self.spoiler_mode is SpoilerMode.RELAXED and self.safe_through_episode_id is not None:
            raise ValueError(AgentJobErrorMessages.SPOILER_BOUNDARY_INVALID)
        if self.safe_through_episode_id is not None and self.safe_through_episode_id not in {
            item.episode_id for item in self.candidate_episodes
        }:
            raise ValueError(AgentJobErrorMessages.SPOILER_BOUNDARY_INVALID)
        if (
            not isinstance(self.question, str)
            or not self.question
            or self.question.strip() != self.question
        ):
            raise ValueError(AgentJobErrorMessages.JOB_QUESTION)
        if not isinstance(self.candidate_episodes, tuple) or not self.candidate_episodes:
            raise ValueError(AgentJobErrorMessages.JOB_CANDIDATES)
        if any(
            not isinstance(item, EpisodeRef) or item.series_id != self.series_id
            for item in self.candidate_episodes
        ):
            raise ValueError(AgentJobErrorMessages.JOB_CANDIDATE_SERIES)
        if len({item.episode_id for item in self.candidate_episodes}) != len(
            self.candidate_episodes
        ):
            raise ValueError(AgentJobErrorMessages.CANDIDATES_UNIQUE)
        if not self.corpus_access_scope.allows_all(self.candidate_episodes):
            raise ValueError(AgentJobErrorMessages.JOB_CANDIDATE_ACCESS)
        if self.permission_scope_revision != self.corpus_access_scope.revision:
            raise ValueError(AgentJobErrorMessages.JOB_SCOPE_REVISION)
        try:
            parsed_key = UUID(self.idempotency_key)
        except (AttributeError, TypeError, ValueError) as error:
            raise ValueError(AgentJobErrorMessages.JOB_IDEMPOTENCY) from error
        if str(parsed_key) != self.idempotency_key:
            raise ValueError(AgentJobErrorMessages.JOB_IDEMPOTENCY)
        if (
            not isinstance(self.request_fingerprint, str)
            or re.fullmatch(r"[0-9a-f]{64}", self.request_fingerprint) is None
        ):
            raise ValueError(AgentJobErrorMessages.JOB_FINGERPRINT)
        if (
            self.request_id is not None
            and re.fullmatch(r"[A-Za-z0-9._-]{1,64}", self.request_id) is None
        ):
            raise ValueError("request_id is not a sanitized request identifier")
        _utc(self.created_at, "created_at")
        if self.started_at is not None:
            _utc(self.started_at, "started_at")
            if self.started_at < self.created_at:
                raise ValueError(AgentJobErrorMessages.JOB_STARTED_ORDER)
        if self.finished_at is not None:
            _utc(self.finished_at, "finished_at")
            if self.started_at is None:
                if self.status is not AgentJobStatus.FAILED:
                    raise ValueError(AgentJobErrorMessages.JOB_FINISHED_START)
                if self.finished_at < self.created_at:
                    raise ValueError(AgentJobErrorMessages.JOB_FINISHED_CREATED_ORDER)
            elif self.finished_at < self.started_at:
                raise ValueError(AgentJobErrorMessages.JOB_FINISHED_STARTED_ORDER)
        if self.status is AgentJobStatus.QUEUED and any(
            value is not None
            for value in (self.started_at, self.finished_at, self.result, self.error_code)
        ):
            raise ValueError(AgentJobErrorMessages.JOB_QUEUED_STATE)
        if self.status is AgentJobStatus.RUNNING and (
            self.started_at is None
            or self.finished_at is not None
            or self.result is not None
            or self.error_code is not None
        ):
            raise ValueError(AgentJobErrorMessages.JOB_RUNNING_STATE)
        if self.status in {AgentJobStatus.SUCCEEDED, AgentJobStatus.SAFE_REFUSAL} and (
            self.finished_at is None or self.error_code is not None or self.result is None
        ):
            raise ValueError(AgentJobErrorMessages.JOB_SUCCESS_STATE)
        if (
            self.result is not None
            and self.status is AgentJobStatus.SUCCEEDED
            and self.result.is_safe_refusal
        ):
            raise ValueError(AgentJobErrorMessages.JOB_SUCCESS_REFUSAL)
        if (
            self.result is not None
            and self.status is AgentJobStatus.SAFE_REFUSAL
            and not self.result.is_safe_refusal
        ):
            raise ValueError(AgentJobErrorMessages.JOB_REFUSAL_STATE)
        if self.status is AgentJobStatus.FAILED and (
            self.finished_at is None or not self.error_code or self.result is not None
        ):
            raise ValueError(AgentJobErrorMessages.JOB_FAILED_STATE)
        if (
            self.status is AgentJobStatus.FAILED
            and self.started_at is None
            and self.error_code != AgentJobErrorMessages.DISPATCH_UNAVAILABLE
        ):
            raise ValueError(AgentJobErrorMessages.JOB_FAILED_STATE)
        if (
            self.status is AgentJobStatus.FAILED
            and self.error_code not in ALLOWED_AGENT_JOB_FAILURE_CODES
        ):
            raise ValueError(AgentJobErrorMessages.JOB_ERROR_CODE)

    def start(self, occurred_at: datetime) -> "AgentJob":
        _utc(occurred_at, "occurred_at")
        if self.status is not AgentJobStatus.QUEUED:
            return self
        return replace(self, status=AgentJobStatus.RUNNING, started_at=occurred_at)

    def complete(self, result: SeriesAgentResult, occurred_at: datetime) -> "AgentJob":
        _utc(occurred_at, "occurred_at")
        if self.status is not AgentJobStatus.RUNNING:
            return self
        status = AgentJobStatus.SAFE_REFUSAL if result.is_safe_refusal else AgentJobStatus.SUCCEEDED
        return replace(self, status=status, finished_at=occurred_at, result=result)

    def fail(self, error_code: str, occurred_at: datetime) -> "AgentJob":
        _utc(occurred_at, "occurred_at")
        if self.status is not AgentJobStatus.RUNNING:
            return self
        if not error_code or error_code.strip() != error_code or len(error_code) > 80:
            raise ValueError(AgentJobErrorMessages.JOB_ERROR_CODE)
        return replace(
            self, status=AgentJobStatus.FAILED, finished_at=occurred_at, error_code=error_code
        )

    def reject(self, error_code: str, occurred_at: datetime) -> "AgentJob":
        _utc(occurred_at, "occurred_at")
        if self.status is not AgentJobStatus.QUEUED:
            raise ValueError(AgentJobErrorMessages.JOB_REJECT_STATE)
        if not error_code or error_code.strip() != error_code or len(error_code) > 80:
            raise ValueError(AgentJobErrorMessages.JOB_ERROR_CODE)
        return replace(
            self, status=AgentJobStatus.FAILED, finished_at=occurred_at, error_code=error_code
        )


@dataclass(frozen=True, slots=True)
class AgentJobEvent:
    event_id: UUID
    sequence: int
    job_id: UUID
    kind: AgentJobEventKind
    occurred_at: datetime
    payload: JsonPayload

    @property
    def event_type(self) -> str:
        return self.kind.value

    def __post_init__(self) -> None:
        if not isinstance(self.event_id, UUID) or not isinstance(self.job_id, UUID):
            raise ValueError(AgentJobErrorMessages.EVENT_IDS)
        if (
            isinstance(self.sequence, bool)
            or not isinstance(self.sequence, int)
            or self.sequence < 1
        ):
            raise ValueError(AgentJobErrorMessages.EVENT_SEQUENCE)
        if not isinstance(self.kind, AgentJobEventKind):
            raise ValueError(AgentJobErrorMessages.EVENT_KIND)
        _utc(self.occurred_at, "occurred_at")
        if not isinstance(self.payload, Mapping):
            raise ValueError(AgentJobErrorMessages.EVENT_PAYLOAD_MAPPING)
        _validate_json_payload(self.payload)
        forbidden = {
            "question",
            "prompt",
            "context",
            "transcript",
            "text",
            "exception",
            "scope",
            "token",
            "api_key",
            "provider",
        }
        if any(_contains_forbidden(key, value, forbidden) for key, value in self.payload.items()):
            raise ValueError(AgentJobErrorMessages.EVENT_PAYLOAD_PRIVATE)
        object.__setattr__(self, "payload", _freeze(self.payload))


def _contains_forbidden(key: object, value: object, forbidden: set[str]) -> bool:
    if str(key).casefold() in forbidden:
        return True
    if isinstance(value, Mapping):
        return any(
            _contains_forbidden(child_key, child_value, forbidden)
            for child_key, child_value in value.items()
        )
    if isinstance(value, (tuple, list, set, frozenset)):
        return any(_contains_forbidden("", child, forbidden) for child in value)
    if isinstance(value, str) and any(
        marker in value.casefold()
        for marker in ("api_key", "bearer ", "prompt injection", "exception:")
    ):
        return True
    return False


def _validate_json_payload(value: object) -> None:
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if not isfinite(value):
            raise ValueError(AgentJobErrorMessages.EVENT_PAYLOAD_FINITE)
        return
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise ValueError(AgentJobErrorMessages.EVENT_PAYLOAD_KEYS)
        for key, child in value.items():
            _validate_json_payload(key)
            _validate_json_payload(child)
        return
    if isinstance(value, (tuple, list)):
        for child in value:
            _validate_json_payload(child)
        return
    raise ValueError(AgentJobErrorMessages.EVENT_PAYLOAD_JSON)


def _freeze(value: JsonValue) -> JsonValue:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(child) for key, child in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(child) for child in value)
    return value
