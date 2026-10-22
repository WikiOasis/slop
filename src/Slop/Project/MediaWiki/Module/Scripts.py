import filecmp
import shutil

from Slop.Module.Module import Module


class Scripts(Module):
    name = "scripts"
    title = "Copying scripts"
    description = "Copy the config's scripts/ into <project>/scripts."

    def run(self) -> None:
        src = self.project.configDir / "scripts"
        copied = 0
        for path in sorted(src.rglob("*")) if src.is_dir() else []:
            rel = path.relative_to(src)
            if path.is_dir() or any(part.startswith(".") for part in rel.parts):
                continue
            dest = self.project.scriptsDir / rel
            if dest.is_file() and filecmp.cmp(path, dest, shallow=False):
                continue
            self.ctx.item("Copying", rel.as_posix())
            copied += 1
            if not self.ctx.dryRun:
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, dest)
        if not copied:
            self.ctx.skip("scripts up to date")
