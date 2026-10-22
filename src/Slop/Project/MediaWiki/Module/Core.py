from concurrent.futures import ThreadPoolExecutor

from Slop.Git.Git import Git
from Slop.Module.Module import Module


class Core(Module):
    name = "core"
    title = "Syncing MediaWiki core"
    description = "Sync MediaWiki core into versions/<v> for each version."

    def run(self) -> None:
        checkouts = [self.project.versionCheckouts(v)[0] for v in self.project.versions]
        with (
            self.ctx.progress(len(checkouts), "Synced", "core checkout") as bar,
            ThreadPoolExecutor(max_workers=max(1, min(self.ctx.jobs, len(checkouts)))) as pool,
        ):
            futures = [pool.submit(self.sync, bar, c) for c in checkouts]
            for future in futures:
                future.result()

    def sync(self, bar, checkout) -> None:
        with bar.task(checkout.key):
            Git(self.ctx).sync(checkout.repo, checkout.dest, checkout.key)
