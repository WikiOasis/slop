from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from Slop.Config.ConfigRepo import ConfigRepo
from Slop.Config.Inventory import Inventory

if TYPE_CHECKING:
    from Slop.Project.Project import Project


@dataclass
class Config:
    configDir: Path
    root: Path
    logDir: Path
    lockDir: Path
    projects: dict[str, "Project"]
    defaults: list[str]
    inventory: Inventory
    repo: ConfigRepo
    gerrit: str = "https://gerrit.wikimedia.org/r"
    sal: dict = field(default_factory=dict)
    notify: dict = field(default_factory=dict)
    push: bool = True
    pull: bool = True
    production: bool = False
    data: dict = field(default_factory=dict)

    def project(self, name: str) -> "Project":
        from Slop.SlopError import SlopError

        if name not in self.projects:
            raise SlopError(f"no project named {name!r} ({', '.join(self.projects)})")
        return self.projects[name]
