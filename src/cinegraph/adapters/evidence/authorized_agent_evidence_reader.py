from typing import cast
from uuid import UUID

from cinegraph.application.models.agent_job import AgentJob
from cinegraph.application.models.graph_rag import GraphRagQuery
from cinegraph.application.service.graph_rag_service import GraphRagQueryService
from cinegraph.common.error_messages import AgentJobErrorMessages
from cinegraph.config import DEFAULT_AGENT_JOB_CONFIGURATION, AgentJobConfiguration
from cinegraph.config.graph_rag import DEFAULT_GRAPH_RAG_CONFIGURATION
from cinegraph.domain.models.access import CorpusAccessScope
from cinegraph.domain.models.watch_state import ProfileWatchState
from cinegraph.domain.policy.spoiler_policy import SpoilerPolicy
from cinegraph.domain.policy.watch_state_builder import build_bounded_watch_state
from cinegraph.domain.retrieval import RetrievalScope, RetrievalScopeCompiler
from cinegraph.ports.agent_jobs.agent_evidence_reader import (
    AgentEvidenceCitation,
    AgentEvidenceExcerpt,
    AgentEvidenceNotFoundError,
    AgentEvidenceRequest,
    AgentEvidenceResult,
)
from cinegraph.ports.retrieval import VectorIndex
from cinegraph.ports.retrieval.vector_index import RetrievedSegment


def build_agent_evidence_request(
    job: AgentJob, citation_ids: tuple[UUID, ...]
) -> AgentEvidenceRequest:
    """Project an application job into the dependency-safe evidence port contract."""

    if job.result is None:
        raise AgentEvidenceNotFoundError(AgentJobErrorMessages.EVIDENCE_NOT_FOUND)
    selected = {item.citation_id: item for item in job.result.citations}
    citations = tuple(selected.get(citation_id) for citation_id in citation_ids)
    if any(item is None for item in citations):
        raise AgentEvidenceNotFoundError(AgentJobErrorMessages.EVIDENCE_NOT_FOUND)
    return AgentEvidenceRequest(
        owner_profile_id=job.owner_profile_id,
        series_id=job.series_id,
        candidate_episodes=job.candidate_episodes,
        permission_scope_revision=job.permission_scope_revision,
        spoiler_mode=job.spoiler_mode,
        safe_through_episode_id=job.safe_through_episode_id,
        citations=tuple(
            AgentEvidenceCitation(
                citation_id=item.citation_id,
                kind=item.kind,
                episode=item.episode,
                start_ms=item.start_ms,
                end_ms=item.end_ms,
                segment_id=item.segment_id,
                claim_id=item.claim_id,
                evidence_id=item.evidence_id,
                source_version_id=item.source_version_id,
                transcript_chunk_id=item.transcript_chunk_id,
                subject_display_name=item.subject_display_name,
                object_display_name=item.object_display_name,
                predicate=item.predicate,
            )
            for item in citations
            if item is not None
        ),
    )


