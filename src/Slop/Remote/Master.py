import os
import shlex
import shutil
from pathlib import Path

from Slop.Config.ConfigLoader import ConfigLoader
from Slop.Config.Inventory import Inventory
from Slop.Log.Invoker import Invoker
from Slop.Output.Console import Console
from Slop.SlopError import SlopError


class Master:
    @classmethod
    def name(cls, configDir: Path | None) -> str | None:
        if os.environ.get("SLOP_MASTER"):
            return os.environ["SLOP_MASTER"]
        try:
            return ConfigLoader(configDir).load().inventory.master
        except SlopError:
            return None

    @classmethod
    def forwardIfNeeded(cls, configDir: Path | None, argv: list[str], console: Console) -> None:
        if os.environ.get("SLOP_FORWARDED"):
            return
        master = cls.name(configDir)
        if not master or master in Inventory.thisHost() or master.split(".", 1)[0] in Inventory.thisHost():
            return
        ssh = shutil.which("ssh")
        if ssh is None:
            raise SlopError(f"slop runs on the deploy master ({master}) and ssh is not installed here")
        remote = f"SLOP_FORWARDED=1 SLOP_INVOKER={shlex.quote(Invoker.label())} slop " + shlex.join(cls.strip(argv))
        console.write(f"<dim>→ running on the deploy master {master}</dim>", err=True)
        os.execv(ssh, [ssh, "-t", "-o", "LogLevel=ERROR", master, remote])

    @staticmethod
    def strip(argv: list[str]) -> list[str]:
        kept = []
        skip = False
        for arg in argv:
            if skip:
                skip = False
                continue
            if arg in ("-c", "--config"):
                skip = True
                continue
            if arg.startswith("--config="):
                continue
            kept.append(arg)
        return kept
