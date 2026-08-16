from typing import Protocol


class SessionTokenGenerator(Protocol):
    def generate(self) -> str: ...
