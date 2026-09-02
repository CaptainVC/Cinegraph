"""Immutable policy for private-corpus bundle creation and verification.

Keeping this policy in configuration makes the common bundle implementation
free of deployment-specific literals and gives tests one safe policy seam to
override with a complete replacement configuration.
"""

from dataclasses import dataclass

from cinegraph.config.corpus import DEFAULT_CORPUS_LAYOUT
from cinegraph.config.speaker_review import DEFAULT_SPEAKER_REVIEW_CONFIGURATION


@dataclass(frozen=True, slots=True)
class PrivateCorpusBundleConfiguration:
    schema_version: int = 1
    manifest_filename: str = "manifest.json"
    purpose_reviewed_ingestion: str = "reviewed_ingestion"
    purpose_speaker_review: str = "speaker_review"
    allowed_extensions: frozenset[str] = frozenset({".json", ".pdf", ".srt"})
    review_ledger_filename: str = DEFAULT_CORPUS_LAYOUT.review_ledger_filename
    script_pdf_filename_template: str = (
        DEFAULT_SPEAKER_REVIEW_CONFIGURATION.script_pdf_filename_template
    )
    season_directory_suffix: str = DEFAULT_CORPUS_LAYOUT.season_directory_suffix
    reviewed_directory_name: str = DEFAULT_CORPUS_LAYOUT.reviewed_directory_name
    aligned_directory_name: str = DEFAULT_CORPUS_LAYOUT.aligned_directory_name
    reviewed_subtitle_suffix: str = DEFAULT_CORPUS_LAYOUT.reviewed_subtitle_suffix
    aligned_subtitle_suffix: str = DEFAULT_CORPUS_LAYOUT.aligned_subtitle_suffix
    max_file_count: int = 256
    max_total_bytes: int = 64 * 1024 * 1024
    max_file_bytes: int = 32 * 1024 * 1024
    max_manifest_bytes: int = 256 * 1024
    max_archive_bytes: int = 65 * 1024 * 1024
    max_path_bytes: int = 240
    max_name_bytes: int = 120
    archive_compression: int = 0
    archive_mode: int = 0o100600
    directory_mode: int = 0o700
    file_mode: int = 0o600
    forbidden_exact_names: frozenset[str] = frozenset(
        {".env", "key.txt", "keys", "private.key"}
    )
    forbidden_name_pattern: str = (
        r"(?:^|[._-])(key|keys|secret|secrets|token|password|credential|credentials)(?:$|[._-])"
    )
    private_key_pattern: str = (
        r"(?:private[._ -]?key|id_(?:rsa|dsa|ecdsa|ed25519)|"
        r"(?:^|[._-])ed25519(?:$|[._-]))"
    )
    forbidden_content_markers: tuple[bytes, ...] = (
        b"OPENAI_API_KEY",
        b"BEGIN PRIVATE KEY",
        b"BEGIN RSA PRIVATE KEY",
        b"BEGIN OPENSSH PRIVATE KEY",
    )

    @property
    def allowed_purposes(self) -> frozenset[str]:
        return frozenset({self.purpose_reviewed_ingestion, self.purpose_speaker_review})


DEFAULT_PRIVATE_CORPUS_BUNDLE_CONFIGURATION = PrivateCorpusBundleConfiguration()
