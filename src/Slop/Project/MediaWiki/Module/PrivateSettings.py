import filecmp
import shutil

from Slop.Module.Module import Module
from Slop.SlopError import SlopError


class PrivateSettings(Module):
    name = "private-settings"
    title = "Copying PrivateSettings.php"
    description = "Copy the build host's PrivateSettings.php into <project>/config (it is not in mw-config)."

    def run(self) -> None:
        src = self.project.privateSettings
        if src is None:
            self.ctx.skip("private-settings disabled in slop.yaml")
            return
        dest = self.project.mwConfigDir / "PrivateSettings.php"
        if not src.is_file():
            if dest.is_file():
                self.ctx.warn(f"{src} does not exist; keeping {self.ctx.display(dest)}")
                return
            raise SlopError(f"{src} does not exist and {self.ctx.display(dest)} is missing; LocalSettings.php needs it")
        if dest.is_file() and filecmp.cmp(src, dest, shallow=False):
            self.ctx.skip("PrivateSettings.php up to date")
            return
        self.ctx.item("Copying", self.ctx.display(dest), f"from {src}")
        if not self.ctx.dryRun:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)
