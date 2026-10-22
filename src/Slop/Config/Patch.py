import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from Slop.SlopError import SlopError


@dataclass(frozen=True)
class Patch:
    KINDS = ("file", "gerrit", "github", "commit")

    name: str
    path: Path
    kind: str
    description: str = ""
    task: str | None = None
    file: Path | None = None
    url: str | None = None
    ref: str | None = None
    commit: str | None = None
    commits: int = 1
    gerrit: int | None = None
    patchset: int | None = None
    project: str | None = None
    pr: int | None = None
    spec: dict = field(default_factory=dict, compare=False, hash=False)

    @classmethod
    def fromSpec(cls, entry: dict, patchesDir: Path, gerritBase: str, where: str) -> "Patch":
        if not isinstance(entry, dict) or "path" not in entry:
            raise SlopError(f"{where}: needs a path")
        path = Path(str(entry["path"]))
        file = patchesDir / entry["patch"] if entry.get("patch") else None
        if file is not None and not file.is_file():
            raise SlopError(f"{where}: {file} does not exist")
        common = dict(
            path=path,
            description=str(entry.get("description") or ""),
            task=entry.get("task"),
            file=file,
            spec=dict(entry),
        )

        if entry.get("gerrit"):
            number = int(entry["gerrit"])
            patchset = int(entry["patchset"]) if entry.get("patchset") else None
            project = entry.get("project")
            if not project or not patchset:
                raise SlopError(f"{where}: gerrit patches need project and patchset (use `slop patch add`)")
            return cls(
                name=str(entry.get("name") or f"gerrit-{number}"),
                kind="gerrit",
                url=f"{gerritBase.rstrip('/')}/{project}",
                ref=f"refs/changes/{number % 100:02d}/{number}/{patchset}",
                gerrit=number,
                patchset=patchset,
                project=str(project),
                commit=entry.get("commit"),
                **common,
            )
        if entry.get("github"):
            match = re.fullmatch(r"([\w.-]+/[\w.-]+)#(\d+)", str(entry["github"]))
            if not match or not entry.get("commit"):
                raise SlopError(f"{where}: github patches look like 'Owner/Repo#12' and need a commit")
            return cls(
                name=str(entry.get("name") or f"github-{match.group(1).split('/')[1]}-{match.group(2)}"),
                kind="github",
                url=f"https://github.com/{match.group(1)}.git",
                ref=f"refs/pull/{match.group(2)}/head",
                commit=str(entry["commit"]),
                commits=int(entry.get("commits") or 1),
                pr=int(match.group(2)),
                project=match.group(1),
                **common,
            )
        if entry.get("commit"):
            return cls(
                name=str(entry.get("name") or f"commit-{str(entry['commit'])[:10]}"),
                kind="commit",
                url=entry.get("url"),
                commit=str(entry["commit"]),
                **common,
            )
        if file is None:
            raise SlopError(f"{where}: needs one of patch, gerrit, github or commit")
        return cls(name=str(entry.get("name") or file.stem), kind="file", **common)

    @classmethod
    def loadFile(cls, file: Path, patchesDir: Path, gerritBase: str, data: dict | None = None) -> list["Patch"]:
        from Slop.Config.YamlFile import YamlFile

        data = YamlFile.read(file) if data is None else data
        patches = []
        seen = set()
        for i, entry in enumerate(data.get("patches") or []):
            patch = cls.fromSpec(entry, patchesDir, gerritBase, f"{file}: patches[{i}]")
            if patch.name in seen:
                raise SlopError(f"{file}: more than one patch named {patch.name!r}")
            seen.add(patch.name)
            patches.append(patch)
        return patches

    @property
    def source(self) -> str:
        if self.kind == "gerrit":
            return f"gerrit {self.gerrit},{self.patchset}"
        if self.kind == "github":
            return f"{self.project}#{self.pr} @{(self.commit or '')[:10]}"
        if self.kind == "commit":
            return f"commit {(self.commit or '')[:10]}"
        return f"patch {self.file.name if self.file else '?'}"

    @property
    def resolved(self) -> bool:
        return self.kind != "file" and self.file is not None

    def digest(self) -> str:
        h = hashlib.sha256(json.dumps(self.spec, sort_keys=True, default=str).encode())
        if self.file is not None and self.file.is_file():
            h.update(self.file.read_bytes())
        return h.hexdigest()

    def summary(self) -> str:
        text = self.source
        if self.description:
            text += f": {self.description}"
        return text
