"""Canonical identity inputs for agent jobs."""

import hashlib
import json
from uuid import UUID, uuid5

from cinegraph.common.identifiers.templates import IdentifierTemplates
from cinegraph.domain.models.access import CorpusAccessScope
from cinegraph.domain.models.watch_state import EpisodeRef

AGENT_JOB_NAMESPACE = uuid5(IdentifierTemplates.NAMESPACE, "cinegraph.agent-job.v1")


def canonical_request_fingerprint(
    owner_profile_id: UUID,
    thread_id: UUID,
    series_id: UUID,
    question: str,
    permission_scope_revision: str,
    corpus_access_scope: CorpusAccessScope,
    candidate_episodes: tuple[EpisodeRef, ...],
) -> str:
    if not all(isinstance(value, UUID) for value in (owner_profile_id, thread_id, series_id)):
        raise ValueError("Agent job identity fields must be UUIDs.")
    if not question or question.strip() != question:
        raise ValueError("Agent job question must be trimmed.")
    if len({item.episode_id for item in candidate_episodes}) != len(candidate_episodes):
        raise ValueError("Agent job candidates must be unique.")
    payload = {
        "profile": str(owner_profile_id),
        "thread": str(thread_id),
        "series": str(series_id),
        "question": question,
        "revision": permission_scope_revision,
        "scope": {
            "mode": corpus_access_scope.mode.value,
            "unrestricted": corpus_access_scope.unrestricted,
            "seasons": sorted(
                (str(item.series_id), item.season_number)
                for item in corpus_access_scope.allowed_seasons
            ),
        },
        "candidates": sorted(str(item.episode_id) for item in candidate_episodes),
    }
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def stable_agent_job_id(
    owner_profile_id: UUID, idempotency_key: str, request_fingerprint: str
) -> UUID:
    return uuid5(AGENT_JOB_NAMESPACE, f"{owner_profile_id}:{idempotency_key}:{request_fingerprint}")
