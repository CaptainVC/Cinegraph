from threading import RLock
from uuid import UUID

from cinegraph.common.error_messages import MediaActionErrorMessages
from cinegraph.domain.enums.enum import ApprovalStatus
from cinegraph.domain.models.media_action import ApprovalRequest


class InMemoryApprovalRepository:
    def __init__(self) -> None:
        self._items: dict[UUID, ApprovalRequest] = {}
        self._approval_id_by_idempotency_key: dict[str, UUID] = {}
        self._lock = RLock()

    def add(self, approval: ApprovalRequest) -> None:
        with self._lock:
            if approval.approval_id in self._items:
                raise ValueError(MediaActionErrorMessages.APPROVAL_TRANSITION_INVALID)
            if approval.idempotency_key in self._approval_id_by_idempotency_key:
                raise ValueError(MediaActionErrorMessages.IDEMPOTENCY_KEY_REUSED)
            self._items[approval.approval_id] = approval
            self._approval_id_by_idempotency_key[approval.idempotency_key] = (
                approval.approval_id
            )

    def get(self, approval_id: UUID) -> ApprovalRequest | None:
        with self._lock:
            return self._items.get(approval_id)

    def find_by_idempotency_key(self, key: str) -> ApprovalRequest | None:
        with self._lock:
            approval_id = self._approval_id_by_idempotency_key.get(key)
            return self._items.get(approval_id) if approval_id is not None else None

    def save(
        self,
        approval: ApprovalRequest,
        *,
        expected_status: ApprovalStatus,
    ) -> None:
        with self._lock:
            current = self._items.get(approval.approval_id)
            if current is None or current.status is not expected_status:
                raise ValueError(MediaActionErrorMessages.APPROVAL_TRANSITION_INVALID)
            self._items[approval.approval_id] = approval
