from concurrent.futures import ThreadPoolExecutor

from Slop.Git.Git import Git
from Slop.Module.Module import Module
from Slop.Output.ProgressBar import ProgressBar
from Slop.Project.Checkout import Checkout
from Slop.SlopError import SlopError


class Sync(Module):
    name = "sync"
    title = "Syncing repos"
    description = "Sync mw-config, core, extensions and skins together in one parallel pool (the fast path)."

    def run(self) -> None:
        jobs = [c for c in self.project.selectable() if c.key == "config" or self.ctx.inScope(c.scope)]
        if self.project.mwConfig is None:
            self.ctx.warn("no mw-config repo in slop.yaml")
        parents = [c for c in jobs if any(o is not c and o.dest.is_relative_to(c.dest) for o in jobs)]
        phases = [jobs]
        if any(not (c.dest / ".git").exists() for c in parents):
            phases = [parents, [c for c in jobs if c not in parents]]
        failures = []
        changed = 0
        with (
            self.ctx.progress(len(jobs), "Synced", "repo") as bar,
            ThreadPoolExecutor(max_workers=self.ctx.jobs) as pool,
        ):
            for phase in phases:
                futures = [(c, pool.submit(self.sync, bar, c)) for c in phase]
                for checkout, future in futures:
                    try:
                        changed += 1 if future.result() else 0
                    except SlopError as e:
                        self.ctx.warn(str(e))
                        failures.append(checkout.key)
                if failures:
                    break
        if failures:
            raise SlopError(f"{len(failures)} of {len(jobs)} repos failed to sync: {', '.join(failures)}")
        self.ctx.log(f"<comment>{changed}</comment> changed, <comment>{len(jobs) - changed}</comment> unchanged")

    def sync(self, bar: ProgressBar, checkout: Checkout) -> bool:
        with bar.task(checkout.key.removeprefix("versions/")):
            return Git(self.ctx).sync(checkout.repo, checkout.dest, checkout.key)
