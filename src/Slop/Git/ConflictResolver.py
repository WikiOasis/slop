import os
import shutil
from pathlib import Path
from typing import TYPE_CHECKING

from Slop.Config.Patch import Patch
from Slop.Context import Context
from Slop.SlopError import SlopError

if TYPE_CHECKING:
    from Slop.Git.Git import Git


class ConflictResolver:
    def __init__(self, ctx: Context, git: "Git"):
        self.ctx = ctx
        self.git = git

    def resolve(self, repoDir: Path, patch: Patch, mode: str, files: list[str], before: str) -> str:
        where = self.ctx.display(repoDir)
        if not self.ctx.interactive:
            self.git.abortResolution(repoDir, mode, before)
            raise SlopError(
                f"{patch.name} ({patch.source}) conflicts with {where}:\n  "
                + "\n  ".join(files)
                + "\nRun the build again from a terminal to resolve it interactively, "
                "or update the patch in the config."
            )

        options = {
            "s": "open a shell in the checkout to fix it",
            "m": "run git mergetool",
            "d": "show the conflicting diff",
            "k": "skip this patch for this build",
            "a": "abort the build",
        }
        if mode == "reject":
            options.pop("m")
        while True:
            self.ctx.console.write()
            self.ctx.console.write(
                f"<error>✖</error> <bold>{patch.name}</bold> <dim>({patch.source})</dim> does not apply cleanly to "
                f"<info>{where}</info>"
            )
            if patch.description:
                self.ctx.console.write(f"  <dim>{patch.description}</dim>")
            for file in files:
                self.ctx.console.write(f"  <comment>{file}</comment>")
            if mode == "reject":
                self.ctx.console.write(
                    "  <dim>The hunks that did not apply are in the .rej files next to each file.</dim>"
                )

            choice = self.ctx.console.ask("How do you want to continue?", options, "s")
            self.ctx.event(20, f"conflict on {patch.name}: chose {options[choice]}", event="conflict.choice")
            if choice == "a":
                self.git.abortResolution(repoDir, mode, before)
                raise SlopError(f"aborted while resolving {patch.name} in {where}")
            if choice == "k":
                self.git.abortResolution(repoDir, mode, before)
                return "skip"
            if choice == "d":
                self.ctx.attached(["git", "-C", repoDir, "--no-pager", "diff", "--color=auto"])
                continue
            if choice == "m":
                self.ctx.attached(["git", "-C", repoDir, "mergetool"])
            if choice == "s":
                self.shell(repoDir, patch)

            remaining = self.remaining(repoDir, mode)
            if remaining:
                files = remaining
                self.ctx.console.warning("these still have conflicts:\n" + "\n".join(remaining))
                continue
            if self.git.continueResolution(repoDir, mode, patch):
                self.ctx.success(f"resolved {patch.name} in {where}")
                return "resolved"
            files = self.git.conflicts(repoDir) or files

    def remaining(self, repoDir: Path, mode: str) -> list[str]:
        if mode == "reject":
            return self.git.rejects(repoDir) + self.git.markers(repoDir)
        return self.git.conflicts(repoDir) + self.git.markers(repoDir)

    def shell(self, repoDir: Path, patch: Patch) -> None:
        shell = os.environ.get("SHELL") or shutil.which("bash") or "/bin/sh"
        self.ctx.console.write()
        self.ctx.console.write(
            f"<comment>Fix the conflicts for {patch.name}, `git add` the files (delete any .rej files), "
            "then `exit` to carry on.</comment>"
        )
        self.ctx.attached(
            [shell],
            cwd=repoDir,
            env={"SLOP_RESOLVING": patch.name, "PS1": f"(slop: resolving {patch.name}) \\w $ "},
        )
