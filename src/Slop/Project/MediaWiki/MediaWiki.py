import argparse
import difflib
from pathlib import Path
from types import ModuleType

from Slop.Config.Patch import Patch
from Slop.Config.Repo import Repo
from Slop.Config.YamlFile import YamlFile
from Slop.Project.Checkout import Checkout
from Slop.Project.MediaWiki.Version import Version
from Slop.Project.Project import Project
from Slop.SlopError import SlopError


class MediaWiki(Project):
    type = "mediawiki"
    description = "MediaWiki farm: core, extensions and skins per version, mw-config, composer"

    GERRIT = "https://gerrit.wikimedia.org/r/mediawiki"
    VERSION_TOKEN = "_version_"

    STAGES = {
        "build": ["layout", "sync", "private-settings", "patches", "composer", "links", "scripts"],
    }
    TARGETS = {
        "all": (None, False, "everything (the whole build stage)"),
        "config": (
            ["mw-config", "private-settings", "links"],
            False,
            "mw-config, PrivateSettings.php, then refresh symlinks",
        ),
        "core": (["core", "patches", "composer"], False, "MediaWiki core, its patches, then composer"),
        "extensions": (
            ["extensions", "patches", "composer"],
            True,
            "extensions and skins (all, or just NAME...), their patches, then composer",
        ),
        "composer": (["composer"], False, "merge composer.json files and run composer update"),
        "patches": (["patches"], True, "apply patches (all, or for NAME... / core)"),
        "links": (["links"], False, "LocalSettings.php and w/ entrypoint symlinks"),
        "scripts": (["scripts"], False, "copy the config's scripts/"),
    }
    ALIASES = {
        "ext": "extensions",
        "exts": "extensions",
        "extension": "extensions",
        "skins": "extensions",
        "skin": "extensions",
        "mw-config": "config",
        "patch": "patches",
    }
    SYNC_PATHS = {
        "config": ["config", "w"],
        "core": ["versions"],
        "extensions": ["versions"],
        "composer": ["versions"],
        "patches": ["versions"],
        "links": ["w", "versions"],
        "scripts": ["scripts"],
    }

    DEFAULT_PRIVATE_SETTINGS = "/srv/PrivateSettings.php"

    DEFAULT_ENTRYPOINTS = [
        "api.php",
        "img_auth.php",
        "index.php",
        "load.php",
        "opensearch_desc.php",
        "resources.php",
        "rest.php",
        "sitemap.php",
        "thumb.php",
    ]

    def __init__(self, name: str, spec: dict, *, root: Path, configDir: Path, gerrit: str):
        super().__init__(name, spec, root=root, configDir=configDir, gerrit=gerrit)
        where = f"slop.yaml: projects.{name}"
        self.linkRoot = Path(spec["link-root"]) if spec.get("link-root") else self.root

        self.mwConfig = None
        if mwConfig := spec.get("mw-config"):
            self.mwConfig = Repo.fromSpec(
                Path("config"), mwConfig, defaultUrl=None, defaultBranch="main", where=f"{where}.mw-config"
            )

        versionSpecs = spec.get("versions") or {}
        if not versionSpecs:
            raise SlopError(f"{where}: no versions listed")
        self.allVersions = {str(v): self.loadVersion(str(v), s or {}) for v, s in versionSpecs.items()}
        self.versions = list(self.allVersions.values())

        # private-settings: <path> copies that file into config/PrivateSettings.php; false turns the step off
        privateSettings = spec.get("private-settings", self.DEFAULT_PRIVATE_SETTINGS)
        self.privateSettings = None if privateSettings is False else Path(str(privateSettings))

        self.entrypoints = list(spec.get("entrypoints") or self.DEFAULT_ENTRYPOINTS)
        self.composerAllowPlugins = {
            str(plugin): bool(allow)
            for plugin, allow in ((spec.get("composer") or {}).get("allow-plugins") or {}).items()
        }

    @classmethod
    def defaultPath(cls, name: str) -> str:
        return "mediawiki"

    def deployDefaults(self) -> dict:
        return {
            "exclude": [
                ".git",
                ".slop",
                "/cache/",
                "/cw_cache/",
                "/l10n_cache/",
                "/images/",
                "node_modules/",
                "/versions/*/cache/",
                "/versions/*/images/",
            ],
            "l10n": {"when": "auto"},
            "checks": {
                "tests": [
                    {"name": "main page", "path": "/wiki/Main_Page", "expect": "wikioasis"},
                    {
                        "name": "api.php",
                        "path": "/w/api.php?action=query&meta=siteinfo&format=json",
                        "expect": '"sitename"',
                    },
                    {
                        "name": "load.php",
                        "path": "/w/load.php?modules=startup&only=scripts&raw=1",
                        "expect": "mw.loader",
                    },
                    {"name": "rest.php", "path": "/w/rest.php/v1/page/Main_Page/bare", "expect": '"latest"'},
                    {
                        "name": "Special:Version",
                        "path": "/wiki/Special:Version",
                        "expect": "MediaWiki",
                        "roles": ["canary"],
                    },
                    {
                        "name": "Special:RecentChanges",
                        "path": "/wiki/Special:RecentChanges",
                        "expect": "mw-changeslist",
                        "roles": ["canary"],
                    },
                ]
            },
        }

    def lintFiles(self) -> list[Path]:
        files = sorted(self.mwConfigDir.glob("*.php")) + sorted((self.mwConfigDir / "initialise").rglob("*.php"))
        return [f for f in files if f.is_file()]

    @classmethod
    def moduleNamespace(cls) -> ModuleType:
        import Slop.Project.MediaWiki.Module

        return Slop.Project.MediaWiki.Module

    def loadVersion(self, name: str, spec: dict) -> Version:
        relBranch = "REL" + name.replace(".", "_")
        core = Repo.fromSpec(
            Path("."),
            spec.get("core") or {},
            defaultUrl=f"{self.GERRIT}/core.git",
            defaultBranch=relBranch,
            defaultSubmodules=False,
            where=f"projects.{self.name}.versions.{name}.core",
        )
        core = core.withBranch(core.branch.replace(self.VERSION_TOKEN, relBranch))

        reposFile = self.configDir / f"repos-{name}.yaml"
        repos = []
        if reposFile.exists():
            data = YamlFile.read(reposFile)
            relBranch = data.get(self.VERSION_TOKEN) or relBranch
            repos = self.loadRepos(reposFile, data, relBranch)
            if (data.get("core") or {}).get("commit"):
                core = core.pinnedTo(str(data["core"]["commit"]))
        patchesFile = self.configDir / f"patches-{name}.yaml"
        patches = Patch.loadFile(patchesFile, self.configDir / "patches", self.gerrit) if patchesFile.exists() else []
        known = {Path("."), *(r.path for r in repos)}
        for patch in patches:
            if patch.path not in known:
                raise SlopError(
                    f"{patchesFile}: {patch.name} targets {patch.path}, which is not core or in {reposFile.name}"
                )
        return Version(name=name, core=core, repos=repos, patches=patches)

    def loadRepos(self, file: Path, data: dict, relBranch: str) -> list[Repo]:
        repos = []
        seen = set()
        for kind in ("extensions", "skins"):
            for i, entry in enumerate(data.get(kind) or []):
                where = f"{file}: {kind}[{i}]"
                if "path" not in entry:
                    raise SlopError(f"{where}: no path")
                path = Path(entry["path"])
                if path in seen:
                    raise SlopError(f"{where}: {path} is listed twice")
                seen.add(path)
                repo = Repo.fromSpec(
                    path,
                    entry,
                    defaultUrl=f"{self.GERRIT}/{path.as_posix()}.git",
                    defaultBranch=self.VERSION_TOKEN,
                    where=where,
                )
                repos.append(repo.withBranch(repo.branch.replace(self.VERSION_TOKEN, relBranch)))
        return repos

    @classmethod
    def addOptions(cls, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "--mw-version",
            dest="mwVersions",
            type=lambda v: [x for x in v.split(",") if x],
            metavar="V[,V...]",
            help="MediaWiki: only these versions",
        )

    def applyOptions(self, args: argparse.Namespace) -> None:
        if getattr(args, "mwVersions", None):
            unknown = set(args.mwVersions) - self.allVersions.keys()
            if unknown:
                raise SlopError(f"{self.name}: unknown version(s): {', '.join(sorted(unknown))}")
            self.versions = [self.allVersions[v] for v in args.mwVersions]

    def scope(self, what: str, names: list[str]) -> set[Path] | None:
        if what == "core":
            return {Path(".")}
        if what == "extensions":
            return self.resolve(names) if names else {r.path for v in self.versions for r in v.repos}
        if what == "patches" and names:
            return self.resolve(names, allowCore=True)
        return super().scope(what, names)

    def resolve(self, names: list[str], allowCore: bool = False) -> set[Path]:
        known = {repo.path for version in self.versions for repo in version.repos}
        scope = set()
        for name in names:
            if allowCore and name == "core":
                scope.add(Path("."))
                continue
            matches = {p for p in known if name in (p.name, p.as_posix())} or {
                p for p in known if name.lower() in (p.name.lower(), p.as_posix().lower())
            }
            if len(matches) > 1:
                raise SlopError(f"{name!r} is ambiguous: {', '.join(sorted(p.as_posix() for p in matches))}")
            if not matches:
                close = difflib.get_close_matches(name, sorted({p.name for p in known}), n=3)
                hint = f"; did you mean {' or '.join(close)}?" if close else ""
                raise SlopError(f"no extension or skin named {name!r} in repos-<v>.yaml{hint}")
            scope |= matches
        return scope

    def summary(self) -> str:
        return f"MediaWiki <comment>{', '.join(v.name for v in self.versions)}</comment>"

    @property
    def mwConfigDir(self) -> Path:
        return self.root / "config"

    @property
    def versionsDir(self) -> Path:
        return self.root / "versions"

    @property
    def wDir(self) -> Path:
        return self.root / "w"

    @property
    def scriptsDir(self) -> Path:
        return self.root / "scripts"

    def versionDir(self, version: Version) -> Path:
        return self.versionsDir / version.name

    def linkTarget(self, path: Path) -> Path:
        return self.linkRoot / path.relative_to(self.root)

    def versionCheckouts(self, version: Version) -> list[Checkout]:
        base = self.versionDir(version)
        key = f"versions/{version.name}"
        result = [
            Checkout(
                key=key,
                repo=version.core,
                dest=base,
                patches=[p for p in version.patches if p.path == Path(".")],
                scope=Path("."),
            )
        ]
        for repo in version.repos:
            result.append(
                Checkout(
                    key=f"{key}/{repo.path.as_posix()}",
                    repo=repo,
                    dest=base / repo.path,
                    patches=[p for p in version.patches if p.path == repo.path],
                    scope=repo.path,
                )
            )
        return result

    def checkouts(self) -> list[Checkout]:
        result = []
        if self.mwConfig:
            result.append(Checkout(key="config", repo=self.mwConfig, dest=self.mwConfigDir))
        for version in self.allVersions.values():
            result += self.versionCheckouts(version)
        return result

    def selectable(self) -> list[Checkout]:
        result = [c for c in self.checkouts() if c.key == "config"]
        for version in self.versions:
            result += self.versionCheckouts(version)
        return result

    def addRepo(self, version: Version, entry: dict) -> Path:
        file = self.configDir / f"repos-{version.name}.yaml"
        data = YamlFile.read(file, missingOk=True)
        kind = "skins" if entry["path"].startswith("skins/") else "extensions"
        items = list(data.get(kind) or [])
        if any(Path(e.get("path", "")) == Path(entry["path"]) for e in items):
            raise SlopError(f"{entry['path']} is already in {file.name}")
        items.append(entry)
        items.sort(key=lambda e: str(e.get("path", "")).lower())
        data[kind] = items
        YamlFile.write(file, data)
        return file

    def removeRepo(self, checkout: Checkout) -> Path:
        file = self.reposFile(checkout)
        data = YamlFile.read(file, missingOk=True)
        for kind in ("extensions", "skins"):
            data[kind] = [e for e in data.get(kind) or [] if Path(e.get("path", "")) != checkout.repo.path]
            if not data[kind]:
                data.pop(kind)
        YamlFile.write(file, data)
        return file

    def versionOf(self, checkout: Checkout) -> Version:
        if not checkout.key.startswith("versions/"):
            raise SlopError(f"{checkout.key} is not part of a MediaWiki version")
        return self.allVersions[checkout.key.split("/")[1]]

    def patchesFile(self, checkout: Checkout) -> Path:
        return self.configDir / f"patches-{self.versionOf(checkout).name}.yaml"

    def reposFile(self, checkout: Checkout) -> Path | None:
        return self.configDir / f"repos-{self.versionOf(checkout).name}.yaml"

    def setRepoField(self, checkout: Checkout, changes: dict) -> Path:
        if checkout.key == "config":
            raise SlopError("mw-config is set in slop.yaml (projects.mediawiki.mw-config)")
        file = self.reposFile(checkout)
        data = YamlFile.read(file, missingOk=True)
        if checkout.scope == Path("."):
            entry = data.setdefault("core", {})
        else:
            entry = next(
                (
                    e
                    for kind in ("extensions", "skins")
                    for e in data.get(kind) or []
                    if Path(e.get("path", "")) == checkout.repo.path
                ),
                None,
            )
            if entry is None:
                raise SlopError(f"{checkout.repo.path} is not in {file.name}")
        for key, value in changes.items():
            if value is None:
                entry.pop(key, None)
            else:
                entry[key] = value
        if checkout.scope == Path(".") and not data.get("core"):
            data.pop("core", None)
        YamlFile.write(file, data)
        return file

    def lockfiles(self) -> dict[str, Path]:
        return {f"composer-{v.name}.lock": self.versionDir(v) / "composer.lock" for v in self.allVersions.values()}

    def syncPaths(self, only: list[str] | None) -> list[str] | None:
        if not only or "all" in only:
            return None
        paths: list[str] = []
        for what in only:
            what = self.ALIASES.get(what, what)
            if what not in self.SYNC_PATHS:
                raise SlopError(f"cannot deploy only {what!r}; use one of: {', '.join(self.SYNC_PATHS)}")
            for path in self.SYNC_PATHS[what]:
                if path not in paths:
                    paths.append(path)
        return paths

    def afterSyncCommands(self, changed: set[str], path: Path) -> list[str]:
        commands = super().afterSyncCommands(changed, path)
        l10n = self.deploy.l10n
        when = l10n.get("when", "auto")
        codeChanged = any(key.startswith("versions/") for key in changed)
        if when == "always" or (when == "auto" and codeChanged):
            command = l10n.get("command") or (
                "sudo -n -u www-data php {path}/scripts/mwscript.php rebuildLocalisationCache --wiki {wiki}"
            )
            commands.append(command.format(path=path, wiki=l10n.get("wiki", "testwiki")))
        return commands
