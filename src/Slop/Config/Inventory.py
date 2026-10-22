import fnmatch
import socket
from dataclasses import dataclass
from pathlib import Path

from Slop.Config.ManagedService import ManagedService
from Slop.Config.Role import Role
from Slop.SlopError import SlopError


@dataclass(frozen=True)
class Inventory:
    master: str | None
    user: str | None
    identity: str | None
    sshOptions: list[str]
    sudo: bool
    local: list[str]
    hosts: list[str]
    roles: dict[str, Role]
    services: dict[str, ManagedService]

    DEFAULT_SSH = [
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=10",
        "-o",
        "ServerAliveInterval=15",
        "-o",
        "StrictHostKeyChecking=accept-new",
    ]

    @classmethod
    def fromSpec(cls, data: dict, where: str) -> "Inventory":
        ssh = data.get("ssh") or {}
        hosts = [str(h) for h in data.get("hosts") or []]
        services = {}
        for name, spec in (data.get("services") or {}).items():
            units = spec.get("units") or ([spec["unit"]] if spec.get("unit") else [])
            if not units:
                raise SlopError(f"{where}: services.{name} needs units")
            action = str(spec.get("action", "reload"))
            if action not in ("reload", "restart"):
                raise SlopError(f"{where}: services.{name}.action must be reload or restart")
            services[name] = ManagedService(name=name, units=[str(u) for u in units], action=action)
        roles = {}
        for name, spec in (data.get("roles") or {}).items():
            patterns = [str(h) for h in spec.get("hosts") or []]
            matched = [h for h in hosts if any(fnmatch.fnmatch(h, p) for p in patterns)]
            matched += [p for p in patterns if not any(c in p for c in "*?[") and p not in matched]
            for service in spec.get("services") or []:
                if service not in services:
                    raise SlopError(f"{where}: roles.{name} uses undefined service {service!r}")
            roles[name] = Role(
                name=name,
                hosts=matched,
                batch=max(1, int(spec.get("batch", 1))),
                services=list(spec.get("services") or []),
                soak=float(spec.get("soak", 0)),
            )
        identity = ssh.get("identity")
        return cls(
            master=data.get("master"),
            user=ssh.get("user"),
            identity=str(Path(identity).expanduser()) if identity else None,
            sshOptions=cls.DEFAULT_SSH + [str(o) for o in ssh.get("options") or []],
            sudo=bool(ssh.get("sudo", True)),
            local=[str(h) for h in data.get("local") or []],
            hosts=hosts,
            roles=roles,
            services=services,
        )

    @staticmethod
    def thisHost() -> set[str]:
        names = {socket.gethostname(), socket.getfqdn()}
        return names | {n.split(".", 1)[0] for n in names}

    def isLocal(self, host: str) -> bool:
        return host in self.local or host in ("localhost", "127.0.0.1") or host in self.thisHost()

    def isMaster(self) -> bool:
        return self.master is None or self.isLocal(self.master)

    def role(self, name: str) -> Role:
        if name not in self.roles:
            raise SlopError(f"no role named {name!r} ({', '.join(self.roles) or 'none defined'})")
        return self.roles[name]

    def resolveHost(self, name: str) -> str:
        known = set(self.hosts) | {h for r in self.roles.values() for h in r.hosts}
        if name in known:
            return name
        matches = [h for h in known if h.split(".", 1)[0] == name or fnmatch.fnmatch(h, name)]
        if len(matches) == 1:
            return matches[0]
        if not matches:
            raise SlopError(f"unknown host {name!r}")
        raise SlopError(f"{name!r} matches more than one host: {', '.join(sorted(matches))}")

    def rolesOf(self, host: str) -> list[Role]:
        return [r for r in self.roles.values() if host in r.hosts]

    @staticmethod
    def short(host: str) -> str:
        return host.split(".", 1)[0]
