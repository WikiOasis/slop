import argparse
import re
import shutil
from pathlib import Path

from Slop.Command.Command import Command
from Slop.Command.Reader import Reader
from Slop.Config.Patch import Patch as PatchEntry
from Slop.Deploy.StateDiff import StateDiff
from Slop.Forge.Gerrit import Gerrit
from Slop.Forge.GitHub import GitHub
from Slop.Output.Console import Console
from Slop.Project.Checkout import Checkout
from Slop.Project.ProjectLoader import ProjectLoader
from Slop.Runner import Runner
from Slop.SlopError import SlopError


class Patch(Command):
    name = "patch"
    help = "manage patches applied on top of checkouts: files, Gerrit changes, GitHub PRs, commits"
    forward = "noconfig"

    def configure(self, parser: argparse.ArgumentParser) -> None:
        parser.formatter_class = argparse.RawDescriptionHelpFormatter
        parser.description = self.help + (
            "\n\nPatches live in the config repo, are re-applied after every sync, and persist across deploys. "
            "If one stops applying, an interactive build lets you resolve it and saves the result.\n\n"
            "examples:\n"
            "  slop patch list\n"
            "  slop patch add 1234567 -t T123                 latest patch set of a Gerrit change\n"
            "  slop patch add 1234567,4                         a specific patch set\n"
            "  slop patch add https://gerrit.wikimedia.org/r/c/mediawiki/core/+/1234567\n"
            "  slop patch add WikiOasis/WikiOasisMagic#42       a GitHub pull request\n"
            "  slop patch add fix.patch --path extensions/Echo -d 'Fix notifications'\n"
            "  slop patch add commit:3f2a1bc --path extensions/Echo --url https://github.com/x/y\n"
            "  slop patch update gerrit-1234567                 move to the latest patch set\n"
            "  slop patch remove gerrit-1234567"
        )
        parser.add_argument("action", choices=["list", "add", "update", "remove"])
        parser.add_argument("source", nargs="?", metavar="SOURCE|NAME")
        parser.add_argument("-p", "--project", help="project (default: slop.yaml's default)")
        parser.add_argument("--path", help="checkout to patch, e.g. extensions/Echo or . for core")
        parser.add_argument("--name", help="name for the patch (default: from its source)")
        parser.add_argument("-d", "--description", help="description, used as the commit message")
        parser.add_argument("-t", "--task", help="Phorge task the patch is for; logged to the SAL when deployed")
        parser.add_argument("--url", help="where to fetch a commit: patch from (default: the checkout's origin)")
        parser.add_argument("--patchset", type=int, help="for update: this patch set instead of the latest")
        parser.add_argument("--no-pull", dest="noPull", action="store_true", help="don't pull the config repo first")
        Runner.addMinimalOptions(parser)
        for projectType in ProjectLoader.all().values():
            projectType.addOptions(parser)

    def run(self, args: argparse.Namespace, console: Console) -> None:
        if args.action == "list":
            return self.list(args, console)
        if not args.source:
            raise SlopError(f"usage: slop patch {args.action} {'SOURCE' if args.action == 'add' else 'NAME'}")
        runner = Runner(args, console)
        runner.syncConfig()
        project = runner.projects([args.project] if args.project else runner.config.defaults[:1])[0]
        runner.start(f"patch {args.action} {args.source}")
        try:
            getattr(self, args.action)(runner, project, args)
            runner.publishConfig(
                project,
                f"slop patch {args.action} {args.source}",
                [args.task] if getattr(args, "task", None) else None,
            )
        except BaseException as e:
            runner.fail(e)
            raise
        runner.finish()

    def targets(self, project, path: str) -> list[Checkout]:
        wanted = Path(path)
        found = [c for c in project.selectable() if c.scope == wanted]
        if not found:
            raise SlopError(f"{project.name} has no checkout at {path} (see `slop repo list`)")
        return found

    def add(self, runner: Runner, project, args) -> None:
        source = args.source
        entry: dict
        if Path(source).is_file():
            entry = self.fromFile(project, args)
        elif source.startswith("commit:") or re.fullmatch(r"[0-9a-f]{7,40}", source):
            if not args.path:
                raise SlopError("commit patches need --path")
            sha = source.removeprefix("commit:")
            entry = {"name": args.name or f"commit-{sha[:10]}", "path": args.path, "commit": sha}
            if args.url:
                entry["url"] = args.url
        elif "github" in source or re.fullmatch(r"[\w.-]+/[\w.-]+#\d+", source):
            repo, number = GitHub.parseRef(source)
            pull = GitHub().pull(repo, number)
            path = args.path or self.pathForUrl(project, repo)
            entry = {
                "name": args.name or f"github-{repo.split('/')[1]}-{number}",
                "path": path,
                "github": f"{repo}#{number}",
                "commit": pull["commit"],
                "commits": pull["commits"],
                "description": pull["title"],
            }
            if pull["merged"] or pull["state"] != "open":
                runner.ctx.warn(f"{repo}#{number} is {'merged' if pull['merged'] else pull['state']}")
        else:
            number, patchset = Gerrit.parseRef(source)
            change = Gerrit(runner.config.gerrit).change(number, patchset)
            entry = {
                "name": args.name or f"gerrit-{number}",
                "path": args.path or Gerrit.pathFor(change["project"]),
                "gerrit": number,
                "patchset": change["patchset"],
                "project": change["project"],
                "commit": change["commit"],
                "description": change["subject"],
            }
            if change["status"] != "NEW":
                runner.ctx.warn(f"Gerrit change {number} is {change['status'].lower()}")
            if change["patchset"] != change["latest"]:
                runner.ctx.warn(f"patch set {change['patchset']} is not the latest ({change['latest']})")
        if args.description:
            entry["description"] = args.description
        if args.task:
            entry["task"] = args.task
        entry = {k: v for k, v in entry.items() if v not in (None, "")}

        checkouts = self.targets(project, entry["path"])
        for checkout in checkouts:
            PatchEntry.fromSpec(entry, project.patchesDir(checkout), runner.config.gerrit, "new patch")
            file = project.addPatch(checkout, entry)
            runner.ctx.success(
                f"added <info>{entry['name']}</info> on {StateDiff.label(checkout.key)} <dim>({file.name})</dim>"
            )
        what = "core" if entry["path"] == "." else f"extensions {Path(entry['path']).name}"
        runner.ctx.console.write(
            f"  <dim>try it with `slop update patches {Path(entry['path']).name if entry['path'] != '.' else 'core'}`, "
            f"ship it with `slop deploy {what} -m ...{' -t ' + args.task if args.task else ''}`</dim>"
        )

    def fromFile(self, project, args) -> dict:
        source = Path(args.source)
        path = args.path or "."
        checkouts = self.targets(project, path)
        name = args.name or source.stem
        filename = f"{name}.patch"
        directory = project.patchesDir(checkouts[0])
        directory.mkdir(parents=True, exist_ok=True)
        if (directory / filename).exists() and source.resolve() != (directory / filename).resolve():
            raise SlopError(f"{directory / filename} already exists; pick another --name")
        if source.resolve() != (directory / filename).resolve():
            shutil.copy2(source, directory / filename)
        description = args.description
        if not description:
            match = re.search(r"^Subject: (?:\[PATCH[^\]]*\] )?(.+)$", source.read_text(errors="replace"), re.M)
            description = match.group(1).strip() if match else ""
        return {"name": name, "path": path, "patch": filename, "description": description}

    def pathForUrl(self, project, repo: str) -> str:
        found = {
            c.scope.as_posix()
            for c in project.selectable()
            if c.scope is not None and repo.lower() in c.repo.url.lower().removesuffix(".git")
        }
        if len(found) != 1:
            raise SlopError(f"cannot tell which checkout {repo} belongs to; pass --path")
        return found.pop()

    def find(self, project, name: str) -> list[tuple[Checkout, PatchEntry]]:
        found = [(c, p) for c in project.selectable() for p in c.patches if p.name == name]
        if not found:
            raise SlopError(f"no patch named {name!r} (see `slop patch list`)")
        return found

    def update(self, runner: Runner, project, args) -> None:
        for checkout, patch in self.find(project, args.source):
            if patch.kind == "gerrit":
                change = Gerrit(runner.config.gerrit).change(patch.gerrit, args.patchset)
                if change["patchset"] == patch.patchset and not patch.resolved:
                    runner.ctx.skip(f"{patch.name} is already at patch set {patch.patchset}")
                    continue
                changes = {
                    "patchset": change["patchset"],
                    "commit": change["commit"],
                    "description": change["subject"],
                    "patch": None,
                }
                label = f"patch set {patch.patchset} → {change['patchset']}"
            elif patch.kind == "github":
                repo, number = GitHub.parseRef(f"{patch.project}#{patch.pr}")
                pull = GitHub().pull(repo, number)
                if pull["commit"] == patch.commit and not patch.resolved:
                    runner.ctx.skip(f"{patch.name} is already at {patch.commit[:10]}")
                    continue
                changes = {
                    "commit": pull["commit"],
                    "commits": pull["commits"],
                    "description": pull["title"],
                    "patch": None,
                }
                label = f"{patch.commit[:10]} → {pull['commit'][:10]}"
            else:
                raise SlopError(f"{patch.name} is a {patch.kind} patch; edit or replace it instead")
            project.updatePatch(checkout, patch, changes)
            if patch.resolved and patch.file is not None:
                patch.file.unlink(missing_ok=True)
            runner.ctx.success(f"updated <info>{patch.name}</info> on {StateDiff.label(checkout.key)}: {label}")

    def remove(self, runner: Runner, project, args) -> None:
        for checkout, patch in self.find(project, args.source):
            project.removePatch(checkout, patch)
            runner.ctx.success(f"removed <info>{patch.name}</info> from {StateDiff.label(checkout.key)}")
            if patch.file is not None and patch.file.is_file():
                still = [
                    p for c in self.reloaded(runner, project).selectable() for p in c.patches if p.file == patch.file
                ]
                if not still:
                    patch.file.unlink()

    def reloaded(self, runner: Runner, project):
        runner.reload()
        fresh = runner.config.project(project.name)
        fresh.applyOptions(runner.args)
        return fresh

    def list(self, args: argparse.Namespace, console: Console) -> None:
        config = Reader.config(args)
        project = config.project(args.project or config.defaults[0])
        project.applyOptions(args)
        total = 0
        for checkout in project.selectable():
            if not checkout.patches:
                continue
            console.write(f"<info>{StateDiff.label(checkout.key)}</info> <dim>{checkout.key}</dim>")
            for patch in checkout.patches:
                total += 1
                task = f" <comment>{patch.task}</comment>" if patch.task else ""
                resolved = " <dim>(resolved copy)</dim>" if patch.resolved else ""
                console.write(f"  <bold>{patch.name}</bold>  {patch.source}{resolved}{task}")
                if patch.description:
                    console.write(f"    <dim>{patch.description}</dim>")
        if not total:
            console.write("<dim>no patches configured</dim>")