class AuthorizedAgentEvidenceReader:
    """Hydrate only citations already selected by a completed agent job."""

    def __init__(
        self,
        vector_index: VectorIndex,
        graph_service: GraphRagQueryService,
        configuration: AgentJobConfiguration = DEFAULT_AGENT_JOB_CONFIGURATION,
    ) -> None:
        self._vector_index = vector_index
        self._graph_service = graph_service
        self._scope_compiler = RetrievalScopeCompiler(SpoilerPolicy())
        self._configuration = configuration

    def read(
        self,
        evidence_request: AgentEvidenceRequest,
        current_scope: CorpusAccessScope,
    ) -> AgentEvidenceResult:
        citation_ids = tuple(item.citation_id for item in evidence_request.citations)
        if (
            not citation_ids
            or len(citation_ids) != len(set(citation_ids))
            or len(citation_ids) > self._configuration.evidence_citation_limit
            or evidence_request.permission_scope_revision != current_scope.revision
            or not current_scope.allows_all(evidence_request.candidate_episodes)
        ):
            raise AgentEvidenceNotFoundError(AgentJobErrorMessages.EVIDENCE_NOT_FOUND)
        citations = evidence_request.citations
        scope = self._scope_compiler.compile(
            evidence_request.series_id,
            evidence_request.candidate_episodes,
            _watch_state(evidence_request),
            current_scope,
        )
        if not scope.episode_scopes:
            raise AgentEvidenceNotFoundError(AgentJobErrorMessages.EVIDENCE_NOT_FOUND)
        transcript_ids = tuple(
            item.segment_id
            for item in citations
            if item.kind == "transcript" and item.segment_id is not None
        )
        graph_citations = tuple(item for item in citations if item.kind == "graph")
        segments = self._vector_index.retrieve_by_ids(transcript_ids, scope) if transcript_ids else ()
        segments_by_id = {item.segment_id: item for item in segments}
        graph_segments = self._graph_segments(
            graph_citations, evidence_request, current_scope, scope
        )
        excerpts: list[AgentEvidenceExcerpt] = []
        for citation in citations:
            segment = (
                segments_by_id.get(citation.segment_id)
                if citation.kind == "transcript" and citation.segment_id is not None
                else graph_segments.get(citation.citation_id)
            )
            if (
                segment is None
                or segment.episode != citation.episode
                or not segment.text
                or len(segment.text) > self._configuration.evidence_text_max_chars
            ):
                raise AgentEvidenceNotFoundError(AgentJobErrorMessages.EVIDENCE_NOT_FOUND)
            if segment.start_ms != citation.start_ms or segment.end_ms != citation.end_ms:
                raise AgentEvidenceNotFoundError(AgentJobErrorMessages.EVIDENCE_NOT_FOUND)
            if (
                citation.kind == "graph"
                and segment.source_version_id != citation.source_version_id
            ):
                raise AgentEvidenceNotFoundError(AgentJobErrorMessages.EVIDENCE_NOT_FOUND)
            excerpts.append(
                AgentEvidenceExcerpt(
                    citation_id=citation.citation_id,
                    kind=citation.kind,
                    episode=segment.episode,
                    source_version_id=segment.source_version_id,
                    start_ms=segment.start_ms,
                    end_ms=segment.end_ms,
                    text=segment.text,
                    score=segment.score,
                )
            )
        return AgentEvidenceResult(excerpts=tuple(excerpts))

    def _graph_segments(
        self,
        citations: tuple[AgentEvidenceCitation, ...],
        evidence_request: AgentEvidenceRequest,
        current_scope: CorpusAccessScope,
        scope: RetrievalScope,
    ) -> dict[UUID, RetrievedSegment | None]:
        if not citations:
            return {}
        if any(
            item.source_version_id is None
            or item.transcript_chunk_id is None
            or item.subject_display_name is None
            or item.object_display_name is None
            or item.predicate is None
            for item in citations
        ):
            raise AgentEvidenceNotFoundError(AgentJobErrorMessages.EVIDENCE_NOT_FOUND)
        # Each citation can contribute two unique entity seeds. Batch the
        # authorization re-query within GraphRAG's central input limits so a
        # legitimate bounded evidence trail cannot fail merely because it
        # contains several independent relationships.
        batch_size = min(
            DEFAULT_GRAPH_RAG_CONFIGURATION.max_seeds // 2,
            DEFAULT_GRAPH_RAG_CONFIGURATION.max_predicates,
        )
        claims = tuple(
            claim
            for offset in range(0, len(citations), batch_size)
            for claim in self._graph_service.execute(
                GraphRagQuery(
                    series_id=evidence_request.series_id,
                    seed_terms=tuple(
                        dict.fromkeys(
                            value
                            for item in citations[offset : offset + batch_size]
                            for value in (
                                cast(str, item.subject_display_name),
                                cast(str, item.object_display_name),
                            )
                        )
                    ),
                    predicates=tuple(
                        dict.fromkeys(
                            cast(str, item.predicate)
                            for item in citations[offset : offset + batch_size]
                        )
                    ),
                    candidate_episodes=evidence_request.candidate_episodes,
                    profile_watch_state=_watch_state(evidence_request),
                    corpus_access_scope=current_scope,
                )
            ).claims
        )
        evidence_by_id = {
            evidence.evidence_id: (claim.claim_id, evidence)
            for claim in claims
            for evidence in claim.evidence
        }
        if any(
            item.evidence_id not in evidence_by_id
            or evidence_by_id[item.evidence_id][0] != item.claim_id
            or evidence_by_id[item.evidence_id][1].source_version_id != item.source_version_id
            or evidence_by_id[item.evidence_id][1].transcript_chunk_id != item.transcript_chunk_id
            for item in citations
        ):
            raise AgentEvidenceNotFoundError(AgentJobErrorMessages.EVIDENCE_NOT_FOUND)
        chunk_ids = tuple(item.transcript_chunk_id for item in citations if item.transcript_chunk_id)
        segments = self._vector_index.retrieve_by_ids(chunk_ids, scope)
        by_chunk = {item.segment_id: item for item in segments}
        return {
            item.citation_id: by_chunk.get(item.transcript_chunk_id)
            for item in citations
            if item.transcript_chunk_id is not None
        }


def _watch_state(evidence_request: AgentEvidenceRequest) -> ProfileWatchState:
    try:
        return build_bounded_watch_state(
            evidence_request.owner_profile_id,
            "API session",
            evidence_request.series_id,
            evidence_request.candidate_episodes,
            evidence_request.spoiler_mode,
            evidence_request.safe_through_episode_id,
        )
    except ValueError as error:
        raise AgentEvidenceNotFoundError(AgentJobErrorMessages.EVIDENCE_NOT_FOUND) from error
