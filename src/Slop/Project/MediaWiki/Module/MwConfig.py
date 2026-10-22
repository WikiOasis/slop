from Slop.Git.Git import Git
from Slop.Module.Module import Module


class MwConfig(Module):
    name = "mw-config"
    title = "Syncing mw-config"
    description = "Sync the mw-config repo (LocalSettings.php, entrypoints) into <project>/config."

    def run(self) -> None:
        if self.project.mwConfig is None:
            self.ctx.warn("no mw-config repo in slop.yaml; skipping")
            return
        with self.ctx.progress(1, "Synced", "repo") as bar, bar.task("config"):
            Git(self.ctx).sync(self.project.mwConfig, self.project.mwConfigDir, "config")
