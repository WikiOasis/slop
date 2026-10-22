import fcntl
import json
import os
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

from Slop.Config.ConfigRepo import ConfigRepo
from Slop.Config.YamlFile import YamlFile
from Slop.Log.Invoker import Invoker
from Slop.SlopError import SlopError


class Lock:
    ALL = "_all"
    DIR = "locks"

    def __init__(self, lockDir: Path, repo: ConfigRepo, push: bool = True):
        self.lockDir = lockDir
        self.repo = repo
        self.push = push

    @contextmanager
    def acquire(self, project: str, runId: str, message: str | None) -> Iterator[None]:
        self.lockDir.mkdir(parents=True, exist_ok=True)
        handle = (self.lockDir / f"{project}.lock").open("a+")
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            handle.seek(0)
            holder = self.parseJson(handle.read())
            handle.close()
            raise SlopError(f"{project} is being deployed right now by {self.describe(holder)}") from None
        handle.seek(0)
        handle.truncate()
        handle.write(json.dumps({**self.record(message), "run": runId, "pid": os.getpid()}))
        handle.flush()
        try:
            yield
        finally:
            handle.seek(0)
            handle.truncate()
            fcntl.flock(handle, fcntl.LOCK_UN)
            handle.close()

    def relative(self, name: str) -> str:
        return f"{self.DIR}/{name}.yaml"

    def file(self, name: str) -> Path:
        return self.repo.path / self.relative(name)

    def sync(self) -> None:
        if not self.repo.hasRemote():
            return
        try:
            self.repo.pull()
        except SlopError as e:
            raise SlopError(f"cannot pull the config repo, so its locks cannot be checked:\n{e}") from None

    def held(self, name: str) -> dict | None:
        file = self.file(name)
        if not file.is_file():
            return None
        holder = YamlFile.read(file)
        expires = holder.get("expires")
        if expires and datetime.fromisoformat(str(expires)) < datetime.now(UTC):
            return None
        return holder

    def refuse(self, name: str, holder: dict | None, project: str) -> None:
        if not holder or holder.get("user") == Invoker.user():
            return
        expires = holder.get("expires")
        if expires and datetime.fromisoformat(str(expires)) < datetime.now(UTC):
            return
        what = "all deploys are" if name == self.ALL else f"{name} is"
        raise SlopError(f"{what} locked by {self.describe(holder)} (see `slop locks`)")

    def checkHeld(self, *projects: str) -> None:
        for project in projects:
            for name in (self.ALL, project):
                self.refuse(name, self.held(name), project)
        if not projects:
            self.refuse(self.ALL, self.held(self.ALL), self.ALL)

    def checkRemote(self, *projects: str) -> None:
        self.checkHeld(*projects)
        if not self.repo.hasRemote():
            return
        proc = self.repo.git("fetch", "--quiet", self.repo.remote, self.repo.branch, check=False)
        if proc.returncode != 0:
            raise SlopError(
                "cannot reach the config repo to re-check locks, so nothing was rolled out:\n"
                + (proc.stderr or proc.stdout).strip()
            )
        for project in projects or (self.ALL,):
            for name in (self.ALL, project):
                text = self.repo.show("FETCH_HEAD", self.relative(name))
                if text:
                    self.refuse(name, YamlFile.parse(text, self.relative(name)), project)

    def hold(self, name: str, reason: str, seconds: float | None) -> dict:
        self.sync()
        current = self.held(name)
        if current and current.get("user") != Invoker.user():
            raise SlopError(f"{name} is already locked by {self.describe(current)}")
        record = self.record(reason)
        if seconds:
            record["expires"] = (datetime.now(UTC) + timedelta(seconds=seconds)).isoformat(timespec="seconds")
        before = self.repo.head()
        YamlFile.write(self.file(name), record)
        self.publish(name, f"Lock {self.label(name)}: {reason}", before)
        return record

    def release(self, name: str, force: bool) -> dict | None:
        self.sync()
        file = self.file(name)
        current = YamlFile.read(file) if file.is_file() else None
        if current is None:
            return None
        if self.held(name) and current.get("user") != Invoker.user() and not force:
            raise SlopError(f"{name} is locked by {self.describe(current)}; pass --force to break the lock")
        before = self.repo.head()
        file.unlink()
        self.publish(name, f"Unlock {self.label(name)}", before)
        return current

    def publish(self, name: str, message: str, before: str | None) -> None:
        if not self.repo.isRepo:
            return
        self.repo.commit(message, [self.relative(name)])
        if not self.push or not self.repo.hasRemote():
            return
        try:
            self.repo.push()
        except SlopError as e:
            if before:
                self.repo.git("reset", "--quiet", "--mixed", before)
                if self.repo.git("cat-file", "-e", f"{before}:{self.relative(name)}", check=False).returncode == 0:
                    self.repo.git("checkout", before, "--", self.relative(name))
                else:
                    self.file(name).unlink(missing_ok=True)
            raise SlopError(f"could not push the lock change, so it was undone:\n{e}") from None

    def all(self) -> dict[str, dict]:
        found = {}
        directory = self.repo.path / self.DIR
        for file in sorted(directory.glob("*.yaml")) if directory.is_dir() else []:
            holder = self.held(file.stem)
            if holder:
                found[file.stem] = holder
        for file in sorted(self.lockDir.glob("*.lock")) if self.lockDir.is_dir() else []:
            with file.open("a+") as handle:
                try:
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    fcntl.flock(handle, fcntl.LOCK_UN)
                except BlockingIOError:
                    handle.seek(0)
                    found[f"{file.stem} (deploying now)"] = self.parseJson(handle.read())
        return found

    @classmethod
    def label(cls, name: str) -> str:
        return "all deploys" if name == cls.ALL else name

    @staticmethod
    def record(reason: str | None) -> dict:
        return {
            "user": Invoker.user(),
            "host": Invoker.host(),
            "reason": reason,
            "since": datetime.now(UTC).isoformat(timespec="seconds"),
        }

    @staticmethod
    def parseJson(text: str) -> dict:
        try:
            data = json.loads(text)
            return data if isinstance(data, dict) else {}
        except ValueError:
            return {}

    @staticmethod
    def describe(holder: dict) -> str:
        since = str(holder.get("since") or "?")[:16].replace("T", " ")
        text = f"{holder.get('user', '?')}@{holder.get('host', '?')} since {since} UTC"
        if holder.get("reason"):
            text += f" ({holder['reason']})"
        if holder.get("expires"):
            text += f", until {str(holder['expires'])[:16].replace('T', ' ')} UTC"
        if holder.get("run"):
            text += f", run {holder['run']}"
        return text
