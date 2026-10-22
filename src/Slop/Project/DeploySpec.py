from dataclasses import dataclass, field
from pathlib import Path

from Slop.SlopError import SlopError


@dataclass(frozen=True)
class DeploySpec:
    path: Path | None
    roles: list[str]
    owner: str | None = None
    exclude: list[str] = field(default_factory=list)
    health: dict = field(default_factory=dict)
    l10n: dict = field(default_factory=dict)
    restart: list[str] | None = None
    afterSync: list[str] = field(default_factory=list)
    once: list[str] = field(default_factory=list)
    chmod: str | None = "D755,F644"

    @classmethod
    def fromSpec(cls, spec: dict, root: Path, defaults: dict, where: str) -> "DeploySpec":
        merged = {**defaults, **spec}
        path = merged.get("path")
        if path is not None:
            path = Path(path)
            path = path if path.is_absolute() else (root / path)
        roles = merged.get("roles") or []
        if not isinstance(roles, list):
            raise SlopError(f"{where}.deploy.roles must be a list")
        health = spec.get("checks") or spec.get("health") or {}
        if health and not isinstance(health, dict):
            raise SlopError(f"{where}.deploy.checks must be a mapping")
        if health and defaults.get("checks") and not health.get("tests") and not health.get("path"):
            health = {**health, "tests": defaults["checks"].get("tests", [])}
        for i, test in enumerate(health.get("tests") or []):
            if not isinstance(test, dict) or not test.get("path"):
                raise SlopError(f"{where}.deploy.checks.tests[{i}] needs a path")
        return cls(
            path=path,
            roles=[str(r) for r in roles],
            owner=merged.get("owner"),
            exclude=[str(e) for e in merged.get("exclude") or []],
            health=dict(health),
            l10n=dict(merged.get("l10n") or {}),
            restart=[str(s) for s in merged["restart"]] if merged.get("restart") is not None else None,
            afterSync=[str(c) for c in merged.get("after-sync") or []],
            once=[str(c) for c in merged.get("once") or []],
            chmod=merged.get("chmod", "D755,F644"),
        )

    @property
    def enabled(self) -> bool:
        return self.path is not None and bool(self.roles)
