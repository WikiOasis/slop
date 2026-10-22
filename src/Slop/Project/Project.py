import argparse
from abc import ABC, abstractmethod
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING

from Slop.Autoload.Autoloader import Autoloader
from Slop.Config.Patch import Patch
from Slop.Config.StateFile import StateFile
from Slop.Config.YamlFile import YamlFile
from Slop.Project.Checkout import Checkout
from Slop.Project.DeploySpec import DeploySpec
from Slop.SlopError import SlopError

if TYPE_CHECKING:
    from Slop.Git.Git import Git
    from Slop.Module.Module import Module


class Project(ABC):
    type: str
    description: str
    STAGES: dict[str, list[str]]
    TARGETS: dict[str, tuple[list[str] | None, bool, str]]
    ALIASES: dict[str, str] = {}
    STATE_FILE = ".slop-state.json"

    def __init__(self, name: str, spec: dict, *, root: Path, configDir: Path, gerrit: str):
        self.name = name
        self.spec = spec
        self.configDir = configDir
        self.gerrit = gerrit
        self.root = root / spec.get("path", self.defaultPath(name))
        self.stages = {**self.STAGES, **(spec.get("stages") or {})}
        self.deploy = DeploySpec.fromSpec(
            spec.get("deploy") or {}, root, self.deployDefaults(), f"slop.yaml: projects.{name}"
        )

    @classmethod
    def defaultPath(cls, name: str) -> str:
        return f"deployment/{name}"

    def deployDefaults(self) -> dict:
        return {"exclude": [".git", ".slop"]}

    @classmethod
    @abstractmethod
    def moduleNamespace(cls) -> ModuleType: ...

    @classmethod
    def modules(cls) -> dict[str, type["Module"]]:
        from Slop.Module.Module import Module

        if "_modules" not in cls.__dict__:
            cls._modules = {m.name: m for m in Autoloader.subclasses(cls.moduleNamespace(), Module)}
        return cls._modules

    @classmethod
    def module(cls, name: str) -> type["Module"]:
        try:
            return cls.modules()[name]
        except KeyError:
            raise SlopError(f"{cls.type} projects have no module {name!r} ({', '.join(cls.modules())})") from None

    def stage(self, name: str) -> list[str]:
        if name not in self.stages:
            raise SlopError(f"project {self.name} has no {name!r} stage")
        return list(self.stages[name])

    @classmethod
    def addOptions(cls, parser: argparse.ArgumentParser) -> None:
        pass

    def applyOptions(self, args: argparse.Namespace) -> None:
        pass

    def scope(self, what: str, names: list[str]) -> set[Path] | None:
        if names:
            raise SlopError(f"`slop update {what}` doesn't take names for {self.type} projects")
        return None

    def summary(self) -> str:
        return self.type

    @abstractmethod
    def checkouts(self) -> list[Checkout]: ...

    def checkout(self, name: str) -> Checkout:
        found = [c for c in self.checkouts() if name in (c.key, c.repo.name, c.repo.path.as_posix())]
        if not found:
            found = [c for c in self.checkouts() if name.lower() in (c.repo.name.lower(), c.key.lower())]
        if len(found) > 1:
            raise SlopError(f"{name!r} is ambiguous: {', '.join(c.key for c in found)}")
        if not found:
            raise SlopError(f"{self.name} has no checkout named {name!r}")
        return found[0]

    def select(self, name: str) -> list[Checkout]:
        candidates = self.selectable()
        exact = [c for c in candidates if name in (c.key, c.repo.name, c.repo.path.as_posix())]
        found = exact or [
            c
            for c in candidates
            if name.lower() in (c.repo.name.lower(), c.key.lower(), c.repo.path.as_posix().lower())
        ]
        if not found:
            raise SlopError(f"{self.name} has no repo named {name!r} (see `slop repo list`)")
        return found

    def selectable(self) -> list[Checkout]:
        return self.checkouts()

    def stateFile(self, configDir: Path | None = None) -> StateFile:
        return StateFile(configDir or self.configDir.parent, self.name)

    def configPaths(self) -> list[str]:
        return [self.name, "slop.yaml", "inventory.yaml", *self.stateFile().paths]

    def manifest(self, git: "Git") -> dict:
        repos = {}
        for checkout in self.checkouts():
            if not (checkout.dest / ".git").exists():
                continue
            info = git.describe(checkout.dest)
            repos[checkout.key] = {
                "path": checkout.repo.path.as_posix(),
                "url": checkout.repo.url,
                "branch": checkout.repo.branch,
                "base": info["base"],
                "head": info["head"],
                **({"pinned": checkout.repo.commit} if checkout.repo.commit else {}),
                **({"task": checkout.repo.task} if checkout.repo.task else {}),
                **({"patches": [self.patchRecord(p) for p in checkout.patches]} if checkout.patches else {}),
            }
        return repos

    @staticmethod
    def patchRecord(patch: Patch) -> dict:
        record = {"name": patch.name, "source": patch.source}
        if patch.description:
            record["description"] = patch.description
        if patch.task:
            record["task"] = patch.task
        return record

    def lintFiles(self) -> list[Path]:
        return []

    def lockfiles(self) -> dict[str, Path]:
        return {}

    def syncPaths(self, only: list[str] | None) -> list[str] | None:
        return None

    def afterSyncCommands(self, changed: set[str], path: Path) -> list[str]:
        return [c.format(path=path) for c in self.deploy.afterSync]

    def patchesFile(self, checkout: Checkout) -> Path:
        return self.configDir / "patches.yaml"

    def addPatch(self, checkout: Checkout, entry: dict) -> Path:
        file = self.patchesFile(checkout)
        data = YamlFile.read(file, missingOk=True)
        patches = list(data.get("patches") or [])
        name = entry.get("name")
        if name and any(p.get("name") == name for p in patches):
            raise SlopError(f"{file.name} already has a patch named {name!r}")
        patches.append(entry)
        data["patches"] = patches
        YamlFile.write(file, data)
        return file

    def updatePatch(self, checkout: Checkout, patch: Patch, changes: dict) -> Path:
        file = self.patchesFile(checkout)
        data = YamlFile.read(file, missingOk=True)
        for entry in data.get("patches") or []:
            if Patch.fromSpec(entry, self.patchesDir(checkout), self.gerrit, str(file)).name == patch.name:
                entry.update(changes)
                for key in [k for k, v in changes.items() if v is None]:
                    entry.pop(key, None)
                YamlFile.write(file, data)
                return file
        raise SlopError(f"{file.name} has no patch named {patch.name!r}")

    def removePatch(self, checkout: Checkout, patch: Patch) -> Path:
        file = self.patchesFile(checkout)
        data = YamlFile.read(file, missingOk=True)
        kept = [
            entry
            for entry in data.get("patches") or []
            if Patch.fromSpec(entry, self.patchesDir(checkout), self.gerrit, str(file)).name != patch.name
        ]
        data["patches"] = kept
        YamlFile.write(file, data)
        return file

    def patchesDir(self, checkout: Checkout) -> Path:
        return self.configDir / "patches"

    def saveResolvedPatch(self, checkout: Checkout, patch: Patch, diff: str) -> None:
        directory = self.patchesDir(checkout)
        directory.mkdir(parents=True, exist_ok=True)
        filename = f"{patch.name}.patch"
        (directory / filename).write_text(diff)
        self.updatePatch(checkout, patch, {"patch": filename})

    def reposFile(self, checkout: Checkout) -> Path | None:
        return None

    def setRepoField(self, checkout: Checkout, changes: dict) -> Path:
        raise SlopError(f"{self.type} projects keep their repo in slop.yaml; edit projects.{self.name} there")
