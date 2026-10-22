import argparse

from Slop.Command.Command import Command
from Slop.Config.Inventory import Inventory
from Slop.Deploy.Lock import Lock
from Slop.Notify.Notifier import Notifier
from Slop.Output.Console import Console
from Slop.Output.Duration import Duration
from Slop.Remote.Ssh import Ssh
from Slop.Runner import Runner
from Slop.Sal.Sal import Sal
from Slop.SlopError import SlopError


class Restart(Command):
    name = "restart"
    help = "rolling restart or reload of an inventory service, one host at a time"

    def configure(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument("service", help="service name from the inventory, e.g. php-fpm")
        parser.add_argument("--role", dest="roles", type=self.csv, metavar="R[,R...]", help="only these roles")
        parser.add_argument("--hosts", type=self.csv, metavar="H[,H...]", help="only these hosts")
        parser.add_argument("--action", choices=["reload", "restart"], help="override the service's action")
        Runner.addMinimalOptions(parser)

    def run(self, args: argparse.Namespace, console: Console) -> None:
        runner = Runner(args, console)
        inventory = runner.config.inventory
        if args.service not in inventory.services:
            raise SlopError(f"no service {args.service!r} in the inventory ({', '.join(inventory.services)})")
        service = inventory.services[args.service]
        action = args.action or service.action
        wanted = {inventory.resolveHost(h) for h in args.hosts} if args.hosts else None
        targets = []
        for role in inventory.roles.values():
            if args.service not in role.services or (args.roles and role.name not in args.roles):
                continue
            for host in role.hosts:
                if (wanted is None or host in wanted) and host not in [h for h, _ in targets]:
                    targets.append((host, role))
        if not targets:
            raise SlopError(f"no hosts run {args.service}")

        ctx = runner.start(f"restart {args.service}")
        sal = Sal(ctx, runner.config.sal)
        begun = False
        what = f"Rolling {action} of {args.service} on {len(targets)} hosts" + (
            f": {args.message}" if args.message else ""
        )
        try:
            roles = {role.name for _, role in targets}
            locks = Lock(runner.config.lockDir, runner.config.repo, runner.config.push)
            locks.sync()
            locks.checkHeld(*[p.name for p in runner.config.projects.values() if roles & set(p.deploy.roles)])
            sal.log(f"Starting: {what}")
            begun = True
            ssh = Ssh(ctx, inventory)
            with ctx.progress(len(targets), f"{action.capitalize()}ed {args.service} on", "host") as bar:
                for host, _ in targets:
                    with bar.task(Inventory.short(host)):
                        for unit in service.units:
                            ctx.item(action.capitalize() + "ing", f"{Inventory.short(host)} {unit}")
                            ssh.run(host, ssh.sudo(f"systemctl {action} {unit}"))
            sal.log(f"Finished: {what} in {Duration.format(ctx.runLog.elapsed)}")
        except BaseException as e:
            if begun:
                reason = str(e).splitlines()[0] if isinstance(e, SlopError) and str(e) else type(e).__name__
                sal.log(f"FAILED: {what}: {reason}")
                sal.log(f"Ended: {what} after {Duration.format(ctx.runLog.elapsed)}, some hosts may not have restarted")
                Notifier(ctx, runner.config.notify).send(f"{what} FAILED: {reason}", False)
            runner.fail(e)
            raise
        runner.finish()
