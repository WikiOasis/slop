from dataclasses import dataclass, replace
from pathlib import Path

from Slop.SlopError import SlopError


@dataclass(frozen=True)
class Repo:
    path: Path
    url: str
    branch: str
    submodules: bool = True
    commit: str | None = None
    task: str | None = None

    @classmethod
    def fromSpec(
        cls,
        path: Path,
        spec: dict,
        *,
        defaultUrl: str | None,
        defaultBranch: str,
        defaultSubmodules: bool = True,
        where: str,
    ) -> "Repo":
        url = spec.get("url", defaultUrl)
        if not url:
            raise SlopError(f"{where}: no url")
        commit = spec.get("commit")
        if commit is not None and not isinstance(commit, str):
            raise SlopError(f"{where}: commit must be a string (quote it)")
        return cls(
            path=path,
            url=str(url),
            branch=str(spec.get("branch", defaultBranch)),
            submodules=bool(spec.get("submodules", defaultSubmodules)),
            commit=commit,
            task=spec.get("task"),
        )

    def withBranch(self, branch: str) -> "Repo":
        return replace(self, branch=branch)

    def pinnedTo(self, commit: str | None) -> "Repo":
        return replace(self, commit=commit)

    @property
    def name(self) -> str:
        return self.path.name if self.path != Path(".") else "core"
