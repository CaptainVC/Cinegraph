from uuid import NAMESPACE_URL


class IdentifierTemplates:
    NAMESPACE = NAMESPACE_URL

    SOURCE_VERSION = "cinegraph:source-version:{source_document_id}:{content_hash}"
    SPEAKER = "cinegraph:speaker:{series_id}:{speaker_name}"
    TRANSCRIPT_SEGMENT = (
        "cinegraph:transcript-segment:{source_version_id}:{episode_id}:"
        "{cue_number}:{start_ms}:{end_ms}:{text}"
    )
    EPISODE_SUMMARY = (
        "cinegraph:episode-summary:{source_version_id}:{episode_id}:{language}"
    )
    EPISODE_SUMMARY_SOURCE_DOCUMENT = (
        "cinegraph:source-document:episode-summary:"
        "{episode_id}:{language}:{origin}"
    )
