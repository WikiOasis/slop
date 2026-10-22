import hashlib
import json
import subprocess
from collections.abc import Callable
from pathlib import Path

from Slop.Config.Patch import Patch
from Slop.Config.Repo import Repo
from Slop.Context import Context
from Slop.SlopError import SlopError


class Git:
    BASE_REF = "refs/slop/base"
    DEPLOYED_REF = "refs/slop/deployed"
    AUTHOR_NAME = "slop"
    AUTHOR_EMAIL = "slop@wikioasis.invalid"
    STAMP = "slop.json"
    EMPTY = hashlib.sha256(b"[]").hexdigest()

    def __init__(self, ctx: Context):
        self.ctx = ctx

    @property
    def identity(self) -> list[str]:
        return ["-c", f"user.name={self.AUTHOR_NAME}", "-c", f"user.email={self.AUTHOR_EMAIL}"]

    @property
    def committer(self) -> dict:
        return {"GIT_COMMITTER_NAME": self.AUTHOR_NAME, "GIT_COMMITTER_EMAIL": self.AUTHOR_EMAIL}

    @property
    def author(self) -> dict:
        return {"GIT_AUTHOR_NAME": self.AUTHOR_NAME, "GIT_AUTHOR_EMAIL": self.AUTHOR_EMAIL, **self.committer}

    def sync(self, repo: Repo, dest: Path, key: str) -> bool:
        target = self.ctx.pins.get(key) or repo.commit
        label = target[:10] if target else repo.branch
        if not (dest / ".git").exists():
            self.clone(repo, dest, target)
            return True

        dirty = self.dirty(dest)
        if not self.ctx.force:
            self.assertNoLocalChanges(dest)

        self.git(dest, "remote", "set-url", "origin", repo.url)
        if target:
            self.ensureCommit(dest, target, repo.branch)
            new = self.revParse(dest, target)
        else:
            self.git(dest, "fetch", "--quiet", "--no-tags", "--depth", "1", "origin", repo.branch, readOnly=True)
            new = self.revParse(dest, "FETCH_HEAD")

        stamp = self.readStamp(dest)
        head = self.revParse(dest, "HEAD")
        if not dirty and stamp.get("base") == new and stamp.get("head") == head:
            self.ctx.item("Unchanged", self.ctx.display(dest), label)
            return False

        old = stamp.get("base") or self.revParse(dest, self.BASE_REF, check=False)
        self.ctx.item("Updating", self.ctx.display(dest), f"{label} {(old or '')[:7]}..{new[:7]}" if old else label)
        self.git(dest, "reset", "--quiet", "--hard", new)
        if repo.submodules:
            self.git(dest, "submodule", "--quiet", "update", "--init", "--recursive", "--depth", "1")
        self.git(dest, "update-ref", self.BASE_REF, new)
        self.writeStamp(dest, base=new, head=new, digest=self.EMPTY)
        return True

    def clone(self, repo: Repo, dest: Path, target: str | None) -> None:
        if dest.exists() and any(dest.iterdir()):
            raise SlopError(f"{self.ctx.display(dest)} exists but is not a git checkout")
        self.ctx.item("Cloning", self.ctx.display(dest), target[:10] if target else repo.branch)
        self.ctx.run(
            [
                "git",
                "clone",
                "--quiet",
                "--no-tags",
                "--depth",
                "1",
                "--single-branch",
                "--branch",
                repo.branch,
                repo.url,
                dest,
            ]
        )
        if self.ctx.dryRun:
            return
        if target:
            self.ensureCommit(dest, target, repo.branch)
            self.git(dest, "reset", "--quiet", "--hard", target)
        if repo.submodules:
            self.git(dest, "submodule", "--quiet", "update", "--init", "--recursive", "--depth", "1")
        head = self.revParse(dest, "HEAD")
        self.git(dest, "update-ref", self.BASE_REF, head)
        self.writeStamp(dest, base=head, head=head, digest=self.EMPTY)

    def has(self, dest: Path, rev: str) -> bool:
        return (
            self.git(
                dest, "rev-parse", "--verify", "--quiet", f"{rev}^{{commit}}", check=False, readOnly=True
            ).returncode
            == 0
        )

    def ensureCommit(self, dest: Path, sha: str, branch: str, url: str = "origin") -> None:
        if self.has(dest, sha):
            return
        attempts = [
            ["fetch", "--quiet", "--no-tags", "--depth", "1", url, sha],
            ["fetch", "--quiet", "--no-tags", "--depth", "500", url, branch],
            ["fetch", "--quiet", "--no-tags", "--unshallow", url, branch],
        ]
        for args in attempts:
            self.git(dest, *args, check=False, readOnly=True)
            if self.has(dest, sha):
                return
        raise SlopError(f"commit {sha} is not reachable from {self.ctx.display(dest)}'s remote")

    def resolveRemote(self, dest: Path, rev: str, branch: str) -> str:
        if self.has(dest, rev):
            return self.revParse(dest, rev)
        remote = self.git(dest, "ls-remote", "origin", rev, check=False, readOnly=True).stdout.split()
        if remote:
            self.ensureCommit(dest, remote[0], branch)
            return remote[0]
        self.ensureCommit(dest, rev, branch)
        return self.revParse(dest, rev)

    def dirty(self, dest: Path) -> bool:
        status = self.git(
            dest, "status", "--porcelain", "--untracked-files=no", "--ignore-submodules=all", readOnly=True
        ).stdout.strip()
        return bool(status)

    def assertNoLocalChanges(self, dest: Path) -> None:
        status = self.git(
            dest, "status", "--porcelain", "--untracked-files=no", "--ignore-submodules=all", readOnly=True
        ).stdout.strip()
        if status:
            raise SlopError(
                f"{self.ctx.display(dest)} has uncommitted changes (use --force to discard them):\n{status}"
            )
        if not self.revParse(dest, self.BASE_REF, check=False):
            raise SlopError(
                f"{self.ctx.display(dest)} was not created by slop, so its local commits are unknown "
                "(use --force to sync anyway)"
            )
        foreign = [
            line
            for line in self.git(
                dest, "log", "--format=%h %ce %s", f"{self.BASE_REF}..HEAD", readOnly=True
            ).stdout.splitlines()
            if line.split(" ", 2)[1] != self.AUTHOR_EMAIL
        ]
        if foreign:
            raise SlopError(
                f"{self.ctx.display(dest)} has local commits (use --force to discard them):\n" + "\n".join(foreign)
            )

    def applyPatches(
        self,
        repoDir: Path,
        patches: list[Patch],
        onResolved: Callable[[Patch, str], None] | None = None,
    ) -> bool:
        digest = (
            hashlib.sha256(json.dumps([[p.name, p.digest()] for p in patches]).encode()).hexdigest()
            if patches
            else self.EMPTY
        )
        exists = (repoDir / ".git").exists()
        stamp = self.readStamp(repoDir) if exists else {}
        head = self.revParse(repoDir, "HEAD") if exists else None
        if exists and stamp.get("digest") == digest and stamp.get("head") == head and not self.dirty(repoDir):
            if patches:
                self.ctx.skip(f"{self.ctx.display(repoDir)}: {len(patches)} patch(es) already applied")
            return False
        if self.ctx.dryRun:
            for patch in patches:
                self.ctx.item("Would apply", patch.name, f"{patch.source} → {self.ctx.display(repoDir)}")
            return True

        base = stamp.get("base") or self.revParse(repoDir, self.BASE_REF)
        if head != base or self.dirty(repoDir):
            self.git(repoDir, "reset", "--quiet", "--hard", base)
        for patch in patches:
            self.applyOne(repoDir, patch, onResolved)
        self.writeStamp(repoDir, base=base, head=self.revParse(repoDir, "HEAD"), digest=digest)
        return True

    def applyOne(self, repoDir: Path, patch: Patch, onResolved: Callable[[Patch, str], None] | None) -> None:
        from Slop.Git.ConflictResolver import ConflictResolver

        where = self.ctx.display(repoDir)
        self.ctx.item("Applying", patch.name, f"{patch.source} → {where}")
        before = self.revParse(repoDir, "HEAD")

        if patch.kind == "file" or patch.resolved:
            outcome = self.applyFile(repoDir, patch)
        else:
            outcome = self.cherryPick(repoDir, patch)
        if outcome == "applied":
            return
        if outcome == "already":
            self.ctx.skip(f"{patch.name} is already in {where}")
            return

        mode, files = outcome
        resolver = ConflictResolver(self.ctx, self)
        result = resolver.resolve(repoDir, patch, mode, files, before)
        if result == "skip":
            self.ctx.warn(f"skipped {patch.name} for this build; it is still in the config")
            return
        if onResolved is not None:
            diff = self.git(repoDir, "diff", "--binary", "--full-index", before, "HEAD", readOnly=True).stdout
            if self.ctx.console.confirm(
                f"Save the resolved {patch.name} to the config so later builds reuse it?", True
            ):
                onResolved(patch, diff)

    def applyFile(self, repoDir: Path, patch: Patch):
        apply = ["apply", "--whitespace=nowarn"]
        if self.git(repoDir, *apply, "--check", patch.file, check=False, readOnly=True).returncode == 0:
            self.git(repoDir, *apply, "--index", patch.file)
            self.commit(repoDir, self.message(patch))
            return "applied"
        if self.git(repoDir, *apply, "--reverse", "--check", patch.file, check=False, readOnly=True).returncode == 0:
            return "already"
        three = self.git(repoDir, *apply, "--3way", "--index", patch.file, check=False)
        conflicts = self.conflicts(repoDir)
        if three.returncode == 0 and not conflicts:
            self.commit(repoDir, self.message(patch))
            return "applied"
        if conflicts:
            return ("apply", conflicts)
        self.git(repoDir, "reset", "--quiet", "--hard", "HEAD")
        self.git(repoDir, *apply, "--reject", patch.file, check=False)
        rejects = self.rejects(repoDir)
        return ("reject", rejects or [patch.file.name])

    def cherryPick(self, repoDir: Path, patch: Patch):
        if patch.kind == "gerrit":
            self.git(repoDir, "fetch", "--quiet", "--no-tags", "--depth", "2", patch.url, patch.ref)
            picks = [self.revParse(repoDir, "FETCH_HEAD")]
            if patch.commit and picks[0] != patch.commit:
                self.ctx.warn(f"{patch.name}: {patch.ref} is {picks[0][:10]}, not the recorded {patch.commit[:10]}")
        elif patch.kind == "github":
            self.fetchCommit(repoDir, patch.url, patch.commit, patch.commits + 1, patch.ref)
            picks = [patch.commit] if patch.commits <= 1 else [f"{patch.commit}~{patch.commits}..{patch.commit}"]
        else:
            self.fetchCommit(repoDir, patch.url or "origin", patch.commit, 2, None)
            picks = [patch.commit]

        proc = self.git(
            repoDir, *self.identity, "cherry-pick", "--allow-empty", *picks, check=False, env=self.committer
        )
        if proc.returncode == 0:
            return "applied"
        conflicts = self.conflicts(repoDir)
        if conflicts:
            return ("cherry-pick", conflicts)
        if "empty" in (proc.stderr + proc.stdout):
            self.git(repoDir, "cherry-pick", "--skip", check=False, env=self.committer)
            return "already"
        self.git(repoDir, "cherry-pick", "--abort", check=False)
        raise SlopError(f"cannot cherry-pick {patch.name} onto {self.ctx.display(repoDir)}:\n{proc.stderr.strip()}")

    def fetchCommit(self, repoDir: Path, url: str, sha: str, depth: int, ref: str | None) -> None:
        if self.has(repoDir, sha) and self.has(repoDir, f"{sha}~1"):
            return
        self.git(repoDir, "fetch", "--quiet", "--no-tags", "--depth", str(depth), url, sha, check=False)
        if not self.has(repoDir, sha) and ref:
            self.git(repoDir, "fetch", "--quiet", "--no-tags", "--depth", str(depth + 50), url, ref, check=False)
        if not self.has(repoDir, sha):
            raise SlopError(f"cannot fetch {sha[:10]} from {url}")

    def continueResolution(self, repoDir: Path, mode: str, patch: Patch) -> bool:
        self.git(repoDir, "add", "--all", "--", ".")
        if mode == "cherry-pick":
            proc = self.git(
                repoDir,
                *self.identity,
                "-c",
                "core.editor=true",
                "cherry-pick",
                "--continue",
                check=False,
                env=self.committer,
            )
            if proc.returncode != 0 and not self.conflicts(repoDir) and self.pickInProgress(repoDir):
                self.git(repoDir, "cherry-pick", "--skip", check=False, env=self.committer)
            return not self.pickInProgress(repoDir)
        for rej in self.rejects(repoDir):
            (repoDir / rej).unlink(missing_ok=True)
        self.git(repoDir, "add", "--all", "--", ".")
        self.commit(repoDir, self.message(patch) + "\n\nConflicts resolved by hand during slop build.")
        return True

    def abortResolution(self, repoDir: Path, mode: str, before: str) -> None:
        if mode == "cherry-pick":
            self.git(repoDir, "cherry-pick", "--abort", check=False)
        for rej in self.rejects(repoDir):
            (repoDir / rej).unlink(missing_ok=True)
        self.git(repoDir, "reset", "--quiet", "--hard", before)

    def pickInProgress(self, repoDir: Path) -> bool:
        gitDir = Path(self.git(repoDir, "rev-parse", "--absolute-git-dir", readOnly=True).stdout.strip())
        return (gitDir / "CHERRY_PICK_HEAD").exists() or (gitDir / "sequencer").exists()

    def conflicts(self, repoDir: Path) -> list[str]:
        out = self.git(repoDir, "diff", "--name-only", "--diff-filter=U", check=False, readOnly=True).stdout
        return [line for line in out.splitlines() if line]

    def rejects(self, repoDir: Path) -> list[str]:
        out = self.git(repoDir, "ls-files", "--others", "--exclude-standard", check=False, readOnly=True).stdout
        return [line for line in out.splitlines() if line.endswith(".rej")]

    def markers(self, repoDir: Path) -> list[str]:
        proc = self.git(repoDir, "diff", "HEAD", "--check", check=False, readOnly=True)
        return [line for line in proc.stdout.splitlines() if "conflict marker" in line]

    def message(self, patch: Patch) -> str:
        text = patch.description or patch.name
        trailer = f"\n\nPatch: {patch.name} ({patch.source})"
        if patch.task:
            trailer += f"\nTask: {patch.task}"
        return text + trailer

    def commit(self, repoDir: Path, message: str, *paths: str) -> None:
        if paths:
            self.git(repoDir, "add", "--", *paths)
        self.git(
            repoDir,
            *self.identity,
            "commit",
            "--quiet",
            "--no-verify",
            "--allow-empty",
            "-m",
            f"slop: {message}",
            env=self.author,
        )

    def revParse(self, repoDir: Path, rev: str, check: bool = True) -> str | None:
        if self.ctx.dryRun and not (repoDir / ".git").exists():
            return None
        proc = self.git(repoDir, "rev-parse", "--verify", "--quiet", rev, check=False, readOnly=True)
        if proc.returncode != 0:
            if check:
                raise SlopError(f"{self.ctx.display(repoDir)}: cannot resolve {rev}")
            return None
        return proc.stdout.strip()

    def stampFile(self, repoDir: Path) -> Path:
        return repoDir / ".git" / self.STAMP

    def readStamp(self, repoDir: Path) -> dict:
        try:
            return json.loads(self.stampFile(repoDir).read_text())
        except (OSError, ValueError):
            return {}

    def writeStamp(self, repoDir: Path, **fields) -> None:
        if self.ctx.dryRun:
            return
        stamp = {**self.readStamp(repoDir), **fields}
        self.stampFile(repoDir).write_text(json.dumps(stamp))

    def touchHead(self, repoDir: Path) -> None:
        if self.ctx.dryRun:
            return
        self.writeStamp(repoDir, head=self.revParse(repoDir, "HEAD"))

    def describe(self, repoDir: Path) -> dict:
        stamp = self.readStamp(repoDir)
        return {
            "base": stamp.get("base") or self.revParse(repoDir, self.BASE_REF, check=False),
            "head": self.revParse(repoDir, "HEAD", check=False),
        }

    def count(self, repoDir: Path, old: str, new: str) -> int | None:
        if not (self.has(repoDir, old) and self.has(repoDir, new)):
            return None
        proc = self.git(repoDir, "rev-list", "--count", f"{old}..{new}", check=False, readOnly=True)
        return int(proc.stdout.strip()) if proc.returncode == 0 and proc.stdout.strip().isdigit() else None

    def subjects(self, repoDir: Path, old: str, new: str, limit: int = 20) -> list[str]:
        if not (self.has(repoDir, old) and self.has(repoDir, new)):
            return []
        proc = self.git(repoDir, "log", f"-n{limit}", "--format=%h %s", f"{old}..{new}", check=False, readOnly=True)
        return proc.stdout.splitlines()

    def keep(self, repoDir: Path, name: str, sha: str) -> None:
        if not self.ctx.dryRun and sha and self.has(repoDir, sha):
            self.git(repoDir, "update-ref", f"{self.DEPLOYED_REF}/{name}", sha, check=False)

    def git(
        self, repoDir: Path, *args: str | Path, check: bool = True, readOnly: bool = False, env: dict | None = None
    ) -> subprocess.CompletedProcess:
        return self.ctx.run(["git", "-C", repoDir, *args], check=check, mutates=not readOnly, env=env)
