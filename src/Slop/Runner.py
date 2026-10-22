import argparse
import logging
import sys
import time
from pathlib import Path

from Slop import VERSION
from Slop.Config.Config import Config
from Slop.Config.ConfigLoader import ConfigLoader
from Slop.Context import Context
from Slop.Log.RunLog import RunLog
from Slop.Output.Console import Console
from Slop.Output.Duration import Duration
from Slop.Project.Project import Project
from Slop.Project.ProjectLoader import ProjectLoader
from Slop.SlopError import SlopError

Plan = tuple[Project, list[str], set[Path] | None, set[str]]


class Runner:
    @classmethod
    def addMinimalOptions(cls, parser: argparse.ArgumentParser) -> None:
        parser.add_argument("-m", "--message", help="why you're running this; recorded in the run log and the SAL")
        parser.add_argument("-n", "--dry-run", dest="dryRun", action="store_true", help="show what would be done")
        parser.add_argument("-v", "--verbose", action="store_true", help="show every command")
        parser.add_argument(
            "--no-interaction", dest="interactive", action="store_const", const=False, default=None, help="never prompt"
        )
        parser.set_defaults(force=False, jobs=16, root=None, production=False)

    @classmethod
    def addOptions(cls, parser: argparse.ArgumentParser, *, message: bool = True) -> None:
        if message:
            parser.add_argument("-m", "--message", help="why you're running this; recorded in the run log and the SAL")
        parser.add_argument("--root", type=Path, help="deployment root (overrides slop.yaml)")
        parser.add_argument("-j", "--jobs", type=int, default=16, help="parallel git and host operations (default: 16)")
        parser.add_argument("-n", "--dry-run", dest="dryRun", action="store_true", help="show what would be done")
        parser.add_argument("-v", "--verbose", action="store_true", help="show every item and command")
        parser.add_argument("-f", "--force", action="store_true", help="discard local changes in checkouts")
        parser.add_argument(
            "--no-interaction",
            dest="interactive",
            action="store_const",
            const=False,
            default=None,
            help="never prompt; fail on conflicts instead",
        )
        parser.add_argument("--production", action="store_true", help="allow a /srv root away from the deploy master")
        for projectType in ProjectLoader.all().values():
            projectType.addOptions(parser)

    def __init__(self, args: argparse.Namespace, console: Console):
        self.args = args
        self.console = console
        self.config = self.load()
        self.ctx: Context | None = None
        self.timings: list[tuple[str, str, float]] = []
        self.pulled: tuple[str | None, str | None] | None = None

    def load(self, configDir: Path | None = None) -> Config:
        base = ConfigLoader(self.args.config).base
        return ConfigLoader(configDir or self.args.config, base=base).load(root=getattr(self.args, "root", None))

    def guard(self) -> None:
        config = self.config
        if config.production and not config.inventory.isMaster() and not getattr(self.args, "production", False):
            raise SlopError(
                f"{config.root} is a production root and this is not the deploy master "
                f"({config.inventory.master}); run slop there, or pass --production if you mean it"
            )

    def reload(self) -> None:
        self.config = self.load()
        if self.ctx:
            self.ctx.config = self.config

    def projects(self, names: list[str] | None) -> list[Project]:
        projects = []
        for name in names or self.config.defaults:
            project = self.config.project(name)
            project.applyOptions(self.args)
            projects.append(project)
        return projects

    def start(self, command: str) -> Context:
        message = getattr(self.args, "message", None)
        runLog = RunLog(self.config.logDir, command, sys.argv[1:], message, self.args.dryRun)
        self.ctx = Context(
            self.console,
            runLog,
            self.config,
            dryRun=self.args.dryRun,
            verbose=self.args.verbose,
            force=self.args.force,
            jobs=self.args.jobs,
            interactive=self.args.interactive,
        )
        badge = "  <warning>DRY RUN</warning>" if self.args.dryRun else ""
        self.console.write(f"<highlight>slop</highlight> <dim>{VERSION}</dim>  <bold>{command}</bold>{badge}")
        if message:
            self.console.write(f"<dim>»</dim> {message}")
        if runLog.error:
            self.console.warning(runLog.error)
        if self.pulled:
            runLog.event(
                logging.INFO, "config pulled", event="config.pull", before=self.pulled[0], after=self.pulled[1]
            )
        return self.ctx

    def header(self, project: Project) -> None:
        root = (
            project.root.relative_to(Path.cwd()).as_posix()
            if project.root.is_relative_to(Path.cwd())
            else str(project.root)
        )
        self.console.write()
        self.console.write(
            f"<highlight>▶ {project.name}</highlight> <dim>→</dim> <info>{root}</info> <dim>·</dim> {project.summary()}"
        )

    def runPlans(self, plans: list[Plan]) -> None:
        self.guard()
        ctx = self.ctx
        resolved = [
            (project, [project.module(n) for n in pipeline], scope, explicit)
            for project, pipeline, scope, explicit in plans
        ]
        for project, modules, scope, explicit in resolved:
            ctx.project, ctx.scope, ctx.explicit, ctx.module = project, scope, explicit, None
            self.header(project)
            ctx.event(
                logging.INFO,
                f"Project {project.name}: {', '.join(m.name for m in modules)}",
                event="project.start",
                root=str(project.root),
                scope=sorted(p.as_posix() for p in scope) if scope is not None else None,
            )
            for module in modules:
                self.step(project.name, module.name, module.title, lambda m=module: m(ctx).run())
            ctx.module = None

    def step(self, project: str, name: str, title: str, action) -> None:
        ctx = self.ctx
        ctx.module = name
        self.console.section(title)
        started = time.monotonic()
        ctx.event(logging.INFO, title, event="module.start")
        action()
        took = time.monotonic() - started
        self.timings.append((project, name, took))
        ctx.event(logging.INFO, f"{title}: done", event="module.finish", duration=round(took, 3))
        ctx.module = None

    def fail(self, error: BaseException) -> None:
        ctx = self.ctx
        if ctx is None:
            return
        if isinstance(error, SlopError):
            ctx.event(logging.ERROR, str(error), event="error")
            ctx.runLog.finish(False, str(error))
        elif isinstance(error, KeyboardInterrupt):
            ctx.runLog.finish(False, "interrupted")
        else:
            ctx.event(logging.CRITICAL, f"{type(error).__name__}: {error}", event="crash")
            ctx.runLog.finish(False, f"{type(error).__name__}: {error}")

    def finish(self) -> float:
        ctx = self.ctx
        seconds = ctx.runLog.finish(True)
        done = "Dry run finished" if self.args.dryRun else "Done"
        self.console.write()
        self.console.write(f"<success>✔ {done}</success> <dim>in</dim> <bold>{Duration.format(seconds)}</bold>")
        projects = list(dict.fromkeys(p for p, _, _ in self.timings))
        width = max(map(len, projects), default=0)
        for project in projects:
            steps = [f"{m} <comment>{Duration.format(s)}</comment>" for p, m, s in self.timings if p == project]
            self.console.write(f"<dim>  ⏱ {project:<{width}}</dim>  " + " <dim>·</dim> ".join(steps))
        if ctx.runLog.file:
            self.console.write(f"<dim>  log: slop log {ctx.runLog.runId}</dim>")
        return seconds

    def run(self, command: str, plans: list[Plan]) -> None:
        for project, pipeline, _, _ in plans:
            for name in pipeline:
                project.module(name)
        self.start(command)
        try:
            self.runPlans(plans)
        except BaseException as e:
            self.fail(e)
            raise
        self.finish()

    def publishConfig(self, project: Project, subject: str, tasks: list[str] | None = None) -> None:
        config = self.config
        repo = config.repo
        if self.args.dryRun or not repo.isRepo or not config.data.get("config-repo", {}).get("commit-edits", True):
            return
        body = f"\n\n{self.args.message}" if getattr(self.args, "message", None) else ""
        footer = "".join(f"\nBug: {t}" for t in tasks or [])
        commit = repo.commit(f"{subject}{body}{footer}", project.configPaths())
        if not commit:
            return
        self.console.write(f"  <dim>config:</dim> committed <comment>{commit[:7]}</comment> {subject}")
        if not config.push or not repo.hasRemote():
            return
        try:
            repo.push()
        except SlopError as e:
            raise SlopError(
                f"committed {commit[:7]} in the config repo but could not push it; run `slop config push` to retry:\n{e}"
            ) from None
        self.console.write(f"  <dim>config:</dim> pushed to {repo.remote}/{repo.branch}")

    def syncConfig(self) -> None:
        if getattr(self.args, "noPull", False) or not self.config.pull or self.args.dryRun:
            return
        repo = self.config.repo
        if not repo.hasRemote():
            return
        before, after = repo.pull()
        if before != after:
            self.pulled = (before, after)
            self.console.write(
                f"<dim>config:</dim> pulled <comment>{(before or '')[:7]}..{(after or '')[:7]}</comment> "
                f"from {repo.remote}/{repo.branch}"
            )
            self.reload()
