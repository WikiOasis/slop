import argparse
import sys

from Slop.Config.Config import Config
from Slop.Config.ConfigLoader import ConfigLoader
from Slop.Context import Context
from Slop.Log.RunLog import RunLog
from Slop.Output.Console import Console
from Slop.SlopError import SlopError


class Reader:
    @staticmethod
    def config(args: argparse.Namespace, pull: bool = True) -> Config:
        config = ConfigLoader(args.config).load(root=getattr(args, "root", None))
        repo = config.repo
        if not pull or getattr(args, "noPull", False) or not config.pull or not repo.hasRemote():
            return config
        try:
            before, after = repo.pull()
        except SlopError as e:
            print(f"warning: showing the config as it is here, it could not be pulled: {e}", file=sys.stderr)
            return config
        if before != after:
            return ConfigLoader(args.config).load(root=getattr(args, "root", None))
        return config

    @staticmethod
    def context(args: argparse.Namespace, console: Console, config: Config) -> Context:
        return Context(
            console,
            RunLog(None, "read", [], None, False),
            config,
            jobs=getattr(args, "jobs", 16) or 16,
            interactive=False,
        )
