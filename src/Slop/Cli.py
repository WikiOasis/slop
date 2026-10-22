import argparse
import os
import sys
from pathlib import Path

from Slop import VERSION
from Slop.Command.CommandLoader import CommandLoader
from Slop.Config.ConfigLoader import ConfigLoader
from Slop.Output.Console import Console
from Slop.Remote.Master import Master
from Slop.SlopError import SlopError


class Cli:
    @classmethod
    def main(cls, argv: list[str] | None = None) -> int:
        argv = sys.argv[1:] if argv is None else argv
        args = cls.parser().parse_args(argv)
        console = Console(decorated=args.ansi)
        try:
            command = CommandLoader.all()[args.command]
            if (
                command.forward
                and not args.local
                and (command.forward != "noconfig" or not ConfigLoader.exists(args.config))
            ):
                Master.forwardIfNeeded(args.config, argv, console)
            return command.run(args, console) or 0
        except SlopError as e:
            console.errorBlock("Error", str(e))
            return 1
        except BrokenPipeError:
            os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
            return 0
        except KeyboardInterrupt:
            console.write()
            console.write("<comment>Interrupted.</comment>", err=True)
            return 130

    @classmethod
    def parser(cls) -> argparse.ArgumentParser:
        p = argparse.ArgumentParser(prog="slop", description="WikiOasis deployment tooling, scap style")
        p.add_argument("--version", action="version", version=f"slop {VERSION}")
        cls.addGlobalOptions(p, None, None, False)

        sub = p.add_subparsers(dest="command", required=True, metavar="COMMAND")
        for name, command in CommandLoader.all().items():
            parser = sub.add_parser(name, help=command.help, description=command.help)
            command.configure(parser)
            cls.addGlobalOptions(parser, argparse.SUPPRESS, argparse.SUPPRESS, argparse.SUPPRESS)
        return p

    @staticmethod
    def addGlobalOptions(parser: argparse.ArgumentParser, configDefault, ansiDefault, localDefault) -> None:
        group = parser.add_argument_group("global options")
        group.add_argument(
            "-c",
            "--config",
            type=Path,
            default=configDefault,
            help="config directory (default: $SLOP_CONFIG, ./config or slop's own config/)",
        )
        group.add_argument(
            "--local", action="store_true", default=localDefault, help="run here instead of on the deploy master"
        )
        colour = group.add_mutually_exclusive_group()
        colour.add_argument(
            "--ansi", dest="ansi", action="store_const", const=True, default=ansiDefault, help="force colour output"
        )
        colour.add_argument(
            "--no-ansi",
            dest="ansi",
            action="store_const",
            const=False,
            default=ansiDefault,
            help="disable colour output",
        )
