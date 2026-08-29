from langchain.tools import ToolRuntime, tool
from langchain_core.tools import BaseTool

from cinegraph.application.models.graph_rag import GraphRagQuery
from cinegraph.application.models.series_agent_context import SeriesAgentRuntimeContext
from cinegraph.application.service.graph_rag_service import GraphRagQueryService
from cinegraph.common.error_messages import SeriesAgentErrorMessages
from cinegraph.config.series_agent import (
    DEFAULT_SERIES_AGENT_CONFIGURATION,
    SERIES_GRAPH_TOOL_DESCRIPTION,
    SERIES_GRAPH_TOOL_NAME,
    SeriesAgentConfiguration,
)


def build_series_graph_rag_tool(
    service: GraphRagQueryService,
    configuration: SeriesAgentConfiguration = DEFAULT_SERIES_AGENT_CONFIGURATION,
) -> BaseTool:
    @tool(SERIES_GRAPH_TOOL_NAME, description=SERIES_GRAPH_TOOL_DESCRIPTION)
    def authorized_graph_relationships(
        seed_terms: list[str],
        runtime: ToolRuntime[SeriesAgentRuntimeContext, dict[str, object]],
        predicates: list[str] | None = None,
    ) -> dict[str, object]:
        cfg = configuration
        if (
            not isinstance(seed_terms, list)
            or not seed_terms
            or len(seed_terms) > cfg.graph_seed_limit
            or any(
                not isinstance(item, str)
                or not item.strip()
                or item.strip() != item
                or len(item) > cfg.graph_seed_max_length
                for item in seed_terms
            )
        ):
            raise ValueError(SeriesAgentErrorMessages.GRAPH_SEEDS_INVALID)
        predicate_values = [] if predicates is None else predicates
        if (
            not isinstance(predicate_values, list)
            or len(predicate_values) > cfg.graph_predicate_limit
            or any(
                not isinstance(item, str)
                or not item.strip()
                or item.strip() != item
                or len(item) > cfg.graph_predicate_max_length
                for item in predicate_values
            )
        ):
            raise ValueError(SeriesAgentErrorMessages.GRAPH_PREDICATES_INVALID)
        if runtime is None:
            raise ValueError(SeriesAgentErrorMessages.GRAPH_CONTEXT_REQUIRED)
        context = runtime.context
        result = service.execute(
            GraphRagQuery(
                series_id=context.series_id,
                seed_terms=tuple(seed_terms),
                predicates=tuple(predicate_values),
                candidate_episodes=context.candidate_episodes,
                profile_watch_state=context.profile_watch_state,
                corpus_access_scope=context.corpus_access_scope,
                hops=cfg.graph_hops,
                claim_limit=cfg.graph_claim_limit,
                evidence_per_claim=cfg.graph_evidence_per_claim,
            )
        )
        claims = []
        for claim in result.claims:
            row = {
                "claim_id": str(claim.claim_id),
                "series_id": str(claim.series_id),
                "subject": claim.subject.display_name,
                "predicate": claim.predicate,
                "object": claim.object.display_name,
                "polarity": getattr(claim.polarity, "value", claim.polarity),
                "score": getattr(claim, "score", 0.0),
                "evidence": [
                    _evidence_payload(evidence)
                    for evidence in claim.evidence
                ],
            }
            subject_entity_id = getattr(claim.subject, "entity_id", None)
            subject_kind = getattr(claim.subject, "kind", None)
            object_entity_id = getattr(claim.object, "entity_id", None)
            object_kind = getattr(claim.object, "kind", None)
            if all(
                value is not None
                for value in (subject_entity_id, subject_kind, object_entity_id, object_kind)
            ):
                row.update(
                    subject_entity_id=str(subject_entity_id),
                    subject_kind=subject_kind.value,
                    object_entity_id=str(object_entity_id),
                    object_kind=object_kind.value,
                    hop_distance=getattr(claim, "hop_distance", 1),
                )
            claims.append(row)
        return {"claims": claims}

    return authorized_graph_relationships


def _evidence_payload(evidence: object) -> dict[str, object]:
    episode = getattr(evidence, "episode")
    payload: dict[str, object] = {
        "evidence_id": str(getattr(evidence, "evidence_id")),
        "episode_id": str(episode.episode_id),
        "season_number": episode.position.season_number,
        "episode_number": episode.position.episode_number,
        "start_ms": getattr(evidence, "start_ms"),
        "end_ms": getattr(evidence, "end_ms"),
    }
    source_version_id = getattr(evidence, "source_version_id", None)
    transcript_chunk_id = getattr(evidence, "transcript_chunk_id", None)
    if source_version_id is not None and transcript_chunk_id is not None:
        payload.update(
            source_version_id=str(source_version_id),
            transcript_chunk_id=str(transcript_chunk_id),
        )
    return payload


build_authorized_graph_rag_tool = build_series_graph_rag_tool
