import logging
import shutil
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from Slop.Config.Inventory import Inventory
from Slop.Config.Role import Role
from Slop.Context import Context
from Slop.Deploy.DeployFailure import DeployFailure
from Slop.Deploy.Health import Health
from Slop.Project.Project import Project
from Slop.Remote.Ssh import Ssh
from Slop.SlopError import SlopError


class Rollout:
    def __init__(
        self,
        ctx: Context,
        project: Project,
        *,
        hosts: list[str] | None = None,
        roles: list[str] | None = None,
        batch: int | None = None,
        paths: list[str] | None = None,
        changed: set[str] | None = None,
        checksum: bool = False,
    ):
        if not project.deploy.enabled:
            raise SlopError(f"project {project.name} has no deploy: path and roles in slop.yaml")
        self.ctx = ctx
        self.project = project
        self.inventory: Inventory = ctx.config.inventory
        if ctx.config.production and not self.inventory.isMaster() and not ctx.config.data.get("allow-remote-deploys"):
            raise SlopError(f"production deploys run on the deploy master ({self.inventory.master})")
        self.ssh = Ssh(ctx, self.inventory)
        self.health = Health(self.ssh, project.deploy.health)
        self.paths = paths
        self.changed = changed or set()
        self.batch = batch
        self.checksum = checksum
        self.touched: set[str] = set()
        self.done: list[str] = []
        self.onceDone = False
        self.lock = threading.Lock()
        self.plan = self.makePlan(hosts, roles)

    def makePlan(self, hosts: list[str] | None, roles: list[str] | None) -> list[tuple[Role, list[list[str]]]]:
        wanted = [self.inventory.resolveHost(h) for h in hosts] if hosts else None
        for name in roles or []:
            if name not in self.project.deploy.roles:
                raise SlopError(
                    f"{self.project.name} does not deploy to role {name!r} ({', '.join(self.project.deploy.roles)})"
                )
        plan = []
        seen: set[str] = set()
        for name in self.project.deploy.roles:
            if roles and name not in roles:
                continue
            role = self.inventory.role(name)
            members = [h for h in role.hosts if h not in seen and (wanted is None or h in wanted)]
            seen |= set(members)
            if not members:
                continue
            size = self.batch or role.batch
            plan.append((role, [members[i : i + size] for i in range(0, len(members), size)]))
        if wanted:
            missing = [h for h in wanted if h not in seen]
            if missing:
                raise SlopError(
                    f"{', '.join(missing)} {'is' if len(missing) == 1 else 'are'} not in any of {self.project.name}'s roles"
                )
        if not plan:
            raise SlopError(f"no hosts to deploy {self.project.name} to")
        return plan

    @property
    def hosts(self) -> list[str]:
        return [h for _, batches in self.plan for batch in batches for h in batch]

    def show(self) -> None:
        for role, batches in self.plan:
            shown = " <dim>→</dim> ".join(", ".join(Inventory.short(h) for h in batch) for batch in batches)
            self.ctx.console.write(f"  <comment>{role.name}</comment>: {shown}")

    def preflight(self) -> None:
        targets = set(self.hosts)
        remote = sorted(h for h in targets if not self.inventory.isLocal(h))
        if not remote:
            return
        problems = []
        with ThreadPoolExecutor(max_workers=min(self.ctx.jobs, len(remote))) as pool:
            for host, problem in zip(remote, pool.map(self.ssh.reachable, remote), strict=False):
                if problem:
                    problems.append(f"{Inventory.short(host)}: {problem}")
        if problems:
            raise SlopError("cannot reach every host over ssh:\n" + "\n".join(problems))
        self.ctx.success(f"reached <info>{len(remote)}</info> hosts over ssh")

    def run(self) -> None:
        for role, batches in self.plan:
            self.ctx.console.section(f"Deploying to {role.name}")
            total = sum(len(b) for b in batches)
            with self.ctx.progress(total, "Deployed to", "host") as bar:
                for batch in batches:
                    failures: list[DeployFailure] = []
                    with ThreadPoolExecutor(max_workers=max(1, min(self.ctx.jobs, len(batch)))) as executor:
                        futures = [executor.submit(self.guarded, bar, role, host) for host in batch]
                        for future in futures:
                            failure = future.result()
                            if failure:
                                failures.append(failure)
                    if failures:
                        for failure in failures[1:]:
                            self.ctx.warn(str(failure))
                        raise failures[0]
            if role.soak and self.health.enabled and not self.ctx.dryRun and len(self.plan) > 1:
                self.soak(role, [h for batch in batches for h in batch])

    def soak(self, role: Role, hosts: list[str]) -> None:
        self.ctx.log(f"<comment>soaking {role.name} for {role.soak:g}s</comment> before going further")
        time.sleep(role.soak)
        for host in hosts:
            try:
                result = self.health.check(host, role.name)
            except SlopError as e:
                raise DeployFailure(Inventory.short(host), f"{role.name} soak", str(e), set(self.touched)) from None
            self.ctx.success(f"{Inventory.short(host)} still healthy after soaking: {result}")

    def lint(self) -> None:
        files = self.project.lintFiles()
        if not files:
            return
        if shutil.which("php") is None:
            self.ctx.warn("php is not installed here, so the build was not linted")
            return
        with ThreadPoolExecutor(max_workers=self.ctx.jobs) as pool:
            procs = list(pool.map(lambda f: self.ctx.run(["php", "-l", f], check=False, mutates=False), files))
        broken = [(f, p) for f, p in zip(files, procs, strict=True) if p.returncode != 0]
        if broken:
            raise SlopError(
                "PHP syntax errors in the build; nothing was deployed:\n"
                + "\n".join(f"{self.ctx.display(f)}: {(p.stdout or p.stderr).strip()}" for f, p in broken)
            )
        self.ctx.success(f"linted <info>{len(files)}</info> PHP files")

    def guarded(self, bar, role: Role, host: str) -> DeployFailure | None:
        step = {"name": "start"}
        with bar.task(Inventory.short(host)):
            try:
                self.deployHost(role, host, step)
                return None
            except SlopError as e:
                self.ctx.event(
                    logging.ERROR,
                    f"{host}: {step['name']} failed: {e}",
                    event="host.failed",
                    host=host,
                    step=step["name"],
                )
                return DeployFailure(Inventory.short(host), step["name"], str(e), set(self.touched))

    def deployHost(self, role: Role, host: str, step: dict) -> None:
        short = Inventory.short(host)
        dest = self.project.deploy.path

        step["name"] = "sync"
        with self.lock:
            self.touched.add(host)
        proc = self.ssh.rsync(
            host,
            self.project.root,
            dest,
            excludes=self.project.deploy.exclude,
            owner=self.project.deploy.owner,
            chmod=self.project.deploy.chmod,
            paths=self.paths,
            checksum=self.checksum,
        )
        self.ctx.item("Synced", short, Ssh.transferred(proc) or str(dest))

        with self.lock:
            runOnce = not self.onceDone and bool(self.project.deploy.once)
            self.onceDone = True
        for command in self.project.deploy.once if runOnce else []:
            step["name"] = f"once: {command}"
            self.ctx.item("Running once", short, command)
            self.ssh.run(host, command.format(path=dest))

        for command in self.project.afterSyncCommands(self.changed, dest):
            step["name"] = command.split()[-1] if "rebuildLocalisationCache" not in command else "l10n"
            self.ctx.item("Running", short, command)
            self.ssh.run(host, command, timeout=1800)

        for name in role.services:
            if self.project.deploy.restart is not None and name not in self.project.deploy.restart:
                continue
            service = self.inventory.services[name]
            for unit in service.units:
                step["name"] = f"{service.action} {unit}"
                self.ctx.item(service.action.capitalize() + "ing", f"{short} {unit}")
                self.ssh.run(host, self.ssh.sudo(f"systemctl {service.action} {unit}"))

        if self.health.enabled and not self.ctx.dryRun:
            step["name"] = "smoke tests"
            result = self.health.check(host, role.name)
            self.ctx.item("Healthy", short, result)

        with self.lock:
            self.done.append(host)

    @staticmethod
    def hostsLabel(hosts: list[str] | set[str]) -> str:
        return ", ".join(sorted(Inventory.short(h) for h in hosts))

    @property
    def target(self) -> Path:
        return self.project.deploy.path
