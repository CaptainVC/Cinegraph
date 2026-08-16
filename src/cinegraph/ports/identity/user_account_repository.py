from typing import Protocol

from cinegraph.domain.models.identity import UserAccount


class UserAccountRepository(Protocol):
    def get_by_email(self, normalized_email: str) -> UserAccount | None: ...

    def add(self, account: UserAccount) -> None: ...
