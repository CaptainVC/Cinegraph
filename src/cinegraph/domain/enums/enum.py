from enum import StrEnum


class SpoilerMode(StrEnum):
    STRICT = "strict"
    SEQUENTIAL = "sequential"
    RELAXED = "relaxed"


class CorpusAccessMode(StrEnum):
    GUEST = "guest"
    AUTHENTICATED = "authenticated"


class AccountStatus(StrEnum):
    ACTIVE = "active"
    DISABLED = "disabled"


class PrincipalKind(StrEnum):
    GUEST = "guest"
    AUTHENTICATED = "authenticated"


class WatchPreference(StrEnum):
    ANY = "any"
    WATCHED = "watched"
    UNWATCHED = "unwatched"


class MediaCommandKind(StrEnum):
    MARK_WATCHED = "mark_watched"
    SET_FAVORITE = "set_favorite"
    CREATE_PLAYLIST = "create_playlist"
    REQUEST_PLAYBACK = "request_playback"


class MediaCommandRisk(StrEnum):
    REVERSIBLE_LOW_RISK = "reversible_low_risk"
    REVERSIBLE_MULTI_ITEM = "reversible_multi_item"


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXECUTED = "executed"
    VERIFIED = "verified"


class MediaActionAuditStage(StrEnum):
    PROPOSED = "proposed"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXECUTED = "executed"
    VERIFIED = "verified"

class WatchEventKind(StrEnum):
    EPISODE_MARKED_WATCHED = "episode_marked_watched"
    EPISODE_MARKED_UNWATCHED = "episode_marked_unwatched"


class WatchEventSource(StrEnum):
    MANUAL = "manual"
    JELLYFIN = "jellyfin"
    NETFLIX_CSV = "netflix_csv"


class NetflixHistoryImportStatus(StrEnum):
    PENDING_REVIEW = "pending_review"
    COMMITTED = "committed"
    EXPIRED = "expired"


class NetflixTitleResolutionStatus(StrEnum):
    MATCHED = "matched"
    AMBIGUOUS = "ambiguous"
    UNMATCHED = "unmatched"

class RightsStatus(StrEnum):
    ALLOWED = "allowed"
    RESTRICTED = "restricted"
    UNKNOWN = "unknown"


class Language(StrEnum):
    ENGLISH = "en"

class SourceKind(StrEnum):
    SUBTITLE = "subtitle"
    EPISODE_PLOT = "episode_plot"
    METADATA = "metadata"


class SourceAcquisitionMethod(StrEnum):
    USER_UPLOAD = "user_upload"
    LOCAL_FILESYSTEM = "local_filesystem"
    EMBEDDED_SUBTITLE_TRACK = "embedded_subtitle_track"
    MEDIAWIKI_API = "mediawiki_api"
    TVMAZE_API = "tvmaze_api"
    SYNTHETIC_FIXTURE = "synthetic_fixture"


class SourceReviewStatus(StrEnum):
    PENDING = "pending"
    AUTOMATED_REVIEWED = "automated_reviewed"
    HYBRID_REVIEWED = "hybrid_reviewed"
    REVIEWED = "reviewed"
    REJECTED = "rejected"


class SourceVersionStatus(StrEnum):
    ACTIVE = "active"
    RETIRED = "retired"


class SpeakerReviewAction(StrEnum):
    ACCEPT_CANDIDATE = "accept_candidate"
    CORRECT_CANDIDATE = "correct_candidate"
    NEEDS_REVIEW = "needs_review"


class SpeakerReviewDisposition(StrEnum):
    CONSENSUS_ACCEPTED = "consensus_accepted"
    ADJUDICATION_REQUIRED = "adjudication_required"
    ADJUDICATION_ACCEPTED = "adjudication_accepted"
    FINAL_REVIEW_ACCEPTED = "final_review_accepted"
    HUMAN_REVIEW_ACCEPTED = "human_review_accepted"
    NEEDS_HUMAN = "needs_human"


class SpeakerReviewRunStatus(StrEnum):
    PREPARED = "prepared"
    PRIMARY_SUBMITTED = "primary_submitted"
    ADJUDICATION_SUBMITTED = "adjudication_submitted"
    FINAL_REVIEW_SUBMITTED = "final_review_submitted"
    COMPLETED = "completed"
    NEEDS_HUMAN = "needs_human"
    FAILED = "failed"


class IngestionJobKind(StrEnum):
    SPEAKER_REVIEW = "speaker_review"
    TRANSCRIPT_INGESTION = "transcript_ingestion"
    VECTOR_INDEX = "vector_index"
    EPISODE_SUMMARY = "episode_summary"
    SERIES_METADATA = "series_metadata"
    SUBTITLE_ALIGNMENT = "subtitle_alignment"
    GRAPH_CLAIM_EXTRACTION = "graph_claim_extraction"


class GraphEntityKind(StrEnum):
    CHARACTER = "character"
    PERSON = "person"
    LOCATION = "location"
    ORGANIZATION = "organization"
    OBJECT = "object"
    EVENT = "event"
    CONCEPT = "concept"


class GraphClaimPolarity(StrEnum):
    ASSERTED = "asserted"
    NEGATED = "negated"
    UNCERTAIN = "uncertain"


class IngestionJobStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class IngestionJobEventKind(StrEnum):
    ENQUEUED = "enqueued"
    CLAIMED = "claimed"
    HEARTBEAT = "heartbeat"
    RETRIED = "retried"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    RECLAIMED = "reclaimed"


class CorpusReadinessStatus(StrEnum):
    REVIEWED_READY = "reviewed_ready"
    AWAITING_AUTOMATED_REVIEW = "awaiting_automated_review"
    AWAITING_ALIGNMENT = "awaiting_alignment"
    MISSING = "missing"
    INVALID = "invalid"


class CorpusInventoryReason(StrEnum):
    VERIFIED_REVIEW_LEDGER = "verified_review_ledger"
    SCRIPT_ALIGNED_WITHOUT_FINAL_REVIEW = "script_aligned_without_final_review"
    RAW_SUBTITLE_REQUIRES_SCRIPT_ALIGNMENT = "raw_subtitle_requires_script_alignment"
    RAW_SUBTITLE_MISSING = "raw_subtitle_missing"
    MISSING_REVIEWED_LOCATOR = "missing_reviewed_locator"
    REVIEW_LEDGER_MISSING = "review_ledger_missing"
    REVIEW_LEDGER_HASH_OR_SCOPE_MISMATCH = "review_ledger_hash_or_scope_mismatch"
    REVIEW_LEDGER_INVALID = "review_ledger_invalid"
    ARTIFACT_UNREADABLE = "artifact_unreadable"
    UNSAFE_LOCATOR = "unsafe_locator"
