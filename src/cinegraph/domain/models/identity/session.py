import re
from dataclasses import dataclass, replace
from datetime import datetime
from uuid import UUID

from cinegraph.common.error_messages import AuthenticationErrorMessages
from cinegraph.domain.enums.enum import CorpusAccessMode, PrincipalKind
from cinegraph.domain.exceptions.errors import InvalidModelError
from cinegraph.domain.models.access import CorpusAccessScope


@dataclass(frozen=True, slots=True)
class SessionPrincipal:
    kind: PrincipalKind
    profile_id: UUID
    corpus_access_scope: CorpusAccessScope
    user_id: UUID | None = None

    def __post_init__(self) -> None:
        valid_guest = (
            self.kind is PrincipalKind.GUEST
            and self.user_id is None
            and self.corpus_access_scope.mode is CorpusAccessMode.GUEST
        )
        valid_authenticated = (
            self.kind is PrincipalKind.AUTHENTICATED
            and self.user_id is not None
            and self.corpus_access_scope.mode is CorpusAccessMode.AUTHENTICATED
        )
        if not (valid_guest or valid_authenticated):
            raise InvalidModelError(
                AuthenticationErrorMessages.SESSION_PRINCIPAL_MUST_MATCH_KIND
            )


@dataclass(frozen=True, slots=True)
class SessionRecord:
    session_id: UUID
    token_sha256: str
    principal: SessionPrincipal
    created_at: datetime
    expires_at: datetime
    revoked_at: datetime | None = None

    def __post_init__(self) -> None:
        if re.fullmatch(r"[0-9a-f]{64}", self.token_sha256) is None:
            raise InvalidModelError(
                AuthenticationErrorMessages.SESSION_TOKEN_DIGEST_MUST_BE_VALID
            )
        if self.expires_at <= self.created_at:
            raise InvalidModelError(
                AuthenticationErrorMessages.SESSION_EXPIRY_MUST_FOLLOW_CREATION
            )
        if self.revoked_at is not None and self.revoked_at < self.created_at:
            raise InvalidModelError(
                AuthenticationErrorMessages.SESSION_REVOCATION_MUST_NOT_PREDATE_CREATION
            )

    def revoke(self, revoked_at: datetime) -> "SessionRecord":
        return replace(self, revoked_at=revoked_at)
