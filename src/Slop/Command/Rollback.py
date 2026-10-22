import argparse

from Slop.Command.Command import Command
from Slop.Deploy.Deployment import Deployment
from Slop.Output.Console import Console
from Slop.Runner import Runner


class Rollback(Command):
    name = "rollback"
    help = "rebuild a previously deployed state from the config repo and roll it out"

    def configure(self, parser: argparse.ArgumentParser) -> None:
        parser.formatter_class = argparse.RawDescriptionHelpFormatter
        parser.description = self.help + (
            "\n\nREV is a config repo commit that recorded a deploy (see `slop state --history`); the default is "
            "the deploy before the current one. Every repo is checked out at the recorded commit, patches and "
            "composer.lock come from that revision, and the config files are restored to it and committed.\n\n"
            "examples:\n  slop rollback -m 'Broke uploads' -t T789\n  slop rollback 3f2a1bc"
        )
        parser.add_argument("rev", nargs="?", metavar="REV", help="config revision to roll back to")
        parser.add_argument("-p", "--project", help="project to roll back (default: slop.yaml's default)")
        Deployment.addOptions(parser)

    def run(self, args: argparse.Namespace, console: Console) -> None:
        Deployment(Runner(args, console), args).rollback(args.project, args.rev)
