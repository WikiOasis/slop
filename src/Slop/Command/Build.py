import argparse

from Slop.Command.Command import Command
from Slop.Output.Console import Console
from Slop.Runner import Runner


class Build(Command):
    name = "build"
    help = "run the build stage for projects without deploying"

    def configure(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "projects", nargs="*", metavar="PROJECT", help="projects to build (default: slop.yaml's default)"
        )
        parser.add_argument("-a", "--all", action="store_true", help="build every project")
        parser.add_argument("--only", type=self.csv, metavar="MOD[,MOD...]", help="only run these modules")
        parser.add_argument("--skip", type=self.csv, default=[], metavar="MOD[,MOD...]", help="skip these modules")
        parser.add_argument("--no-pull", dest="noPull", action="store_true", help="don't pull the config repo first")
        Runner.addOptions(parser)

    def run(self, args: argparse.Namespace, console: Console) -> None:
        runner = Runner(args, console)
        runner.syncConfig()
        projects = runner.projects(list(runner.config.projects) if args.all else args.projects)
        plans = []
        for project in projects:
            for name in [*(args.only or []), *args.skip]:
                project.module(name)
            pipeline = [m for m in (args.only or project.stage("build")) if m not in args.skip]
            plans.append((project, pipeline, None, set(args.only or [])))
        runner.run("build", plans)
