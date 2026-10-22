import os
from pathlib import Path

from Slop.Config.Config import Config
from Slop.Config.ConfigRepo import ConfigRepo
from Slop.Config.Inventory import Inventory
from Slop.Config.YamlFile import YamlFile
from Slop.Project.ProjectLoader import ProjectLoader
from Slop.SlopError import SlopError


class ConfigLoader:
    LOCAL = "slop.local.yaml"

    def __init__(self, configDir: Path | None = None, base: Path | None = None):
        self.configDir = (configDir or self.defaultDir()).resolve()
        self.base = (base or self.configDir.parent).resolve()

    @staticmethod
    def defaultDir() -> Path:
        if os.environ.get("SLOP_CONFIG"):
            return Path(os.environ["SLOP_CONFIG"])
        if (Path.cwd() / "config" / "slop.yaml").is_file():
            return Path.cwd() / "config"
        return Path(__file__).resolve().parents[3] / "config"

    @classmethod
    def exists(cls, configDir: Path | None) -> bool:
        return ((configDir or cls.defaultDir()) / "slop.yaml").is_file()

    def raw(self) -> dict:
        file = self.configDir / "slop.yaml"
        data = YamlFile.read(file)
        local = YamlFile.read(self.configDir / self.LOCAL, missingOk=True)
        return YamlFile.merge(data, local)

    def load(self, root: Path | None = None) -> Config:
        file = self.configDir / "slop.yaml"
        data = self.raw()
        root = root or (Path(os.environ["SLOP_ROOT"]) if os.environ.get("SLOP_ROOT") else None)
        if root is None:
            if "root" not in data:
                raise SlopError(f"{file}: no root set (or pass --root)")
            root = Path(data["root"])
        root = self.resolve(root)
        logDir = self.resolve(Path(data["log-dir"])) if data.get("log-dir") else root / ".slop" / "log"
        lockDir = self.resolve(Path(data["lock-dir"])) if data.get("lock-dir") else root / ".slop" / "locks"

        inventoryData = YamlFile.merge(
            YamlFile.read(self.configDir / "inventory.yaml", missingOk=True), data.get("inventory") or {}
        )
        inventory = Inventory.fromSpec(inventoryData, str(self.configDir / "inventory.yaml"))
        gerrit = str(data.get("gerrit") or "https://gerrit.wikimedia.org/r")

        specs = data.get("projects") or {}
        if not specs:
            raise SlopError(f"{file}: no projects listed")
        projects = {}
        for name, spec in specs.items():
            spec = spec or {}
            if "type" not in spec:
                raise SlopError(f"{file}: project {name!r} has no type ({', '.join(ProjectLoader.all())})")
            klass = ProjectLoader.get(spec["type"])
            projects[str(name)] = klass(str(name), spec, root=root, configDir=self.configDir / str(name), gerrit=gerrit)

        defaults = data.get("default") or list(projects)[:1]
        defaults = [defaults] if isinstance(defaults, str) else [str(d) for d in defaults]
        for name in defaults:
            if name not in projects:
                raise SlopError(f"{file}: default project {name!r} is not defined")

        repoSpec = data.get("config-repo") or {}
        repo = ConfigRepo(self.configDir, str(repoSpec.get("remote", "origin")), str(repoSpec.get("branch", "main")))
        production = bool(data.get("production", False)) or root == Path("/srv") or root.is_relative_to("/srv")

        return Config(
            configDir=self.configDir,
            root=root,
            logDir=logDir,
            lockDir=lockDir,
            projects=projects,
            defaults=defaults,
            inventory=inventory,
            repo=repo,
            gerrit=gerrit,
            sal=dict(data.get("sal") or {}),
            notify=dict(data.get("notify") or {}),
            push=bool(repoSpec.get("push", True)),
            pull=bool(repoSpec.get("pull", True)),
            production=production,
            data=data,
        )

    def resolve(self, path: Path) -> Path:
        return (path if path.is_absolute() else self.base / path).resolve()
