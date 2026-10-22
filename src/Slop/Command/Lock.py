import argparse
import re

from Slop.Command.Command import Command
from Slop.Deploy.Lock import Lock as DeployLock
from Slop.Log.Invoker import Invoker
from Slop.Notify.Notifier import Notifier
from Slop.Output.Console import Console
from Slop.Runner import Runner
from Slop.Sal.Sal import Sal
from Slop.SlopError import SlopError


class Lock(Command):
    name = "lock"
    help = "stop anyone else deploying one or more projects (or everything with --all); pushed to the config repo"
    forward = "noconfig"

    def configure(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "projects", nargs="*", metavar="PROJECT", help="projects to lock (default: slop.yaml's default)"
        )
        parser.add_argument("-a", "--all", action="store_true", help="lock every project")
        parser.add_argument(
            "--for", dest="duration", metavar="DURATION", help="expire after this long, e.g. 30m, 2h, 1d"
        )
        Runner.addMinimalOptions(parser)

    @staticmethod
    def seconds(text: str | None) -> float | None:
        if not text:
            return None
        match = re.fullmatch(r"(\d+)\s*([smhd])", text.strip())
        if not match:
            raise SlopError(f"{text!r} is not a duration like 30m, 2h or 1d")
        return int(match.group(1)) * {"s": 1, "m": 60, "h": 3600, "d": 86400}[match.group(2)]

    def run(self, args: argparse.Namespace, console: Console) -> None:
        if not args.message:
            raise SlopError("say why with -m, e.g. slop lock -m 'investigating T123'")
        if args.all and args.projects:
            raise SlopError("pass project names or --all, not both")
        runner = Runner(args, console)
        names = [DeployLock.ALL] if args.all else (args.projects or runner.config.defaults[:1])
        for name in names:
            if name != DeployLock.ALL:
                runner.config.project(name)
        ctx = runner.start(f"lock {'--all' if args.all else ' '.join(names)}")
        try:
            locks = DeployLock(runner.config.lockDir, runner.config.repo, runner.config.push)
            for name in names:
                holder = locks.hold(name, args.message, self.seconds(args.duration))
                ctx.success(f"locked {DeployLock.label(name)}: {DeployLock.describe(holder)}")
            what = "all deploys" if args.all else f"deploys of {', '.join(names)}"
            text = f"Locked {what}" + (f" for {args.duration}" if args.duration else "") + f": {args.message}"
            Sal(ctx, runner.config.sal).log(text)
            Notifier(ctx, runner.config.notify).send(f"{text} ({Invoker.label()})", False)
        except BaseException as e:
            runner.fail(e)
            raise
        runner.finish()
