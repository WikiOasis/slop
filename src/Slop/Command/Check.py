import argparse
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from Slop.Command.Command import Command
from Slop.Command.Reader import Reader
from Slop.Config.Inventory import Inventory
from Slop.Deploy.Health import Health
from Slop.Output.Console import Console
from Slop.Remote.Ssh import Ssh
from Slop.SlopError import SlopError


class Check(Command):
    name = "check"
    help = "run a project's smoke tests (test.wikioasis.org on each host) without deploying anything"

    def configure(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument("-p", "--project", help="project (default: slop.yaml's default)")
        parser.add_argument("--hosts", type=self.csv, metavar="H[,H...]", help="only these hosts")
        parser.add_argument("--role", dest="roles", type=self.csv, metavar="R[,R...]", help="only these roles")
        parser.add_argument("--all-tests", dest="allTests", action="store_true", help="also run canary-only tests")
        parser.add_argument("--root", type=Path, help="deployment root (overrides slop.yaml)")

    def run(self, args: argparse.Namespace, console: Console) -> int:
        config = Reader.config(args)
        project = config.project(args.project or config.defaults[0])
        ctx = Reader.context(args, console, config)
        health = Health(Ssh(ctx, config.inventory), project.deploy.health)
        if not health.enabled:
            raise SlopError(f"{project.name} has no deploy.checks in slop.yaml")
        wanted = {config.inventory.resolveHost(h) for h in args.hosts} if args.hosts else None
        targets: list[tuple[str, str]] = []
        for role in project.deploy.roles:
            if args.roles and role not in args.roles:
                continue
            for host in config.inventory.role(role).hosts:
                if (wanted is None or host in wanted) and host not in [h for h, _ in targets]:
                    targets.append((host, role))
        if not targets:
            raise SlopError("no hosts to check")

        def runOne(target: tuple[str, str]):
            host, role = target
            return health.run(host, None if args.allTests else role)

        with ThreadPoolExecutor(max_workers=16) as pool:
            outcomes = list(pool.map(runOne, targets))
        failures = 0
        for (host, role), results in zip(targets, outcomes, strict=True):
            bad = [r for r in results if not r.ok]
            failures += len(bad)
            badge = "<success>✔</success>" if not bad else "<error>✖</error>"
            console.write(f"{badge} <info>{Inventory.short(host)}</info> <dim>{role}</dim>")
            for result in results:
                mark = "<success>✔</success>" if result.ok else "<error>✖</error>"
                console.write(f"    {mark} {result.name} <dim>{result.detail}, {result.seconds:.2f}s</dim>")
        return 1 if failures else 0
