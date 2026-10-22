from pathlib import Path

from Slop.Module.Module import Module
from Slop.SlopError import SlopError


class Links(Module):
    name = "links"
    title = "Linking LocalSettings.php and entrypoints"
    description = "Symlink LocalSettings.php into each version and the entrypoints into w/."

    def run(self) -> None:
        changed = 0
        for version in self.project.versions:
            changed += self.link(
                self.project.versionDir(version) / "LocalSettings.php",
                self.project.mwConfigDir / "LocalSettings.php",
            )
        entrypoints = self.project.mwConfigDir / "initialise" / "entrypoints"
        for name in self.project.entrypoints:
            changed += self.link(self.project.wDir / name, entrypoints / name)
        if not changed:
            self.ctx.skip("all links up to date")

    def link(self, link: Path, target: Path) -> bool:
        pointsTo = self.project.linkTarget(target)
        if link.is_symlink():
            if link.readlink() == pointsTo:
                return False
            if not self.ctx.dryRun:
                link.unlink()
        elif link.exists():
            raise SlopError(f"{self.ctx.display(link)} exists and is not a symlink; refusing to replace it")

        if not target.exists() and not self.ctx.dryRun:
            self.ctx.warn(f"{self.ctx.display(target)} does not exist yet (is mw-config synced?)")
        self.ctx.item("Linking", self.ctx.display(link), str(pointsTo))
        if not self.ctx.dryRun:
            link.parent.mkdir(parents=True, exist_ok=True)
            link.symlink_to(pointsTo)
        return True
