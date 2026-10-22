from Slop.Git.Git import Git
from Slop.Module.Module import Module
from Slop.SlopError import SlopError


class Patches(Module):
    name = "patches"
    title = "Applying patches"
    description = (
        "Apply patches from patches-<v>.yaml (files, Gerrit changes, GitHub PRs, commits) on top of each checkout."
    )

    def run(self) -> None:
        git = Git(self.ctx)
        checkouts = [
            checkout
            for version in self.project.versions
            for checkout in self.project.versionCheckouts(version)
            if self.ctx.inScope(checkout.scope)
        ]
        applied = 0
        total = sum(len(c.patches) for c in checkouts)
        for checkout in checkouts:
            if not self.ctx.dryRun and not (checkout.dest / ".git").exists():
                if checkout.patches:
                    raise SlopError(f"{checkout.key} has patches but is not a synced checkout")
                continue
            if git.applyPatches(
                checkout.dest,
                checkout.patches,
                lambda patch, diff, c=checkout: self.project.saveResolvedPatch(c, patch, diff),
            ):
                applied += len(checkout.patches)
        if not total:
            self.ctx.skip("no patches configured")
        elif applied:
            self.ctx.success(f"applied <info>{applied}</info> of {total} patches")
