import filecmp
import shutil

from Slop.Module.Module import Module
from Slop.SlopError import SlopError


class OAuthKey(Module):
    name = "oauth-key"
    title = "Copying OAuth.key"
    description = "Copy the build host's OAuth.key into <project>/config (it is not in mw-config)."

    def run(self) -> None:
        src = self.project.oauthKey
        if src is None:
            self.ctx.skip("oauth-key disabled in slop.yaml")
            return
        dest = self.project.mwConfigDir / "OAuth.key"
        if not src.is_file():
            if dest.is_file():
                self.ctx.warn(f"{src} does not exist; keeping {self.ctx.display(dest)}")
                return
            raise SlopError(
                f"{src} does not exist and {self.ctx.display(dest)} is missing; the OAuth extension needs it"
            )
        if dest.is_file() and filecmp.cmp(src, dest, shallow=False):
            self.ctx.skip("OAuth.key up to date")
            return
        self.ctx.item("Copying", self.ctx.display(dest), f"from {src}")
        if not self.ctx.dryRun:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)
