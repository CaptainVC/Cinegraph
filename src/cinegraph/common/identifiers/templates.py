from uuid import NAMESPACE_URL


class IdentifierTemplates:
    NAMESPACE = NAMESPACE_URL

    SOURCE_VERSION = "cinegraph:source-version:{source_document_id}:{content_hash}"
    TRANSCRIPT_SOURCE_DOCUMENT = (
        "cinegraph:source-document:transcript:{episode_id}:{language}:{origin}"
    )
    SPEAKER = "cinegraph:speaker:{series_id}:{speaker_name}"
    TRANSCRIPT_SEGMENT = (
        "cinegraph:transcript-segment:{source_version_id}:{episode_id}:"
        "{cue_number}:{start_ms}:{end_ms}:{text}"
    )
    TRANSCRIPT_CHUNK = (
        "cinegraph:transcript-chunk:{revision}:{source_version_id}:"
        "{series_id}:{season_id}:{episode_id}:{segment_ids}"
    )
    EPISODE_SUMMARY = "cinegraph:episode-summary:{source_version_id}:{episode_id}:{language}"
    EPISODE_SUMMARY_SOURCE_DOCUMENT = (
        "cinegraph:source-document:episode-summary:{episode_id}:{language}:{origin}"
    )
    SERIES_METADATA_SOURCE_DOCUMENT = (
        "cinegraph:source-document:series-metadata:{series_id}:{origin}"
    )
    GRAPH_ENTITY = "cinegraph:graph-entity:{series_id}:{kind}:{normalized_key}"
    GRAPH_ENTITY_ALIAS = "cinegraph:graph-entity-alias:{entity_id}:{normalized_alias}"
    GRAPH_CLAIM = "cinegraph:graph-claim:{revision}:{series_id}:{subject}:{predicate}:{object}:{polarity}"
    GRAPH_EVIDENCE = "cinegraph:graph-evidence:{claim_id}:{source_version_id}:{chunk_id}"
