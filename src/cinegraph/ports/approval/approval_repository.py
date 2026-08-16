from typing import Protocol
from uuid import UUID

from cinegraph.domain.enums.enum import ApprovalStatus
from cinegraph.domain.models.media_action import ApprovalRequest


class ApprovalRepository(Protocol):
    def add(self, approval: ApprovalRequest) -> None: ...

    def get(self, approval_id: UUID) -> ApprovalRequest | None: ...

    def find_by_idempotency_key(self, key: str) -> ApprovalRequest | None: ...

    def save(
        self,
        approval: ApprovalRequest,
        *,
        expected_status: ApprovalStatus,
    ) -> None: ...
