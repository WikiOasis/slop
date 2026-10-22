import argparse
import subprocess
from pathlib import Path

from Slop.Command.Command import Command
from Slop.Command.Reader import Reader
from Slop.Deploy.StateDiff import StateDiff
from Slop.Git.Git import Git
from Slop.Output.Console import Console
from Slop.Project.Checkout import Checkout
from Slop.Project.ProjectLoader import ProjectLoader
from Slop.Runner import Runner
from Slop.SlopError import SlopError


class Repo(Command):
    name = "repo"
    help = "list repos, pin them to commits, switch branches, add, remove or import extensions and skins"
    forward = "noconfig"

    def configure(self, parser: argparse.ArgumentParser) -> None:
        parser.formatter_class = argparse.RawDescriptionHelpFormatter
        parser.description = self.help + (
            "\n\nChanges are written to the config repo, committed and pushed straight away; deploying is separate.\n\n"
            "examples:\n"
            "  slop repo list\n"
            "  slop repo pin CheckUser 3f2a1bc -t T123     check out an exact commit\n"
            "  slop repo pin core 1.46.0                      a tag works too\n"
            "  slop repo unpin CheckUser\n"
            "  slop repo branch WikiOasisMagic feature/x\n"
            "  slop repo add extensions/Cargo --url https://github.com/wikimedia/mediawiki-extensions-Cargo\n"
            "  slop repo remove Cargo\n"
            "  slop repo import /srv/mediawiki-staging/1.46 --mw-version 1.46"
        )
        parser.add_argument("action", choices=["list", "pin", "unpin", "branch", "add", "remove", "import"])
        parser.add_argument("args", nargs="*", metavar="ARG")
        parser.add_argument("-p", "--project", help="project (default: slop.yaml's default)")
        parser.add_argument("-t", "--task", help="Phorge task the change is for, recorded with it")
        parser.add_argument("--url", help="repo url for add")
        parser.add_argument("--branch", help="branch for add (default: the version's release branch)")
        parser.add_argument("--no-pull", dest="noPull", action="store_true", help="don't pull the config repo first")
        Runner.addMinimalOptions(parser)
        for projectType in ProjectLoader.all().values():
            projectType.addOptions(parser)

    def run(self, args: argparse.Namespace, console: Console) -> None:
        if args.action == "list":
            return self.list(args, console)
        runner = Runner(args, console)
        runner.syncConfig()
        project = runner.projects([args.project] if args.project else runner.config.defaults[:1])[0]
        runner.start(f"repo {args.action} {' '.join(args.args)}".strip())
        try:
            getattr(self, "do" + args.action.capitalize())(runner, project, args)
            runner.publishConfig(
                project,
                f"slop repo {args.action} {' '.join(args.args)}".strip(),
                [args.task] if getattr(args, "task", None) else None,
            )
        except BaseException as e:
            runner.fail(e)
            raise
        runner.finish()

    def need(self, args: argparse.Namespace, count: int, usage: str) -> list[str]:
        if len(args.args) != count:
            raise SlopError(f"usage: slop repo {args.action} {usage}")
        return args.args

    def doPin(self, runner: Runner, project, args) -> None:
        name, rev = self.need(args, 2, "NAME REV")
        git = Git(runner.ctx)
        for checkout in project.select(name):
            self.requireCheckout(checkout)
            sha = git.resolveRemote(checkout.dest, rev, checkout.repo.branch)
            subject = git.git(checkout.dest, "log", "-1", "--format=%s", sha, readOnly=True).stdout.strip()
            file = project.setRepoField(checkout, {"commit": sha, "task": args.task})
            runner.ctx.success(
                f"pinned <info>{StateDiff.label(checkout.key)}</info> to <comment>{sha[:10]}</comment> {subject} <dim>({file.name})</dim>"
            )
        self.hint(runner, project, name)

    def doUnpin(self, runner: Runner, project, args) -> None:
        (name,) = self.need(args, 1, "NAME")
        for checkout in project.select(name):
            file = project.setRepoField(checkout, {"commit": None, "task": None})
            runner.ctx.success(
                f"unpinned <info>{StateDiff.label(checkout.key)}</info>; it follows {checkout.repo.branch} again <dim>({file.name})</dim>"
            )
        self.hint(runner, project, name)

    def doBranch(self, runner: Runner, project, args) -> None:
        name, branch = self.need(args, 2, "NAME BRANCH")
        for checkout in project.select(name):
            remote = subprocess.run(
                ["git", "ls-remote", "--heads", checkout.repo.url, branch], check=False, capture_output=True, text=True
            )
            if not remote.stdout.strip():
                raise SlopError(f"{checkout.repo.url} has no branch {branch!r}")
            file = project.setRepoField(checkout, {"branch": branch, "commit": None})
            runner.ctx.success(
                f"<info>{StateDiff.label(checkout.key)}</info> now follows <comment>{branch}</comment> <dim>({file.name})</dim>"
            )
        self.hint(runner, project, name)

    def doAdd(self, runner: Runner, project, args) -> None:
        (path,) = self.need(args, 1, "extensions/NAME|skins/NAME [--url URL] [--branch BRANCH]")
        if not path.startswith(("extensions/", "skins/")):
            raise SlopError("give the path, e.g. extensions/Cargo or skins/Citizen")
        if not hasattr(project, "addRepo"):
            raise SlopError(f"{project.type} projects have a single repo")
        entry = {"path": path}
        if args.url:
            entry["url"] = args.url
        entry["branch"] = args.branch or "_version_"
        if args.task:
            entry["task"] = args.task
        for version in project.versions:
            file = project.addRepo(version, entry)
            runner.ctx.success(f"added <info>{path}</info> to {file.name}")
        self.hint(runner, project, path.split("/")[-1])

    def doRemove(self, runner: Runner, project, args) -> None:
        (name,) = self.need(args, 1, "NAME")
        if not hasattr(project, "removeRepo"):
            raise SlopError(f"{project.type} projects have a single repo")
        for checkout in project.select(name):
            if checkout.scope in (None, Path(".")):
                raise SlopError("core and mw-config cannot be removed")
            if checkout.patches:
                raise SlopError(f"{checkout.key} still has patches: {', '.join(p.name for p in checkout.patches)}")
            file = project.removeRepo(checkout)
            runner.ctx.success(f"removed <info>{checkout.repo.path}</info> from {file.name}")
        runner.ctx.console.write(
            "  <dim>the checkout stays in the build tree until you delete it; deploy to drop it from hosts</dim>"
        )

    def doImport(self, runner: Runner, project, args) -> None:
        (tree,) = self.need(args, 1, "VERSION_DIR")
        if not hasattr(project, "addRepo") or len(project.versions) != 1:
            raise SlopError("pick one MediaWiki version with --mw-version")
        version = project.versions[0]
        root = Path(tree)
        known = {r.path for r in version.repos}
        added = 0
        for kind in ("extensions", "skins"):
            for directory in sorted((root / kind).iterdir()) if (root / kind).is_dir() else []:
                rel = Path(kind) / directory.name
                if rel in known or not (directory / ".git").exists():
                    continue
                url = self.gitOut(directory, "remote", "get-url", "origin")
                if not url:
                    runner.ctx.warn(f"{rel}: no origin remote, skipped")
                    continue
                branch = self.gitOut(directory, "rev-parse", "--abbrev-ref", "HEAD")
                if not branch or branch == "HEAD":
                    branch = "_version_"
                relBranch = "REL" + version.name.replace(".", "_")
                entry = {"path": rel.as_posix(), "url": url, "branch": "_version_" if branch == relBranch else branch}
                project.addRepo(version, entry)
                runner.ctx.item("Imported", rel.as_posix(), f"{url} {entry['branch']}")
                added += 1
        runner.ctx.success(f"imported <info>{added}</info> repos into repos-{version.name}.yaml")

    @staticmethod
    def gitOut(directory: Path, *args: str) -> str:
        proc = subprocess.run(["git", "-C", str(directory), *args], check=False, capture_output=True, text=True)
        return proc.stdout.strip() if proc.returncode == 0 else ""

    @staticmethod
    def requireCheckout(checkout: Checkout) -> None:
        if not (checkout.dest / ".git").exists():
            raise SlopError(f"{checkout.key} is not built yet; run `slop build` first")

    @staticmethod
    def hint(runner: Runner, project, name: str) -> None:
        target = ""
        if project.type == "mediawiki":
            target = f"{name} " if name in ("core", "config") else f"extensions {name} "
        runner.ctx.console.write(
            f"  <dim>deploy it with `slop deploy {target}-m ...`</dim>"
        )

    def list(self, args: argparse.Namespace, console: Console) -> None:
        config = Reader.config(args)
        project = config.project(args.project or config.defaults[0])
        project.applyOptions(args)
        ctx = Reader.context(args, console, config)
        ctx.project = project
        git = Git(ctx)
        deployed = project.stateFile().load().get("repos") or {}
        checkouts = project.selectable()
        width = max((len(StateDiff.label(c.key)) for c in checkouts), default=0)
        console.write(f"<comment>{project.name}</comment> repos <dim>(built / deployed)</dim>")
        for checkout in checkouts:
            built = git.describe(checkout.dest).get("base") if (checkout.dest / ".git").exists() else None
            live = (deployed.get(checkout.key) or {}).get("base")
            mark = (
                "<success>=</success>"
                if built and built == live
                else "<warning>≠</warning>"
                if live
                else "<dim>·</dim>"
            )
            pin = f" <comment>pinned {checkout.repo.commit[:10]}</comment>" if checkout.repo.commit else ""
            task = f" <dim>{checkout.repo.task}</dim>" if checkout.repo.task else ""
            patches = (
                f" <info>+{len(checkout.patches)} patch{'es' if len(checkout.patches) != 1 else ''}</info>"
                if checkout.patches
                else ""
            )
            console.write(
                f"  {mark} <info>{StateDiff.label(checkout.key):<{width}}</info>  {str(built)[:7] if built else '-------'} / "
                f"{str(live)[:7] if live else '-------'}  <dim>{checkout.repo.branch}</dim>{pin}{patches}{task}"
            )
