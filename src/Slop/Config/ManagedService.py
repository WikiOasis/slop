from dataclasses import dataclass


@dataclass(frozen=True)
class ManagedService:
    name: str
    units: list[str]
    action: str = "reload"
