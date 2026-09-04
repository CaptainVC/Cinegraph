"""Stdlib-only immutable policy for private-corpus bundles and handoff."""

from dataclasses import dataclass
from typing import Final

# These layout literals are shared by the application-side corpus configuration and
# the dependency-free host receiver. Keeping one source of truth prevents a normal
# application change from silently making transferred bundles unreadable on the VPS.
REVIEW_LEDGER_FILENAME: Final = "review-ledger.json"
SCRIPT_PDF_FILENAME_TEMPLATE: Final = "Modern Family S{season:02d} Script.pdf"
SEASON_DIRECTORY_SUFFIX: Final = " - season {season_number:01}.en"
REVIEWED_DIRECTORY_NAME: Final = "reviewed"
ALIGNED_DIRECTORY_NAME: Final = "script-aligned"
REVIEWED_SUBTITLE_SUFFIX: Final = ".reviewed.srt"
ALIGNED_SUBTITLE_SUFFIX: Final = ".script-aligned.srt"


@dataclass(frozen=True, slots=True)
class PrivateCorpusBundleConfiguration:
    schema_version: int = 1
    manifest_filename: str = "manifest.json"
    purpose_reviewed_ingestion: str = "reviewed_ingestion"
    purpose_speaker_review: str = "speaker_review"
    allowed_extensions: frozenset[str] = frozenset({".json", ".pdf", ".srt"})
    review_ledger_filename: str = REVIEW_LEDGER_FILENAME
    script_pdf_filename_template: str = SCRIPT_PDF_FILENAME_TEMPLATE
    season_directory_suffix: str = SEASON_DIRECTORY_SUFFIX
    reviewed_directory_name: str = REVIEWED_DIRECTORY_NAME
    aligned_directory_name: str = ALIGNED_DIRECTORY_NAME
    reviewed_subtitle_suffix: str = REVIEWED_SUBTITLE_SUFFIX
    aligned_subtitle_suffix: str = ALIGNED_SUBTITLE_SUFFIX
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
        b"BEGIN " + b"PRIVATE" + b" KEY",
        b"BEGIN RSA " + b"PRIVATE" + b" KEY",
        b"BEGIN OPENSSH " + b"PRIVATE" + b" KEY",
    )

    @property
    def allowed_purposes(self) -> frozenset[str]:
        return frozenset({self.purpose_reviewed_ingestion, self.purpose_speaker_review})


DEFAULT_PRIVATE_CORPUS_BUNDLE_CONFIGURATION = PrivateCorpusBundleConfiguration()
