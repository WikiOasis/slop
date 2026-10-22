import argparse
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from Slop.Command.Command import Command
from Slop.Command.Reader import Reader
from Slop.Config.Inventory import Inventory
from Slop.Output.Console import Console
from Slop.Project.Project import Project
from Slop.Remote.Ssh import Ssh


class Status(Command):
    name = "status"
    help = "show what each host is running and whether it matches the last deploy"

    def configure(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument("-p", "--project", help="project (default: slop.yaml's default)")
        parser.add_argument("--root", type=Path, help="deployment root (overrides slop.yaml)")

    def run(self, args: argparse.Namespace, console: Console) -> None:
        config = Reader.config(args)
        project = config.project(args.project or config.defaults[0])
        ctx = Reader.context(args, console, config)
        ssh = Ssh(ctx, config.inventory)
        state = project.stateFile().load()
        hosts = []
        for role in project.deploy.roles:
            for host in config.inventory.role(role).hosts:
                if host not in [h for h, _ in hosts]:
                    hosts.append((host, role))

        def fetch(host: str) -> dict | str:
            proc = ssh.run(
                host, f"cat {project.deploy.path}/{Project.STATE_FILE}", check=False, mutates=False, timeout=30
            )
            if proc.returncode != 0:
                return (
                    (proc.stderr or "no marker").strip().splitlines()[-1]
                    if (proc.stderr or "").strip()
                    else "never deployed by slop"
                )
            try:
                return json.loads(proc.stdout)
            except ValueError:
                return "unreadable marker"

        with ThreadPoolExecutor(max_workers=16) as pool:
            markers = dict(zip([h for h, _ in hosts], pool.map(fetch, [h for h, _ in hosts]), strict=False))

        expected = state.get("run")
        console.write(
            f"<comment>{project.name}</comment> last deployed {state.get('deployed-at', 'never')} "
            f"by <info>{state.get('deployed-by', '?')}</info> <dim>(run {expected or '?'})</dim>"
        )
        width = max((len(Inventory.short(h)) for h, _ in hosts), default=0)
        for host, role in hosts:
            marker = markers[host]
            if isinstance(marker, str):
                text = f"<error>{marker}</error>"
            else:
                same = marker.get("run") == expected
                badge = "<success>✔ current</success>" if same else "<warning>✖ differs</warning>"
                text = (
                    f"{badge} <dim>run {marker.get('run')} by {marker.get('by')} at {str(marker.get('at'))[:16]}</dim>"
                )
            console.write(f"  <info>{Inventory.short(host):<{width}}</info>  <dim>{role:<10}</dim> {text}")
