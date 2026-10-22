import os
import shutil
import subprocess
import tarfile
import tempfile
from pathlib import Path

from Slop.Git.Git import Git
from Slop.Log.Invoker import Invoker
from Slop.SlopError import SlopError


class ConfigRepo:
    def __init__(self, path: Path, remote: str = "origin", branch: str = "main"):
        self.path = path
        self.remote = remote
        self.branch = branch

    @property
    def isRepo(self) -> bool:
        return (self.path / ".git").exists()

    def git(self, *args: str, check: bool = True, env: dict | None = None) -> subprocess.CompletedProcess:
        proc = subprocess.run(
            ["git", "-C", str(self.path), *args],
            check=False,
            capture_output=True,
            text=True,
            env={**os.environ, **(env or {})},
        )
        if check and proc.returncode != 0:
            raise SlopError(f"config repo: git {' '.join(args)} failed:\n{(proc.stderr or proc.stdout).strip()}")
        return proc

    def head(self) -> str | None:
        if not self.isRepo:
            return None
        proc = self.git("rev-parse", "--verify", "--quiet", "HEAD", check=False)
        return proc.stdout.strip() or None

    def hasRemote(self) -> bool:
        return self.isRepo and self.remote in self.git("remote").stdout.split()

    def currentBranch(self) -> str | None:
        name = self.git("symbolic-ref", "--quiet", "--short", "HEAD", check=False).stdout.strip()
        return name or None

    def changes(self, paths: list[str] | None = None) -> list[str]:
        if not self.isRepo:
            return []
        args = ["status", "--porcelain", "--untracked-files=all"]
        if paths:
            args += ["--", *paths]
        return [line for line in self.git(*args).stdout.splitlines() if line.strip()]

    def ensureBranch(self) -> None:
        if self.currentBranch() == self.branch:
            return
        local = self.git("rev-parse", "--verify", "--quiet", f"refs/heads/{self.branch}", check=False).returncode == 0
        if local:
            self.git("checkout", "--quiet", self.branch)
        else:
            self.git("checkout", "--quiet", "-B", self.branch)
        if self.hasRemote():
            self.git("branch", "--quiet", f"--set-upstream-to={self.remote}/{self.branch}", self.branch, check=False)

    def pull(self) -> tuple[str | None, str | None]:
        if not self.isRepo or not self.hasRemote():
            return None, None
        before = self.head()
        self.ensureBranch()
        self.git("fetch", "--quiet", self.remote, self.branch)
        remoteHead = self.git("rev-parse", "FETCH_HEAD").stdout.strip()
        if before and self.git("merge-base", "--is-ancestor", remoteHead, before, check=False).returncode == 0:
            return before, before
        proc = self.git("rebase", "--autostash", "--quiet", "FETCH_HEAD", check=False)
        if proc.returncode != 0:
            self.git("rebase", "--abort", check=False)
            raise SlopError(
                "the config repo has diverged from its remote and could not be rebased automatically; "
                f"resolve it by hand in {self.path}:\n{(proc.stderr or proc.stdout).strip()}"
            )
        return before, self.head()

    def commit(self, message: str, paths: list[str]) -> str | None:
        if not self.isRepo:
            return None
        existing = [p for p in paths if (self.path / p).exists() or self.tracked(p)]
        if not existing:
            return None
        self.git("add", "--all", "--", *existing)
        if self.git("diff", "--cached", "--quiet", check=False).returncode == 0:
            return None
        user = Invoker.user()
        env = {
            "GIT_AUTHOR_NAME": user,
            "GIT_AUTHOR_EMAIL": f"{user}@{Invoker.host()}",
            "GIT_COMMITTER_NAME": Git.AUTHOR_NAME,
            "GIT_COMMITTER_EMAIL": Git.AUTHOR_EMAIL,
        }
        self.git("commit", "--quiet", "--no-verify", "-m", message, env=env)
        return self.head()

    def tracked(self, path: str) -> bool:
        return bool(self.git("ls-files", "--", path).stdout.strip())

    def push(self, attempts: int = 3) -> None:
        if not self.hasRemote():
            return
        error = ""
        for _ in range(attempts):
            proc = self.git("push", "--quiet", self.remote, f"HEAD:refs/heads/{self.branch}", check=False)
            if proc.returncode == 0:
                return
            error = (proc.stderr or proc.stdout).strip()
            self.git("fetch", "--quiet", self.remote, self.branch, check=False)
            rebased = self.git("rebase", "--autostash", "--quiet", "FETCH_HEAD", check=False)
            if rebased.returncode != 0:
                self.git("rebase", "--abort", check=False)
                break
        raise SlopError(f"could not push the config repo to {self.remote}/{self.branch}:\n{error}")

    def ahead(self) -> int:
        if not self.hasRemote():
            return 0
        proc = self.git("rev-list", "--count", f"{self.remote}/{self.branch}..HEAD", check=False)
        return int(proc.stdout.strip() or 0) if proc.returncode == 0 else 0

    def resolve(self, rev: str) -> str:
        proc = self.git("rev-parse", "--verify", "--quiet", f"{rev}^{{commit}}", check=False)
        if proc.returncode != 0:
            raise SlopError(f"no config revision {rev!r}")
        return proc.stdout.strip()

    def show(self, rev: str, path: str) -> str | None:
        proc = self.git("show", f"{rev}:{path}", check=False)
        return proc.stdout if proc.returncode == 0 else None

    def history(self, path: str, limit: int = 20) -> list[dict]:
        if not self.isRepo:
            return []
        proc = self.git("log", f"-n{limit}", "--format=%H%x1f%an%x1f%aI%x1f%s", "--", path, check=False)
        entries = []
        for line in proc.stdout.splitlines():
            sha, author, when, subject = ([*line.split("\x1f"), "", "", "", ""])[:4]
            entries.append({"commit": sha, "author": author, "date": when, "subject": subject})
        return entries

    def export(self, rev: str) -> Path:
        target = Path(tempfile.mkdtemp(prefix="slop-config-"))
        archive = subprocess.run(
            ["git", "-C", str(self.path), "archive", "--format=tar", rev], check=False, capture_output=True
        )
        if archive.returncode != 0:
            shutil.rmtree(target, ignore_errors=True)
            raise SlopError(f"cannot export config revision {rev}: {archive.stderr.decode().strip()}")
        tmp = target / ".export.tar"
        tmp.write_bytes(archive.stdout)
        with tarfile.open(tmp) as tar:
            tar.extractall(target, filter="data")
        tmp.unlink()
        for name in ("slop.local.yaml",):
            local = self.path / name
            if local.is_file():
                shutil.copy2(local, target / name)
        return target

    def restore(self, rev: str, paths: list[str]) -> None:
        self.git("restore", f"--source={rev}", "--staged", "--worktree", "--", *paths)
