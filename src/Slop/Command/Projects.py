import argparse
from pathlib import Path

from Slop.Command.Command import Command
from Slop.Command.Reader import Reader
from Slop.Output.Console import Console


class Projects(Command):
    name = "projects"
    help = "list the configured projects"
    forward = False

    def configure(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument("--root", type=Path, help="deployment root (overrides slop.yaml)")

    def run(self, args: argparse.Namespace, console: Console) -> None:
        config = Reader.config(args)
        width = max(map(len, config.projects))
        console.write(f"<comment>Projects</comment> <dim>built under {config.root}</dim>")
        for name, project in config.projects.items():
            default = " <success>(default)</success>" if name in config.defaults else ""
            target = (
                f"  <dim>→ {project.deploy.path} on {', '.join(project.deploy.roles)}</dim>"
                if project.deploy.enabled
                else "  <dim>(not deployed)</dim>"
            )
            console.write(f"  <info>{name:<{width}}</info>  {project.type:<9} {project.summary()}{default}{target}")
