from dataclasses import dataclass, field

from Slop.Config.Patch import Patch
from Slop.Config.Repo import Repo


@dataclass(frozen=True)
class Version:
    name: str
    core: Repo
    repos: list[Repo] = field(default_factory=list)
    patches: list[Patch] = field(default_factory=list)
