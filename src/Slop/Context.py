import logging
import os
import shlex
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from Slop.Log.RunLog import RunLog
from Slop.Output.Console import Console
from Slop.Output.ProgressBar import ProgressBar
from Slop.SlopError import SlopError

if TYPE_CHECKING:
    from Slop.Config.Config import Config


class Context:
    def __init__(
        self,
        console: Console,
        runLog: RunLog,
        config: "Config",
        *,
        dryRun: bool = False,
        verbose: bool = False,
        force: bool = False,
        jobs: int = 8,
        interactive: bool | None = None,
    ):
        self.console = console
        self.runLog = runLog
        self.config = config
        self.dryRun = dryRun
        self.verbose = verbose
        self.force = force
        self.jobs = jobs
        self.interactive = (sys.stdin.isatty() and sys.stdout.isatty()) if interactive is None else interactive
        self.project: Any = None
        self.module: str | None = None
        self.scope: set[Path] | None = None
        self.explicit: set[str] = set()
        self.state: dict | None = None
        self.pinned = False
        self.stateDir: Path | None = None

    @property
    def pins(self) -> dict[str, str]:
        if not self.state or not self.pinned:
            return {}
        return {key: entry["base"] for key, entry in (self.state.get("repos") or {}).items() if entry.get("base")}

    def inScope(self, path: Path) -> bool:
        return self.scope is None or path in self.scope

    def display(self, path: Path) -> str:
        bases = ([self.project.root] if self.project and path != self.project.root else []) + [Path.cwd()]
        for base in bases:
            if path.is_relative_to(base):
                return path.relative_to(base).as_posix() or "."
        return str(path)

    def event(self, level: int, message: str, **fields) -> None:
        self.runLog.event(
            level, message, project=self.project.name if self.project else None, module=self.module, **fields
        )

    def log(self, msg: str, **fields) -> None:
        self.event(logging.INFO, msg, **fields)
        self.console.write(f"  {msg}")

    def item(self, verb: str, subject: str, extra: str = "") -> None:
        extra = f" (<comment>{extra}</comment>)" if extra else ""
        msg = f"- {verb} <info>{subject}</info>{extra}"
        self.event(logging.INFO, msg, event="item")
        if self.verbose or not self.console.barShown:
            self.console.write(f"  {msg}")

    def success(self, msg: str) -> None:
        self.event(logging.INFO, msg, event="success")
        self.console.success(msg)

    def skip(self, msg: str) -> None:
        self.event(logging.INFO, msg, event="skip")
        self.console.write(f"  <dim>· {msg}</dim>")

    def warn(self, msg: str) -> None:
        self.event(logging.WARNING, msg, event="warning")
        self.console.warning(msg)

    def progress(self, total: int, verb: str, noun: str) -> ProgressBar:
        return self.console.progress(
            total, verb, noun, onClose=lambda summary: self.event(logging.INFO, summary, event="progress")
        )

    def run(
        self,
        cmd: list[str | Path],
        *,
        cwd: Path | None = None,
        check: bool = True,
        mutates: bool = True,
        env: dict | None = None,
        input: str | None = None,
        timeout: float | None = None,
    ) -> subprocess.CompletedProcess:
        argv = [str(c) for c in cmd]
        skip = self.dryRun and mutates
        self.showCommand(argv, cwd, skip)
        if skip:
            return subprocess.CompletedProcess(argv, 0, "", "")
        started = time.monotonic()
        try:
            proc = subprocess.run(
                argv,
                check=False,
                cwd=cwd,
                capture_output=True,
                text=True,
                env={**os.environ, **env} if env else None,
                input=input,
                timeout=timeout,
                stdin=None if input is not None else subprocess.DEVNULL,
            )
        except FileNotFoundError:
            raise SlopError(f"{argv[0]} not found on PATH") from None
        except subprocess.TimeoutExpired:
            self.logCommand(argv, cwd, -1, started, "timed out")
            raise SlopError(f"command timed out after {timeout}s: {shlex.join(argv)}") from None
        self.logCommand(argv, cwd, proc.returncode, started, proc.stderr or proc.stdout)
        if check and proc.returncode != 0:
            output = (proc.stderr or proc.stdout).strip()
            raise SlopError(f"command failed ({proc.returncode}): {shlex.join(argv)}\n{output}")
        return proc

    def stream(
        self, cmd: list[str | Path], onLine: Callable[[str], None], *, cwd: Path | None = None, env: dict | None = None
    ) -> None:
        argv = [str(c) for c in cmd]
        self.showCommand(argv, cwd, self.dryRun)
        if self.dryRun:
            return
        started = time.monotonic()
        tail: list[str] = []
        with subprocess.Popen(
            argv,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            text=True,
            env={**os.environ, **env} if env else None,
        ) as proc:
            for line in proc.stdout:
                line = line.rstrip()
                tail = ([*tail, line])[-30:]
                if line:
                    onLine(line)
        self.logCommand(argv, cwd, proc.returncode, started, "\n".join(tail))
        if proc.returncode != 0:
            raise SlopError(f"command failed ({proc.returncode}): {shlex.join(argv)}\n" + "\n".join(tail).strip())

    def attached(self, cmd: list[str | Path], *, cwd: Path | None = None, env: dict | None = None) -> int:
        argv = [str(c) for c in cmd]
        started = time.monotonic()
        with self.console.pause():
            code = subprocess.call(argv, cwd=cwd, env={**os.environ, **env} if env else None)
        self.logCommand(argv, cwd, code, started, "")
        return code

    def showCommand(self, argv: list[str], cwd: Path | None, skipped: bool) -> None:
        if self.verbose or skipped:
            where = f" <dim>(in {self.display(cwd)})</dim>" if cwd else ""
            shown = [self.display(Path(a)) if a.startswith("/") else a for a in argv]
            self.console.write(f"  <dim>$</dim> <question>{shlex.join(shown)}</question>{where}")

    def logCommand(self, argv: list[str], cwd: Path | None, returncode: int, started: float, output: str) -> None:
        fields = {
            "event": "command",
            "argv": argv,
            "cwd": str(cwd) if cwd else None,
            "returncode": returncode,
            "duration": round(time.monotonic() - started, 3),
        }
        if returncode != 0:
            fields["output"] = output.strip()[-4000:]
        self.event(logging.DEBUG if returncode == 0 else logging.ERROR, f"$ {shlex.join(argv)}", **fields)
