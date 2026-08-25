"""Privacy-safe SSE/event payloads shared by all agent-job repositories."""

from cinegraph.application.models.series_agent_result import SeriesAgentResult


def result_event_payload(result: SeriesAgentResult) -> dict[str, object]:
    if result.is_safe_refusal:
        return {"status": "safe_refusal", "safe_refusal": True}
    citations = tuple(
        {
            "kind": item.kind,
            "episode_id": str(item.episode.episode_id),
            "season_number": item.episode.position.season_number,
            "episode_number": item.episode.position.episode_number,
            "start_ms": item.start_ms,
            "end_ms": item.end_ms,
            **(
                {"segment_id": str(item.segment_id)}
                if item.kind == "transcript"
                else {"claim_id": str(item.claim_id), "evidence_id": str(item.evidence_id)}
            ),
        }
        for item in result.citations
    )
    return {
        "status": "succeeded",
        "answer": result.answer,
        "used_tools": tuple(result.used_tools),
        "citations": citations,
    }
