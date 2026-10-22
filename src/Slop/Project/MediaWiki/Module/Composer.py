import hashlib
import json
import re
import shutil
from pathlib import Path

from Slop.Git.Git import Git
from Slop.Module.Module import Module
from Slop.Output.ProgressBar import ProgressBar
from Slop.Project.MediaWiki.Version import Version
from Slop.SlopError import SlopError


class Composer(Module):
    name = "composer"
    title = "Installing PHP dependencies"
    description = (
        "Merge every extension/skin composer.json via composer.local.json, then composer install from the "
        "deployed lock file or composer update when inputs changed."
    )

    STAMP = Path("vendor") / ".slop-composer"
    SUMMARY = re.compile(r"^(Package operations:|Nothing to install, update or remove)")
    STEP = re.compile(r"^\s*- (Installing|Updating|Downgrading|Removing|Upgrading) ")

    def run(self) -> None:
        if not self.ctx.dryRun and shutil.which("composer") is None:
            raise SlopError("composer not found on PATH (use --skip composer to build without it)")
        with self.ctx.progress(len(self.project.versions), "Finished composer for", "version") as bar:
            for version in self.project.versions:
                with bar.task(version.name):
                    self.install(bar, version)

    def install(self, bar: ProgressBar, version: Version) -> None:
        versionDir = self.project.versionDir(version)
        if not self.ctx.dryRun and not (versionDir / "composer.json").is_file():
            raise SlopError(f"{self.ctx.display(versionDir)} has no composer.json (is core synced?)")
        includes = self.includes(version)
        self.writeLocalJson(versionDir, includes)
        self.allowPlugins(versionDir, includes)

        inputs = self.digest(versionDir, includes)
        stored = self.storedLock(version)
        lockFile = versionDir / "composer.lock"
        stamp = versionDir / self.STAMP
        current = self.readStamp(stamp)
        explicit = self.name in self.ctx.explicit

        if stored is not None and (stored.get("inputs") == inputs or self.ctx.pinned):
            lockDigest = self.fileDigest(stored["file"])
            if not explicit and current.get("lock") == lockDigest and current.get("inputs") == inputs:
                self.ctx.skip(f"{version.name}: vendor/ matches the deployed composer.lock")
                return
            self.ctx.item("Installing", version.name, "from the deployed composer.lock")
            if not self.ctx.dryRun:
                shutil.copy2(stored["file"], lockFile)
            self.composer(bar, version, versionDir, "install")
            self.writeStamp(stamp, inputs, lockFile)
            return

        if not explicit and current.get("inputs") == inputs and lockFile.is_file():
            self.ctx.skip(f"{version.name}: composer.json files unchanged, skipping composer update")
            return
        self.composer(bar, version, versionDir, "update")
        self.writeStamp(stamp, inputs, lockFile)

    def composer(self, bar: ProgressBar, version: Version, versionDir: Path, action: str) -> None:
        def onLine(line: str) -> None:
            if self.SUMMARY.match(line):
                self.ctx.log(f"<comment>{version.name}</comment> {line}")
            elif self.STEP.match(line):
                bar.status(line.strip().removeprefix("- "))
                if self.ctx.verbose or not self.ctx.console.barShown:
                    self.ctx.log(f"<dim>{line.strip()}</dim>")

        self.ctx.stream(
            ["composer", action, "--no-dev", "--no-interaction", "--no-progress", "--no-ansi", "--optimize-autoloader"],
            onLine,
            cwd=versionDir,
            env={"COMPOSER_NO_AUDIT": "1"},
        )
        bar.status("")

    def storedLock(self, version: Version) -> dict | None:
        if self.ctx.stateDir is None:
            return None
        file = self.ctx.stateDir / f"composer-{version.name}.lock"
        if not file.is_file():
            return None
        inputs = None
        meta = (self.ctx.state or {}).get("composer", {}).get(version.name, {})
        if isinstance(meta, dict):
            inputs = meta.get("inputs")
        return {"file": file, "inputs": inputs}

    def readStamp(self, stamp: Path) -> dict:
        try:
            data = json.loads(stamp.read_text())
            return data if isinstance(data, dict) else {"inputs": stamp.read_text()}
        except (OSError, ValueError):
            return {"inputs": stamp.read_text()} if stamp.is_file() else {}

    def writeStamp(self, stamp: Path, inputs: str, lockFile: Path) -> None:
        if self.ctx.dryRun:
            return
        stamp.parent.mkdir(parents=True, exist_ok=True)
        stamp.write_text(json.dumps({"inputs": inputs, "lock": self.fileDigest(lockFile)}))

    @staticmethod
    def fileDigest(file: Path) -> str | None:
        return hashlib.sha256(file.read_bytes()).hexdigest() if file.is_file() else None

    def includes(self, version: Version) -> list[str]:
        versionDir = self.project.versionDir(version)
        return [
            (repo.path / "composer.json").as_posix()
            for repo in version.repos
            if (versionDir / repo.path / "composer.json").is_file()
        ]

    def digest(self, versionDir: Path, includes: list[str]) -> str:
        h = hashlib.sha256()
        for rel in ["composer.json", "composer.local.json", *includes]:
            file = versionDir / rel
            h.update(rel.encode() + b"\0")
            h.update(file.read_bytes() if file.is_file() else b"")
        return h.hexdigest()

    def writeLocalJson(self, versionDir: Path, includes: list[str]) -> None:
        content = json.dumps({"extra": {"merge-plugin": {"include": includes, "merge-dev": False}}}, indent="\t") + "\n"
        file = versionDir / "composer.local.json"
        if file.is_file() and file.read_text() == content:
            return
        self.ctx.item("Writing", self.ctx.display(file), f"merging {len(includes)} composer.json files")
        if not self.ctx.dryRun:
            file.write_text(content)

    def allowPlugins(self, versionDir: Path, includes: list[str]) -> None:
        coreJson = versionDir / "composer.json"
        current = self.readJson(coreJson).get("config", {}).get("allow-plugins", {}) if coreJson.is_file() else {}
        if current is True:
            return

        missing: dict[str, bool] = {}
        for include in includes:
            allowed = self.readJson(versionDir / include).get("config", {}).get("allow-plugins")
            if isinstance(allowed, dict):
                for plugin, allow in allowed.items():
                    if plugin not in current:
                        missing[plugin] = missing.get(plugin, False) or bool(allow)
        for plugin, allow in self.project.composerAllowPlugins.items():
            if current.get(plugin) != allow:
                missing[plugin] = allow
        if not missing:
            return

        for plugin, allow in sorted(missing.items()):
            self.ctx.item("Allowing" if allow else "Denying", plugin, "composer plugin")
            self.ctx.run(
                ["composer", "config", "--no-plugins", f"allow-plugins.{plugin}", "true" if allow else "false"],
                cwd=versionDir,
            )
        git = Git(self.ctx)
        git.commit(
            versionDir, "merge composer allow-plugins from extensions\n\n" + "\n".join(sorted(missing)), "composer.json"
        )
        git.touchHead(versionDir)

    def readJson(self, file: Path) -> dict:
        try:
            return json.loads(file.read_text())
        except (OSError, json.JSONDecodeError) as e:
            raise SlopError(f"cannot read {self.ctx.display(file)}: {e}") from None
