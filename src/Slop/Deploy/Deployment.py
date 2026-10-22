import argparse
import json
import logging
import shutil

from Slop.Config.Config import Config
from Slop.Config.StateFile import StateFile
from Slop.Context import Context
from Slop.Deploy.DeployFailure import DeployFailure
from Slop.Deploy.Lock import Lock
from Slop.Deploy.Rollout import Rollout
from Slop.Deploy.StateDiff import StateDiff
from Slop.Git.Git import Git
from Slop.Log.Invoker import Invoker
from Slop.Notify.Notifier import Notifier
from Slop.Output.Duration import Duration
from Slop.Project.Project import Project
from Slop.Runner import Runner
from Slop.Sal.Sal import Sal
from Slop.SlopError import SlopError


class Deployment:
    def __init__(self, runner: Runner, args: argparse.Namespace):
        self.runner = runner
        self.args = args
        self.begun = False
        self.reported = False

    @property
    def ctx(self) -> Context:
        return self.runner.ctx

    @property
    def config(self) -> Config:
        return self.runner.config

    @property
    def message(self) -> str:
        return (getattr(self.args, "message", None) or "").strip()

    def tasks(self, diff: StateDiff | None = None) -> list[str]:
        found = Sal.tasks(self.message, *(getattr(self.args, "tasks", None) or []))
        for task in diff.tasks() if diff else []:
            if task not in found:
                found.append(task)
        return found

    def sal(self) -> Sal:
        return Sal(self.ctx, self.config.sal)

    def announce(self, text: str, ok: bool = True) -> None:
        text = f"{text} (run {self.ctx.runLog.runId})" if self.ctx.runLog.file else text
        self.sal().log(text)
        Notifier(self.ctx, self.config.notify).send(text, ok)

    def begin(self, text: str) -> None:
        self.begun = True
        self.announce(text)

    def deploy(self, projectName: str | None, what: str | None, names: list[str]) -> None:
        runner = self.runner
        project = runner.projects([projectName] if projectName else runner.config.defaults[:1])[0]
        label = "deploy" + (f" {what}" if what else "") + (f" {' '.join(names)}" if names else "")
        runner.start(f"{label} ({project.name})")
        try:
            with self.locks().acquire(project.name, self.ctx.runLog.runId, self.message):
                self.pull(project.name)
                project = runner.projects([project.name])[0]
                self.ctx.config = self.config
                self.deployLocked(project, what, names)
        except BaseException as e:
            self.abandon(project.name, "deploy", e)
            runner.fail(e)
            raise
        runner.finish()

    def abandon(self, project: str, what: str, error: BaseException) -> None:
        if not self.begun or self.reported:
            return
        reason = str(error).splitlines()[0] if isinstance(error, SlopError) and str(error) else type(error).__name__
        self.reported = True
        self.announce(f"{what.capitalize()} of {project} aborted: {reason}", ok=False)

    def locks(self) -> Lock:
        return Lock(self.config.lockDir, self.config.repo, self.config.push)

    def pull(self, project: str) -> None:
        repo = self.config.repo
        if repo.hasRemote():
            try:
                before, after = repo.pull()
            except SlopError as e:
                raise SlopError(
                    f"cannot pull the config repo, so locks and config cannot be checked; not deploying:\n{e}"
                ) from None
            if before != after:
                self.ctx.log(f"config: pulled <comment>{(before or '')[:7]}..{(after or '')[:7]}</comment>")
                self.ctx.event(logging.INFO, "config pulled", event="config.pull", before=before, after=after)
                self.runner.reload()
        elif self.config.pull and repo.isRepo:
            self.ctx.warn("the config repo has no remote, so locks from other hosts cannot be seen")
        self.ctx.config = self.config
        self.locks().checkHeld(project)

    def selection(self, project: Project, what: str | None, names: list[str]):
        if not what or what == "all":
            if names:
                raise SlopError("give something to deploy before names, e.g. `slop deploy extensions CheckUser`")
            return project.stage("build"), None, None, set()
        what = project.ALIASES.get(what, what)
        if what not in project.TARGETS:
            raise SlopError(f"{project.type} projects can't deploy {what!r}; try one of: {', '.join(project.TARGETS)}")
        modules, takesNames, _ = project.TARGETS[what]
        if names and not takesNames:
            raise SlopError(f"`slop deploy {what}` doesn't take names")
        scope = project.scope(what, names)
        explicit = set(modules) if modules and len(modules) == 1 else set()
        return modules or project.stage("build"), scope, project.syncPaths([what]), explicit

    def lastDeploy(self, project: Project) -> tuple[dict, str | None]:
        stateFile = project.stateFile()
        repo = self.config.repo
        if repo.isRepo:
            history = repo.history(stateFile.relative, 1)
            if history:
                rev = history[0]["commit"]
                return StateFile.parse(repo.show(rev, stateFile.relative), f"{rev}:{stateFile.relative}"), rev
            return {}, None
        return stateFile.load(), None

    def deployLocked(self, project: Project, what: str | None, names: list[str]) -> None:
        ctx = self.ctx
        modules, scope, paths, explicit = self.selection(project, what, names)
        previous, previousRev = self.lastDeploy(project)
        stateFile = project.stateFile()
        ctx.state, ctx.stateDir, ctx.pinned = previous, stateFile.filesDir, False

        if not self.args.skipBuild:
            self.runner.runPlans([(project, modules, scope, explicit)])
        else:
            self.runner.header(project)

        git = Git(ctx)
        manifest = project.manifest(git)
        newRepos = self.merge(previous.get("repos") or {}, manifest, paths)
        diff = StateDiff.compare(previous.get("repos") or {}, newRepos)

        self.runner.step(
            project.name, "plan", "Changes since the last deploy", lambda: diff.show(ctx.console, git, project.root)
        )
        if diff.empty and not self.args.resync:
            ctx.console.write()
            ctx.success("nothing to deploy (pass --resync to push the current build anyway)")
            return

        rollout = Rollout(
            ctx,
            project,
            hosts=self.args.hosts,
            roles=self.args.roles,
            batch=self.args.batch,
            paths=paths,
            changed=diff.keys if not self.args.resync else set(newRepos),
        )
        ctx.console.section("Rollout plan")
        rollout.show()
        if paths:
            ctx.console.write(f"  <dim>syncing only: {', '.join(paths)}</dim>")
        if previousRev is None:
            ctx.warn(
                f"{project.name} has no deploy recorded in the config repo yet, so a failure cannot be rolled back automatically"
            )
        self.confirm(f"Deploy {project.name} to {len(rollout.hosts)} hosts?")

        self.runner.step(project.name, "lint", "Linting the build", rollout.lint)
        self.runner.step(project.name, "preflight", "Checking hosts", rollout.preflight)
        self.runner.step(project.name, "locks", "Re-checking locks", lambda: self.locks().checkRemote(project.name))
        tasks = self.tasks(diff)
        self.begin(
            f"Deploying {project.name}"
            + (f" {what}" if what and what != "all" else "")
            + (f" {' '.join(names)}" if names else "")
            + (f": {self.message}" if self.message else "")
            + (f" ({' '.join(tasks)})" if tasks else "")
            + f" [{diff.summary(git, project.root) or 'resync'}] to {len(rollout.hosts)} hosts"
        )
        self.writeMarker(project, newRepos, previousRev, tasks)
        try:
            self.runner.step(project.name, "rollout", "Rolling out", rollout.run)
        except DeployFailure as failure:
            self.failed(project, failure, rollout, previous, previousRev, diff, tasks)
            raise

        state = self.record(project, previous, newRepos, tasks, extra={"config": previousRev})
        commit = self.commit(
            project, state, f"Deploy {project.name}" + (f": {self.message}" if self.message else ""), diff, tasks
        )
        self.keepDeployed(project, git, newRepos)
        summary = diff.summary(git, project.root) or "resync, no code changes"
        hosts = len(rollout.done)
        text = (
            f"Deployed {project.name}"
            + (f" {what}" if what and what != "all" else "")
            + (f" {' '.join(names)}" if names else "")
        )
        text += f": {self.message}" if self.message else ""
        text += f" ({' '.join(tasks)})" if tasks else ""
        text += f" [{summary}] to {hosts} host{'s' if hosts != 1 else ''} in {Duration.format(ctx.runLog.elapsed)}"
        text += f", config {commit[:7]}" if commit else ""
        self.announce(text)

    def merge(self, previous: dict, manifest: dict, paths: list[str] | None) -> dict:
        if paths is None:
            return dict(manifest)
        merged = dict(previous)

        def inside(key: str) -> bool:
            return any(key == p or key.startswith(p + "/") for p in paths)

        for key in [k for k in merged if inside(k) and k not in manifest]:
            del merged[key]
        for key, entry in manifest.items():
            if inside(key):
                merged[key] = entry
        return merged

    def confirm(self, question: str) -> None:
        if self.ctx.dryRun or self.args.yes or not self.ctx.interactive:
            return
        if not self.ctx.console.confirm(question, False):
            raise SlopError("cancelled; nothing was deployed")

    def writeMarker(self, project: Project, repos: dict, configRev: str | None, tasks: list[str]) -> None:
        if self.ctx.dryRun:
            return
        marker = {
            "project": project.name,
            "run": self.ctx.runLog.runId,
            "at": StateFile.now(),
            "by": Invoker.label(),
            "message": self.message,
            "tasks": tasks,
            "config": configRev,
            "repos": {k: {"base": v.get("base"), "head": v.get("head")} for k, v in repos.items()},
        }
        (project.root / Project.STATE_FILE).write_text(json.dumps(marker, indent=1) + "\n")

    def record(
        self, project: Project, previous: dict, repos: dict, tasks: list[str], extra: dict | None = None
    ) -> dict:
        stateFile = project.stateFile()
        composer = {}
        if not self.ctx.dryRun:
            stateFile.filesDir.mkdir(parents=True, exist_ok=True)
            for name, source in project.lockfiles().items():
                if source.is_file():
                    shutil.copy2(source, stateFile.lockFile(name))
                    stamp = source.parent / "vendor" / ".slop-composer"
                    try:
                        inputs = json.loads(stamp.read_text()).get("inputs")
                    except (OSError, ValueError, AttributeError):
                        inputs = None
                    composer[name.removeprefix("composer-").removesuffix(".lock")] = {"inputs": inputs}
        state = {
            "project": project.name,
            "run": self.ctx.runLog.runId,
            "deployed-at": StateFile.now(),
            "deployed-by": Invoker.label(),
            "message": self.message or None,
            "tasks": tasks or None,
            **(extra or {}),
            "composer": composer or previous.get("composer") or None,
            "repos": repos,
        }
        state = {k: v for k, v in state.items() if v is not None}
        if not self.ctx.dryRun:
            stateFile.save(state)
        return state

    def commit(
        self, project: Project, state: dict, subject: str, diff: StateDiff | None, tasks: list[str]
    ) -> str | None:
        repo = self.config.repo
        if self.ctx.dryRun or not repo.isRepo:
            return None
        body = [subject, ""]
        if diff is not None and not diff.empty:
            body += [diff.summary(limit=4000), ""]
        if tasks:
            body.append(f"Bug: {', '.join(tasks)}")
        body += [f"Run: {state['run']}", f"Deployed-by: {state['deployed-by']}"]
        commit = repo.commit("\n".join(body), project.configPaths())
        if commit and self.config.push:
            try:
                repo.push()
                self.ctx.success(f"committed and pushed config <comment>{commit[:7]}</comment>")
            except SlopError as e:
                self.ctx.warn(f"committed config {commit[:7]} but could not push it; run `slop config push`\n{e}")
        elif commit:
            self.ctx.success(f"committed config <comment>{commit[:7]}</comment>")
        return commit or repo.head()

    def keepDeployed(self, project: Project, git: Git, repos: dict) -> None:
        for checkout in project.checkouts():
            entry = repos.get(checkout.key)
            if entry and (checkout.dest / ".git").exists():
                git.keep(checkout.dest, "current", entry.get("base"))

    def failed(
        self,
        project: Project,
        failure: DeployFailure,
        rollout: Rollout,
        previous: dict,
        previousRev: str | None,
        diff: StateDiff,
        tasks: list[str],
    ) -> None:
        ctx = self.ctx
        ctx.console.errorBlock("Deploy failed", str(failure))
        taskText = f" ({' '.join(tasks)})" if tasks else ""
        base = (
            f"Deploy of {project.name} FAILED on {failure.host} at {failure.step}"
            + (f": {self.message}" if self.message else "")
            + taskText
        )
        touched = sorted(failure.touched | rollout.touched)

        self.reported = True
        self.announce(f"{base}: {failure.error.splitlines()[0][:300] if failure.error else 'unknown error'}", ok=False)

        if self.args.noRollback or previousRev is None or not touched or ctx.dryRun:
            done = Rollout.hostsLabel(touched) or "none"
            self.announce(f"Deploy of {project.name} ended without a rollback, hosts already changed: {done}", ok=False)
            return

        ctx.console.section(f"Rolling {Rollout.hostsLabel(touched)} back to config {previousRev[:7]}")
        try:
            self.rebuildAndSync(project.name, previousRev, previous, touched)
        except SlopError as e:
            self.announce(
                f"Deploy of {project.name} ended: ROLLBACK to {previousRev[:7]} ALSO FAILED: {str(e).splitlines()[0]}",
                ok=False,
            )
            raise SlopError(f"{failure}\n\nrollback to {previousRev[:7]} also failed: {e}") from None
        self.writeMarker(project, previous.get("repos") or {}, previousRev, tasks)
        self.announce(
            f"Deploy of {project.name} ended: rolled {Rollout.hostsLabel(touched)} back to {previousRev[:7]} "
            f"in {Duration.format(ctx.runLog.elapsed)}",
            ok=False,
        )

    def rebuildAndSync(self, projectName: str, rev: str, state: dict, hosts: list[str] | None) -> Rollout:
        ctx = self.ctx
        repo = self.config.repo
        exported = repo.export(rev)
        saved = (ctx.state, ctx.stateDir, ctx.pinned, ctx.force, ctx.scope, ctx.explicit)
        try:
            exportedConfig = self.runner.load(exported)
            project = exportedConfig.project(projectName)
            project.applyOptions(self.args)
            ctx.state, ctx.stateDir, ctx.pinned, ctx.force = (
                state,
                StateFile(exported, projectName).filesDir,
                True,
                True,
            )
            self.runner.runPlans([(project, project.stage("build"), None, set())])
            rollout = Rollout(
                ctx, project, hosts=hosts, batch=self.args.batch, changed=set(state.get("repos") or {}), checksum=True
            )
            self.runner.step(project.name, "rollback", "Re-syncing the previous build", rollout.run)
            return rollout
        finally:
            ctx.state, ctx.stateDir, ctx.pinned, ctx.force, ctx.scope, ctx.explicit = saved
            shutil.rmtree(exported, ignore_errors=True)

    def rollback(self, projectName: str | None, rev: str | None) -> None:
        runner = self.runner
        project = runner.projects([projectName] if projectName else runner.config.defaults[:1])[0]
        runner.start(f"rollback ({project.name})")
        try:
            with self.locks().acquire(project.name, self.ctx.runLog.runId, self.message):
                self.pull(project.name)
                project = runner.projects([project.name])[0]
                self.rollbackLocked(project, rev)
        except BaseException as e:
            self.abandon(project.name, "rollback", e)
            runner.fail(e)
            raise
        runner.finish()

    def rollbackLocked(self, project: Project, rev: str | None) -> None:
        ctx = self.ctx
        repo = self.config.repo
        if not repo.isRepo:
            raise SlopError(
                f"{self.config.configDir} is not a git repo, so there are no previous deploys to roll back to"
            )
        stateFile = project.stateFile()
        history = repo.history(stateFile.relative, 50)
        if not history:
            raise SlopError(f"no deploys of {project.name} are recorded in the config repo")
        current = StateFile.parse(repo.show(history[0]["commit"], stateFile.relative), stateFile.relative)
        if rev is None:
            if len(history) < 2:
                raise SlopError(f"{project.name} has only been deployed once; there is nothing to roll back to")
            target = history[1]["commit"]
        else:
            target = repo.resolve(rev)
        targetState = StateFile.parse(repo.show(target, stateFile.relative), f"{target[:7]}:{stateFile.relative}")
        if not targetState.get("repos"):
            raise SlopError(f"config {target[:7]} has no recorded state for {project.name}")

        dirty = repo.changes([p for p in project.configPaths()])
        if dirty and not ctx.force:
            raise SlopError(
                f"the config has uncommitted changes for {project.name}; rolling back would discard them "
                "(commit them with `slop config commit`, or pass --force):\n" + "\n".join(dirty)
            )

        git = Git(ctx)
        diff = StateDiff.compare(current.get("repos") or {}, targetState["repos"])
        ctx.console.section(
            f"Rolling back to config {target[:7]} (deployed {targetState.get('deployed-at', '?')} by {targetState.get('deployed-by', '?')})"
        )
        if targetState.get("message"):
            ctx.console.write(f"  <dim>»</dim> {targetState['message']}")
        diff.show(ctx.console, git, project.root)
        rollout = Rollout(ctx, project, hosts=self.args.hosts, roles=self.args.roles, batch=self.args.batch)
        ctx.console.section("Rollout plan")
        rollout.show()
        self.confirm(f"Roll {project.name} back on {len(rollout.hosts)} hosts?")
        self.runner.step(project.name, "preflight", "Checking hosts", rollout.preflight)
        self.runner.step(project.name, "locks", "Re-checking locks", lambda: self.locks().checkRemote(project.name))

        tasks = self.tasks()
        self.begin(
            f"Rolling back {project.name} to config {target[:7]}"
            + (f": {self.message}" if self.message else "")
            + (f" ({' '.join(tasks)})" if tasks else "")
            + f" on {len(rollout.hosts)} hosts"
        )
        try:
            done = self.rebuildAndSync(project.name, target, targetState, rollout.hosts)
        except SlopError as e:
            self.announce(f"Rollback of {project.name} to {target[:7]} FAILED: {str(e).splitlines()[0]}", ok=False)
            raise
        if ctx.dryRun:
            return

        existing = [
            p
            for p in project.configPaths()
            if repo.git("ls-tree", "--name-only", target, "--", p).stdout.strip() or repo.tracked(p)
        ]
        if existing:
            repo.restore(target, existing)
            repo.git("clean", "-fdq", "--", *existing)
        self.runner.reload()
        project = self.config.project(project.name)
        self.writeMarker(project, targetState["repos"], target, tasks)
        state = self.record(
            project,
            targetState,
            targetState["repos"],
            tasks,
            extra={"config": target, "rollback-of": history[0]["commit"]},
        )
        commit = self.commit(
            project,
            state,
            f"Roll back {project.name} to {target[:7]}" + (f": {self.message}" if self.message else ""),
            diff,
            tasks,
        )
        text = f"Rolled back {project.name} to config {target[:7]}" + (f": {self.message}" if self.message else "")
        text += f" ({' '.join(tasks)})" if tasks else ""
        text += f" [{diff.summary() or 'no code changes'}] on {len(done.done)} host{'s' if len(done.done) != 1 else ''} in {Duration.format(ctx.runLog.elapsed)}"
        text += f", config {commit[:7]}" if commit else ""
        self.announce(text)

    @staticmethod
    def addOptions(parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "-t",
            "--task",
            dest="tasks",
            action="append",
            default=[],
            metavar="T123",
            help="Phorge task this deploy is for (repeatable); also found in -m",
        )
        parser.add_argument("--hosts", type=Deployment.csv, metavar="H[,H...]", help="only these hosts")
        parser.add_argument("--role", dest="roles", type=Deployment.csv, metavar="R[,R...]", help="only these roles")
        parser.add_argument("--batch", type=int, help="hosts at a time, overriding each role's batch size")
        parser.add_argument("-y", "--yes", action="store_true", help="don't ask for confirmation")
        Runner.addOptions(parser)

    @staticmethod
    def csv(value: str) -> list[str]:
        return [x for x in value.split(",") if x]
