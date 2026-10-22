from Slop.Git.Git import Git
from Slop.Module.Module import Module


class Patches(Module):
    name = "patches"
    title = "Applying patches"
    description = "Apply patches from <config>/<project>/patches.yaml on top of the checkout."

    def run(self) -> None:
        (checkout,) = self.project.checkouts()
        Git(self.ctx).applyPatches(
            checkout.dest, checkout.patches, lambda patch, diff: self.project.saveResolvedPatch(checkout, patch, diff)
        )
        if not checkout.patches:
            self.ctx.skip("no patches configured")
