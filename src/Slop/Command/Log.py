import argparse
import contextlib
import json
import logging
import time
from datetime import datetime
from pathlib import Path

from Slop.Command.Command import Command
from Slop.Command.Reader import Reader
from Slop.Output.Console import Console
from Slop.Output.Duration import Duration
from Slop.SlopError import SlopError


class Log(Command):
    name = "log"
    help = "show run logs: list runs, show one, or follow the latest"

    LEVEL_STYLE = {"DEBUG": "dim", "INFO": "info", "WARNING": "comment", "ERROR": "error", "CRITICAL": "error"}

    def configure(self, parser: argparse.ArgumentParser) -> None:
        parser.formatter_class = argparse.RawDescriptionHelpFormatter
        parser.description = self.help + (
            "\n\nexamples:\n  slop log                 the latest run\n  slop log -l              recent runs\n"
            "  slop log 20261005-2310   a run (any unique prefix of its id)\n"
            "  slop log -f              follow the latest run as it happens\n"
            "  slop log --level debug   include every command that ran"
        )
        parser.add_argument("run", nargs="?", metavar="RUN", help="run id or prefix (default: latest)")
        parser.add_argument("-l", "--list", action="store_true", help="list recent runs")
        parser.add_argument("-n", "--limit", type=int, default=20, help="runs to list (default: 20)")
        parser.add_argument("-f", "--follow", action="store_true", help="keep printing new events until the run ends")
        parser.add_argument(
            "--level",
            default="info",
            choices=["debug", "info", "warning", "error"],
            help="minimum level to show (default: info)",
        )
        parser.add_argument("--json", action="store_true", help="print raw JSON lines")
        parser.add_argument("--root", type=Path, help="deployment root (overrides slop.yaml)")

    def run(self, args: argparse.Namespace, console: Console) -> None:
        logDir = Reader.config(args).logDir
        runs = sorted(logDir.glob("*.log"), reverse=True) if logDir.is_dir() else []
        if not runs:
            raise SlopError(f"no runs logged in {logDir} yet")

        if args.list:
            self.list(console, runs[: args.limit])
            return

        if args.run:
            matches = [r for r in runs if r.stem.startswith(args.run)]
            if len(matches) != 1:
                raise SlopError(
                    f"{'no' if not matches else 'more than one'} run matching {args.run!r} (see `slop log -l`)"
                )
            file = matches[0]
        else:
            file = runs[0]
        self.show(console, file, logging.getLevelName(args.level.upper()), args.json, args.follow)

    def list(self, console: Console, runs: list[Path]) -> None:
        console.write(f"<comment>Recent runs</comment> <dim>({runs[0].parent})</dim>")
        for file in runs:
            entries = self.read(file)
            if not entries:
                continue
            start, end = entries[0], entries[-1]
            when = self.time(start, "%Y-%m-%d %H:%M")
            if end.get("event") == "run.finish":
                status = "<success>✔</success>" if end.get("ok") else "<error>✖</error>"
                took = f"<dim>{Duration.format(end.get('duration') or 0)}</dim>"
            else:
                status, took = "<comment>…</comment>", "<dim>running</dim>"
            dry = " <warning>dry run</warning>" if start.get("dry_run") else ""
            message = f" <dim>»</dim> {start['run_message']}" if start.get("run_message") else ""
            console.write(
                f"  {status} <dim>{file.stem}</dim>  {when}  <info>{start.get('user')}</info><dim>@{start.get('host')}</dim>"
                f"  <bold>{start.get('command')}</bold>{dry}  {took}{message}"
            )

    def show(self, console: Console, file: Path, minLevel: int, raw: bool, follow: bool) -> None:
        with file.open(encoding="utf-8") as f:
            while True:
                line = f.readline()
                if not line:
                    if not follow:
                        return
                    time.sleep(0.25)
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if logging.getLevelName(entry.get("level", "INFO")) >= minLevel:
                    console.write(line.rstrip() if raw else self.format(entry))
                if follow and entry.get("event") == "run.finish":
                    return

    def format(self, entry: dict) -> str:
        level = entry.get("level", "INFO")
        style = self.LEVEL_STYLE.get(level, "info")
        where = "/".join(filter(None, [entry.get("project"), entry.get("module")]))
        where = f" <comment>{where}</comment>" if where else ""
        message = entry.get("message", "").replace("\n", "\n      ")
        if entry.get("event") in ("run.start", "run.finish", "project.start"):
            message = f"<bold>{message}</bold>"
        if entry.get("duration") is not None and entry.get("event") in ("module.finish", "command"):
            message += f" <dim>({Duration.format(entry['duration'])})</dim>"
        out = f"<dim>{self.time(entry, '%H:%M:%S')}</dim> <{style}>{level[:4]:<4}</{style}>{where} {message}"
        if entry.get("output"):
            out += "".join(f"\n      <dim>{line}</dim>" for line in entry["output"].splitlines())
        return out

    @staticmethod
    def read(file: Path) -> list[dict]:
        entries = []
        for line in file.read_text(encoding="utf-8").splitlines():
            with contextlib.suppress(json.JSONDecodeError):
                entries.append(json.loads(line))
        return entries

    @staticmethod
    def time(entry: dict, fmt: str) -> str:
        try:
            return datetime.fromisoformat(entry["@timestamp"]).astimezone().strftime(fmt)
        except (KeyError, ValueError):
            return "?"
