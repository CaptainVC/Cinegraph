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
