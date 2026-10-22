import argparse

from Slop.Command.Command import Command
from Slop.Command.Reader import Reader
from Slop.Deploy.Lock import Lock as DeployLock
from Slop.Output.Console import Console
from Slop.SlopError import SlopError


class Locks(Command):
    name = "locks"
    help = "list held locks and running deploys"
    forward = "noconfig"

    def run(self, args: argparse.Namespace, console: Console) -> None:
        config = Reader.config(args)
        locks = DeployLock(config.lockDir, config.repo, config.push)
        try:
            locks.sync()
        except SlopError as e:
            console.warning(f"showing the locks as of the last pull: {e}")
        locks = locks.all()
        if not locks:
            console.write("<dim>nothing is locked</dim>")
            return
        for name, holder in locks.items():
            label = "everything" if name == DeployLock.ALL else name
            console.write(f"  <warning>🔒</warning> <info>{label}</info>  {DeployLock.describe(holder)}")
