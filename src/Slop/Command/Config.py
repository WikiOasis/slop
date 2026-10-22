import argparse
import subprocess

from Slop.Command.Command import Command
from Slop.Command.Reader import Reader
from Slop.Config.ConfigLoader import ConfigLoader
from Slop.Output.Console import Console
from Slop.Runner import Runner
from Slop.SlopError import SlopError


class Config(Command):
    name = "config"
    help = "work with the config repo: status, pull, push, commit, diff, log, validate"
    forward = "noconfig"

    def configure(self, parser: argparse.ArgumentParser) -> None:
        parser.formatter_class = argparse.RawDescriptionHelpFormatter
        parser.description = self.help + (
            "\n\nThe config repo is pulled before every command. Changes made by `slop repo` and `slop patch` are "
            "committed and pushed straight away, and a successful deploy commits and pushes its state, so you can "
            "also edit it in your IDE and push.\n\n"
            "examples:\n  slop config status\n  slop config pull\n  slop config commit -m 'Add inventory host'\n"
            "  slop config validate        what CI runs"
        )
        parser.add_argument("action", choices=["status", "pull", "push", "commit", "diff", "log", "validate", "path"])
        Runner.addMinimalOptions(parser)

    def run(self, args: argparse.Namespace, console: Console) -> int | None:
        if args.action == "validate":
            return self.validate(args, console)
        if args.action == "path":
            console.write(str(ConfigLoader(args.config).configDir))
            return None
        config = Reader.config(args, pull=False)
        repo = config.repo
        if not repo.isRepo:
            raise SlopError(f"{config.configDir} is not a git repo")
        if args.action == "status":
            branch = repo.currentBranch() or "(detached)"
            console.write(
                f"<comment>config</comment> {config.configDir} <dim>on</dim> {branch} <dim>at</dim> {(repo.head() or '')[:10]}"
            )
            if repo.hasRemote():
                repo.git("fetch", "--quiet", repo.remote, repo.branch, check=False)
                counts = repo.git(
                    "rev-list", "--left-right", "--count", f"HEAD...{repo.remote}/{repo.branch}", check=False
                ).stdout.split()
                if len(counts) == 2:
                    console.write(
                        f"  ahead <comment>{counts[0]}</comment>, behind <comment>{counts[1]}</comment> {repo.remote}/{repo.branch}"
                    )
            changes = repo.changes()
            console.write(
                f"  {len(changes)} uncommitted change(s)"
                + (" <dim>(committed by the next successful deploy or `slop config commit`)</dim>" if changes else "")
            )
            for line in changes:
                console.write(f"    <dim>{line}</dim>")
            for project in config.projects.values():
                history = repo.history(project.stateFile().relative, 1)
                last = (
                    f"{history[0]['commit'][:7]} {history[0]['date'][:16]} {history[0]['subject']}"
                    if history
                    else "never deployed"
                )
                console.write(f"  <info>{project.name}</info> last deploy: <dim>{last}</dim>")
            return None
        if args.action == "diff":
            return subprocess.call(["git", "-C", str(config.configDir), "diff", "HEAD"])
        if args.action == "log":
            return subprocess.call(["git", "-C", str(config.configDir), "log", "--oneline", "--decorate", "-n", "30"])

        runner = Runner(args, console)
        if args.action == "pull":
            runner.syncConfig()
            if not runner.pulled:
                console.write("<dim>config is up to date</dim>")
            return None
        if args.action == "commit":
            if not args.message:
                raise SlopError("give a commit message with -m")
            commit = repo.commit(args.message, ["."])
            if not commit:
                console.write("<dim>nothing to commit</dim>")
                return None
            console.write(f"committed <comment>{commit[:10]}</comment>")
            if config.push:
                repo.push()
                console.write(f"pushed to {repo.remote}/{repo.branch}")
            return None
        if args.action == "push":
            repo.push()
            console.write(f"pushed to {repo.remote}/{repo.branch}")
        return None

    def validate(self, args: argparse.Namespace, console: Console) -> int:
        config = ConfigLoader(args.config).load(root=getattr(args, "root", None))
        problems = []
        for project in config.projects.values():
            for role in project.deploy.roles:
                if role not in config.inventory.roles:
                    problems.append(f"{project.name}: deploy role {role!r} is not in inventory.yaml")
            checkouts = project.checkouts()
            keys = [c.key for c in checkouts]
            for key in {k for k in keys if keys.count(k) > 1}:
                problems.append(f"{project.name}: {key} is defined twice")
            for checkout in checkouts:
                for patch in checkout.patches:
                    if patch.kind == "gerrit" and not patch.patchset:
                        problems.append(f"{project.name}: {patch.name} has no patch set")
            state = project.stateFile().load()
            if state and state.get("project") not in (None, project.name):
                problems.append(f"{project.stateFile().relative} belongs to {state.get('project')}")
        for role in config.inventory.roles.values():
            if not role.hosts:
                console.write(f"  <warning>⚠</warning> inventory: role {role.name} has no hosts")
        if problems:
            for problem in problems:
                console.write(f"  <error>✖</error> {problem}")
            return 1
        patchCount = sum(len(c.patches) for p in config.projects.values() for c in p.checkouts())
        repoCount = sum(len(p.checkouts()) for p in config.projects.values())
        console.write(
            f"<success>✔</success> config is valid: {len(config.projects)} project(s), {repoCount} repos, "
            f"{patchCount} patches, {len(config.inventory.roles)} roles"
        )
        return 0
