import argparse

from Slop.Command.Command import Command
from Slop.Output.Console import Console
from Slop.Project.ProjectLoader import ProjectLoader


class Modules(Command):
    name = "modules"
    help = "list each project type's build modules"
    forward = False

    def configure(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument("type", nargs="?", help="only this project type")

    def run(self, args: argparse.Namespace, console: Console) -> None:
        types = [ProjectLoader.get(args.type)] if args.type else ProjectLoader.all().values()
        for i, projectType in enumerate(types):
            if i:
                console.write()
            modules = projectType.modules()
            order = [m for stage in projectType.STAGES.values() for m in stage]
            names = sorted(modules, key=lambda n: order.index(n) if n in order else len(order))
            width = max(map(len, names))
            console.write(f"<comment>{projectType.type}</comment> <dim>— {projectType.description}</dim>")
            for name in names:
                console.write(f"  <info>{name:<{width}}</info>  {modules[name].description}")
