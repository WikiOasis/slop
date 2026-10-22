import argparse

from Slop.Command.Command import Command
from Slop.Deploy.Deployment import Deployment
from Slop.Output.Console import Console
from Slop.Runner import Runner


class Deploy(Command):
    name = "deploy"
    help = "pull the config, build, then roll out to every host (canary first); rolls back automatically on failure"

    def configure(self, parser: argparse.ArgumentParser) -> None:
        parser.formatter_class = argparse.RawDescriptionHelpFormatter
        parser.description = self.help + (
            "\n\nOn success the new state (every repo's exact commit, patches and composer.lock) is written to "
            "state/<project>.yaml in the config repo, committed and pushed. On failure the hosts that were touched "
            "are rebuilt from the last recorded state and re-synced.\n\n"
            "examples:\n"
            "  slop deploy -m 'Enable FooBar' -t T123\n"
            "  slop deploy config -m 'Raise upload limit'\n"
            "  slop deploy extensions CheckUser -m 'Security fix' -t T456\n"
            "  slop deploy --hosts mw-us-east-011 --resync\n"
            "  slop deploy -n                      show what would happen"
        )
        parser.add_argument(
            "what", nargs="?", metavar="WHAT", help="only build and sync this part (config, core, extensions, ...)"
        )
        parser.add_argument("names", nargs="*", metavar="NAME", help="limit to these (e.g. extension names)")
        parser.add_argument("-p", "--project", help="project to deploy (default: slop.yaml's default)")
        parser.add_argument(
            "--skip-build", dest="skipBuild", action="store_true", help="deploy the build tree as it is"
        )
        parser.add_argument("--resync", action="store_true", help="sync even if nothing changed since the last deploy")
        parser.add_argument(
            "--no-rollback", dest="noRollback", action="store_true", help="on failure, leave hosts as they are"
        )
        Deployment.addOptions(parser)

    def run(self, args: argparse.Namespace, console: Console) -> None:
        Deployment(Runner(args, console), args).deploy(args.project, args.what, args.names)
