import logging
import os
import secrets
import socket
import time
from datetime import datetime
from pathlib import Path

from Slop import VERSION
from Slop.Log.Invoker import Invoker
from Slop.Log.JsonFormatter import JsonFormatter
from Slop.Output.Duration import Duration


class RunLog:
    def __init__(self, logDir: Path | None, command: str, argv: list[str], message: str | None, dryRun: bool):
        self.runId = datetime.now().strftime("%Y%m%d-%H%M%S-") + secrets.token_hex(2)
        self.command = command
        self.message = message
        self.started = time.monotonic()
        self.file: Path | None = logDir / f"{self.runId}.log" if logDir else None
        self.error: str | None = None
        self.finished = False

        self.logger = logging.getLogger(f"slop.run.{self.runId}")
        self.logger.setLevel(logging.DEBUG)
        self.logger.propagate = False
        handler: logging.Handler = logging.NullHandler()
        if logDir is not None:
            try:
                logDir.mkdir(parents=True, exist_ok=True)
                handler = logging.FileHandler(self.file, encoding="utf-8")
            except OSError as e:
                self.error = f"cannot write run log to {logDir}: {e.strerror or e}"
                self.file = None
        handler.setFormatter(
            JsonFormatter(
                {
                    "run": self.runId,
                    "command": command,
                    "user": Invoker.user(),
                    "host": socket.gethostname(),
                    "dry_run": dryRun,
                }
            )
        )
        self.logger.addHandler(handler)

        self.event(
            logging.INFO,
            f"Started slop {command}" + (f": {message}" if message else ""),
            event="run.start",
            argv=argv,
            slop_version=VERSION,
            run_message=message,
            cwd=os.getcwd(),
        )

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self.started

    def event(self, level: int, message: str, **fields) -> None:
        self.logger.log(level, message, extra={"slop": fields})

    def finish(self, ok: bool, error: str | None = None) -> float:
        duration = self.elapsed
        if self.finished:
            return duration
        self.finished = True
        status = "Finished" if ok else "Failed"
        self.event(
            logging.INFO if ok else logging.ERROR,
            f"{status} slop {self.command} (duration: {Duration.format(duration)})",
            event="run.finish",
            ok=ok,
            duration=round(duration, 3),
            error=error,
        )
        for handler in list(self.logger.handlers):
            handler.close()
            self.logger.removeHandler(handler)
        return duration
