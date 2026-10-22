from pathlib import Path
from types import ModuleType

from Slop.Config.Patch import Patch
from Slop.Config.Repo import Repo
from Slop.Project.Checkout import Checkout
from Slop.Project.Project import Project


class Service(Project):
    type = "service"
    description = "a git-deployed service built on the deploy master and pushed to its roles"

    STAGES = {
        "build": ["checkout", "patches", "commands"],
    }
    TARGETS = {
        "all": (None, False, "everything (the whole build stage)"),
        "code": (["checkout", "patches", "commands"], False, "the service checkout, its patches, then build commands"),
        "patches": (["patches", "commands"], False, "apply patches, then build commands"),
        "commands": (["commands"], False, "re-run the build commands"),
    }
    ALIASES = {"checkout": "code", "repo": "code", "patch": "patches", "build": "commands"}

    def __init__(self, name: str, spec: dict, *, root: Path, configDir: Path, gerrit: str):
        super().__init__(name, spec, root=root, configDir=configDir, gerrit=gerrit)
        self.repo = Repo.fromSpec(
            Path("."), spec, defaultUrl=None, defaultBranch="main", where=f"slop.yaml: projects.{name}"
        )
        patchesFile = configDir / "patches.yaml"
        self.patches = Patch.loadFile(patchesFile, configDir / "patches", gerrit) if patchesFile.exists() else []
        self.commands = [str(c) for c in (spec.get("build") or [])]

    @classmethod
    def moduleNamespace(cls) -> ModuleType:
        import Slop.Project.Service.Module

        return Slop.Project.Service.Module

    def checkouts(self) -> list[Checkout]:
        return [Checkout(key=".", repo=self.repo, dest=self.root, patches=self.patches, scope=Path("."))]

    def summary(self) -> str:
        return f"service <comment>{self.repo.commit[:10] if self.repo.commit else self.repo.branch}</comment>"
