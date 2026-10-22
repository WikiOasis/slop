import shlex
import subprocess
from pathlib import Path

from Slop.Config.Inventory import Inventory
from Slop.Context import Context
from Slop.SlopError import SlopError


class Ssh:
    def __init__(self, ctx: Context, inventory: Inventory):
        self.ctx = ctx
        self.inventory = inventory

    def options(self) -> list[str]:
        opts = list(self.inventory.sshOptions)
        if self.inventory.identity:
            opts += ["-i", self.inventory.identity]
        return opts

    def destination(self, host: str) -> str:
        return f"{self.inventory.user}@{host}" if self.inventory.user else host

    def sudo(self, command: str, user: str | None = None) -> str:
        if not self.inventory.sudo:
            return command
        return f"sudo -n {'-u ' + shlex.quote(user) + ' ' if user else ''}{command}"

    def run(
        self, host: str, command: str, *, check: bool = True, mutates: bool = True, timeout: float = 600
    ) -> subprocess.CompletedProcess:
        if self.inventory.isLocal(host):
            argv = ["bash", "-c", command]
        else:
            argv = ["ssh", *self.options(), self.destination(host), command]
        proc = self.ctx.run(argv, check=False, mutates=mutates, timeout=timeout)
        if check and proc.returncode != 0:
            output = (proc.stderr or proc.stdout).strip()
            raise SlopError(f"{Inventory.short(host)}: `{command}` failed ({proc.returncode})\n{output[-2000:]}")
        return proc

    def reachable(self, host: str) -> str | None:
        proc = self.run(host, "true", check=False, mutates=False, timeout=30)
        if proc.returncode == 0:
            return None
        lines = (proc.stderr or proc.stdout or "").strip().splitlines()
        return lines[-1] if lines else "unreachable"

    def rsync(
        self,
        host: str,
        source: Path,
        dest: Path,
        *,
        excludes: list[str],
        owner: str | None,
        chmod: str | None,
        paths: list[str] | None = None,
        checksum: bool = False,
    ) -> subprocess.CompletedProcess:
        argv = [
            "rsync",
            "--archive",
            "--delete",
            "--delete-delay",
            "--delay-updates",
            "--mkpath",
            "--human-readable",
            "--stats",
            "--no-inc-recursive",
        ]
        if checksum:
            argv.append("--checksum")
        argv += [f"--exclude={pattern}" for pattern in excludes]
        if owner and self.inventory.sudo:
            argv.append(f"--chown={owner}")
        if chmod:
            argv.append(f"--chmod={chmod}")

        if paths:
            argv.append("--relative")
            sources = [f"{source}/./{p}" for p in paths if (source / p).exists()]
            if not sources:
                raise SlopError(f"none of {', '.join(paths)} exist in {source}")
        else:
            sources = [f"{source}/"]
        target = f"{dest}/"

        if self.inventory.isLocal(host):
            if self.inventory.sudo:
                argv = ["sudo", "-n", *argv]
            argv += [*sources, target]
        else:
            argv += ["-e", shlex.join(["ssh", *self.options()])]
            if self.inventory.sudo:
                argv.append("--rsync-path=sudo -n rsync")
            argv += [*sources, f"{self.destination(host)}:{target}"]

        proc = self.ctx.run(argv, check=False, timeout=3600)
        if proc.returncode not in (0, 24):
            raise SlopError(
                f"{Inventory.short(host)}: rsync failed ({proc.returncode})\n{(proc.stderr or proc.stdout).strip()[-2000:]}"
            )
        return proc

    @staticmethod
    def transferred(proc: subprocess.CompletedProcess) -> str:
        files = size = None
        for line in (proc.stdout or "").splitlines():
            if line.startswith("Number of regular files transferred:"):
                files = line.split(":", 1)[1].strip()
            elif line.startswith("Total transferred file size:"):
                size = line.split(":", 1)[1].strip().split(" bytes")[0]
        if files is None:
            return ""
        return f"{files} files, {size}" if size else f"{files} files"
