from typing import Protocol
from uuid import UUID

from cinegraph.domain.models.identity import UserAccount


class UserAccountRepository(Protocol):
    def get_by_email(self, normalized_email: str) -> UserAccount | None: ...

    def get_by_user_id(
        self, user_id: UUID, *, for_update: bool = False
    ) -> UserAccount | None: ...

    def add(self, account: UserAccount) -> None: ...

    def save(self, account: UserAccount) -> None: ...
