from enum import StrEnum

class SpoilerMode(StrEnum):
    STRICT = "strict"
    SEQUENTIAL = "sequential"
    RELAXED = "relaxed"

class WatchEventKind(StrEnum):
    EPISODE_MARKED_WATCHED = "episode_marked_watched"
    EPISODE_MARKED_UNWATCHED = "episode_marked_unwatched"


class WatchEventSource(StrEnum):
    MANUAL = "manual"
    JELLYFIN = "jellyfin"
    NETFLIX_CSV = "netflix_csv"

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
    REVIEWED = "reviewed"
    REJECTED = "rejected"


class SourceVersionStatus(StrEnum):
    ACTIVE = "active"
    RETIRED = "retired"
