import argparse
from pathlib import Path

from Slop.Command.Command import Command
from Slop.Command.Reader import Reader
from Slop.Deploy.StateDiff import StateDiff
from Slop.Git.Git import Git
from Slop.Output.Console import Console


class Diff(Command):
    name = "diff"
    help = "compare the build tree with the last deploy: what `slop deploy --skip-build` would ship"

    def configure(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument("-p", "--project", help="project (default: slop.yaml's default)")
        parser.add_argument("--root", type=Path, help="deployment root (overrides slop.yaml)")

    def run(self, args: argparse.Namespace, console: Console) -> None:
        config = Reader.config(args)
        project = config.project(args.project or config.defaults[0])
        ctx = Reader.context(args, console, config)
        ctx.project = project
        git = Git(ctx)
        deployed = project.stateFile().load().get("repos") or {}
        diff = StateDiff.compare(deployed, project.manifest(git))
        console.write(f"<comment>{project.name}</comment>: build tree vs last deploy")
        diff.show(console, git, project.root)
        changes = config.repo.changes(project.configPaths())
        if changes:
            console.write()
            console.write(
                "<comment>Uncommitted config changes</comment> <dim>(committed by the next successful deploy or `slop config commit`)</dim>"
            )
            for line in changes:
                console.write(f"  <dim>{line}</dim>")
