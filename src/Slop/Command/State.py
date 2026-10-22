import argparse
from pathlib import Path

from Slop.Command.Command import Command
from Slop.Command.Reader import Reader
from Slop.Config.StateFile import StateFile
from Slop.Deploy.StateDiff import StateDiff
from Slop.Output.Console import Console
from Slop.SlopError import SlopError


class State(Command):
    name = "state"
    help = "show what is deployed, as recorded in the config repo's statefile, or the deploy history"
    forward = "noconfig"

    def configure(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument("rev", nargs="?", metavar="REV", help="show the state recorded at this config revision")
        parser.add_argument("-p", "--project", help="project (default: slop.yaml's default)")
        parser.add_argument("--history", action="store_true", help="list recorded deploys and rollbacks")
        parser.add_argument("-n", "--limit", type=int, default=20, help="deploys to list (default: 20)")
        parser.add_argument("--root", type=Path, help="deployment root (overrides slop.yaml)")

    def run(self, args: argparse.Namespace, console: Console) -> None:
        config = Reader.config(args)
        project = config.project(args.project or config.defaults[0])
        stateFile = project.stateFile()
        repo = config.repo

        if args.history:
            history = repo.history(stateFile.relative, args.limit)
            if not history:
                raise SlopError(f"no deploys of {project.name} recorded in {config.configDir}")
            console.write(
                f"<comment>Deploys of {project.name}</comment> <dim>(newest first; roll back with `slop rollback REV`)</dim>"
            )
            for i, entry in enumerate(history):
                marker = "<success>●</success>" if i == 0 else "<dim>○</dim>"
                console.write(
                    f"  {marker} <comment>{entry['commit'][:7]}</comment>  {entry['date'][:16].replace('T', ' ')}  "
                    f"<info>{entry['author']}</info>  {entry['subject']}"
                )
            return

        if args.rev:
            rev = repo.resolve(args.rev)
            state = StateFile.parse(repo.show(rev, stateFile.relative), f"{rev[:7]}:{stateFile.relative}")
        else:
            state = stateFile.load()
        if not state:
            raise SlopError(f"no state recorded for {project.name}" + (f" at {args.rev}" if args.rev else " yet"))

        console.write(
            f"<comment>{project.name}</comment> deployed {state.get('deployed-at', '?')} by <info>{state.get('deployed-by', '?')}</info> <dim>(run {state.get('run', '?')})</dim>"
        )
        if state.get("message"):
            console.write(f"  <dim>»</dim> {state['message']}")
        if state.get("tasks"):
            console.write(f"  tasks: <comment>{' '.join(state['tasks'])}</comment>")
        if state.get("rollback-of"):
            console.write(f"  <warning>rollback</warning> of {state['rollback-of'][:7]}")
        repos = state.get("repos") or {}
        width = max((len(StateDiff.label(k)) for k in repos), default=0)
        for key, entry in sorted(repos.items()):
            pinned = " <comment>pinned</comment>" if entry.get("pinned") else ""
            console.write(
                f"  <info>{StateDiff.label(key):<{width}}</info>  {str(entry.get('base'))[:10]}  <dim>{entry.get('branch')}</dim>{pinned}"
            )
            for patch in entry.get("patches") or []:
                task = f" <comment>{patch['task']}</comment>" if patch.get("task") else ""
                console.write(f"      <dim>+ {patch['name']}: {patch.get('source')}</dim>{task}")
