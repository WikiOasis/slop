import argparse

from Slop.Command.Command import Command
from Slop.Deploy.Lock import Lock as DeployLock
from Slop.Log.Invoker import Invoker
from Slop.Notify.Notifier import Notifier
from Slop.Output.Console import Console
from Slop.Runner import Runner
from Slop.Sal.Sal import Sal


class Unlock(Command):
    name = "unlock"
    help = "release locks taken with `slop lock`"
    forward = "noconfig"

    def configure(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "projects", nargs="*", metavar="PROJECT", help="projects to unlock (default: slop.yaml's default)"
        )
        parser.add_argument("-a", "--all", action="store_true", help="release the --all lock")
        parser.add_argument("--force", dest="breakLock", action="store_true", help="break someone else's lock")
        Runner.addMinimalOptions(parser)

    def run(self, args: argparse.Namespace, console: Console) -> None:
        runner = Runner(args, console)
        names = [DeployLock.ALL] if args.all else (args.projects or runner.config.defaults[:1])
        ctx = runner.start(f"unlock {'--all' if args.all else ' '.join(names)}")
        try:
            locks = DeployLock(runner.config.lockDir, runner.config.repo, runner.config.push)
            released = []
            broke = []
            for name in names:
                holder = locks.release(name, args.breakLock)
                if holder is None:
                    ctx.skip(f"{DeployLock.label(name)} was not locked")
                    continue
                ctx.success(f"unlocked {DeployLock.label(name)}")
                released.append(name)
                if args.breakLock and holder.get("user") and holder["user"] != Invoker.user():
                    broke.append(holder["user"])
            if released:
                what = "all deploys" if args.all else f"deploys of {', '.join(released)}"
                text = f"Unlocked {what}"
                text += f" (broke {', '.join(dict.fromkeys(broke))}'s lock)" if broke else ""
                text += f": {args.message}" if args.message else ""
                Sal(ctx, runner.config.sal).log(text)
                Notifier(ctx, runner.config.notify).send(f"{text} ({Invoker.label()})", True)
        except BaseException as e:
            runner.fail(e)
            raise
        runner.finish()
