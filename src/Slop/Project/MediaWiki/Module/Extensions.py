from concurrent.futures import ThreadPoolExecutor

from Slop.Git.Git import Git
from Slop.Module.Module import Module
from Slop.Output.ProgressBar import ProgressBar
from Slop.Project.Checkout import Checkout
from Slop.SlopError import SlopError


class Extensions(Module):
    name = "extensions"
    title = "Syncing extensions and skins"
    description = "Sync extensions and skins from repos-<v>.yaml into each version, in parallel."

    def run(self) -> None:
        jobs = [
            checkout
            for version in self.project.versions
            for checkout in self.project.versionCheckouts(version)[1:]
            if self.ctx.inScope(checkout.scope)
        ]
        if not jobs:
            self.ctx.skip("no extensions or skins selected")
            return

        failures = []
        changed = 0
        with (
            self.ctx.progress(len(jobs), "Synced", "repo") as bar,
            ThreadPoolExecutor(max_workers=self.ctx.jobs) as pool,
        ):
            futures = [(c, pool.submit(self.sync, bar, c)) for c in jobs]
            for checkout, future in futures:
                try:
                    changed += 1 if future.result() else 0
                except SlopError as e:
                    self.ctx.warn(str(e))
                    failures.append(checkout.key)

        if failures:
            raise SlopError(f"{len(failures)} of {len(jobs)} repos failed to sync: {', '.join(failures)}")
        self.ctx.log(f"<comment>{changed}</comment> changed, <comment>{len(jobs) - changed}</comment> unchanged")

    def sync(self, bar: ProgressBar, checkout: Checkout) -> bool:
        name = f"{checkout.repo.path.name}@{checkout.key.split('/')[1]}"
        with bar.task(name):
            return Git(self.ctx).sync(checkout.repo, checkout.dest, checkout.key)
