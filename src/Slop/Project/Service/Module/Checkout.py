from Slop.Git.Git import Git
from Slop.Module.Module import Module


class Checkout(Module):
    name = "checkout"
    title = "Syncing service checkout"
    description = "Sync the service's git repo into its build directory."

    def run(self) -> None:
        with self.ctx.progress(1, "Synced", "repo") as bar, bar.task(self.project.name):
            Git(self.ctx).sync(self.project.repo, self.project.root, ".")
