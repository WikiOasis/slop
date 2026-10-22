from dataclasses import dataclass, field
from pathlib import Path

from Slop.Config.Patch import Patch
from Slop.Config.Repo import Repo


@dataclass(frozen=True)
class Checkout:
    key: str
    repo: Repo
    dest: Path
    patches: list[Patch] = field(default_factory=list)
    scope: Path | None = None

    @property
    def label(self) -> str:
        return self.key
