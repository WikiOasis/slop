from dataclasses import dataclass, field


@dataclass(frozen=True)
class Role:
    name: str
    hosts: list[str]
    batch: int = 1
    services: list[str] = field(default_factory=list)
    soak: float = 0.0
