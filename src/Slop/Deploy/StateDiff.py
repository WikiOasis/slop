from dataclasses import dataclass, field
from pathlib import Path

from Slop.Git.Git import Git
from Slop.Output.Console import Console


@dataclass
class StateDiff:
    old: dict
    new: dict
    changed: dict[str, tuple[str | None, str | None]] = field(default_factory=dict)
    added: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    patchesAdded: list[tuple[str, dict]] = field(default_factory=list)
    patchesRemoved: list[tuple[str, dict]] = field(default_factory=list)

    @classmethod
    def compare(cls, old: dict, new: dict) -> "StateDiff":
        diff = cls(old, new)
        for key, entry in new.items():
            before = old.get(key)
            if before is None:
                diff.added.append(key)
            elif before.get("base") != entry.get("base") or before.get("head") != entry.get("head"):
                diff.changed[key] = (before.get("base"), entry.get("base"))
            oldPatches = {p["name"]: p for p in (before or {}).get("patches") or []}
            newPatches = {p["name"]: p for p in entry.get("patches") or []}
            for name, patch in newPatches.items():
                if name not in oldPatches or oldPatches[name].get("source") != patch.get("source"):
                    diff.patchesAdded.append((key, patch))
            for name, patch in oldPatches.items():
                if name not in newPatches:
                    diff.patchesRemoved.append((key, patch))
        diff.removed = [key for key in old if key not in new]
        return diff

    @property
    def empty(self) -> bool:
        return not (self.changed or self.added or self.removed or self.patchesAdded or self.patchesRemoved)

    @property
    def keys(self) -> set[str]:
        return (
            set(self.changed)
            | set(self.added)
            | set(self.removed)
            | {k for k, _ in self.patchesAdded}
            | {k for k, _ in self.patchesRemoved}
        )

    def tasks(self) -> list[str]:
        found = []
        moved = {k for k, (old, new) in self.changed.items() if old != new} | set(self.added)
        for key in sorted(moved):
            task = self.new.get(key, {}).get("task")
            if task and task not in found:
                found.append(task)
        for _, patch in self.patchesAdded:
            if patch.get("task") and patch["task"] not in found:
                found.append(patch["task"])
        return found

    @staticmethod
    def label(key: str) -> str:
        parts = key.split("/")
        if parts[0] == "versions" and len(parts) == 2:
            return f"core@{parts[1]}"
        if parts[0] == "versions":
            return f"{parts[-1]}@{parts[1]}"
        return "." if key == "." else parts[-1]

    def show(self, console: Console, git: Git, root: Path) -> None:
        if self.empty:
            console.write("  <dim>no changes since the last deploy</dim>")
            return
        for key in sorted(self.added):
            entry = self.new[key]
            console.write(
                f"  <success>+</success> <info>{self.label(key)}</info> <dim>{entry.get('branch')} @ {str(entry.get('base'))[:10]}</dim>"
            )
        for key in sorted(self.removed):
            console.write(f"  <error>-</error> <info>{self.label(key)}</info>")
        for key, (old, new) in sorted(self.changed.items()):
            directory = root / key if key != "." else root
            count = git.count(directory, old, new) if old and new and (directory / ".git").exists() else None
            arrow = f"{str(old)[:7]}..{str(new)[:7]}" if old != new else "patches only"
            extra = f" <comment>(+{count})</comment>" if count else ""
            console.write(f"  <comment>~</comment> <info>{self.label(key)}</info> <dim>{arrow}</dim>{extra}")
            if old and new and old != new and (directory / ".git").exists():
                for line in git.subjects(directory, old, new, 8):
                    console.write(f"      <dim>{line}</dim>")
        for key, patch in self.patchesAdded:
            task = f" <comment>{patch['task']}</comment>" if patch.get("task") else ""
            console.write(
                f"  <success>+</success> patch <info>{patch['name']}</info> <dim>on {self.label(key)}: {patch.get('source')}</dim>{task}"
            )
        for key, patch in self.patchesRemoved:
            console.write(f"  <error>-</error> patch <info>{patch['name']}</info> <dim>from {self.label(key)}</dim>")

    def summary(self, git: Git | None = None, root: Path | None = None, limit: int = 1200) -> str:
        parts = []
        for key, (old, new) in sorted(self.changed.items()):
            text = self.label(key)
            if old != new:
                text += f" {str(old)[:7]}..{str(new)[:7]}"
                if git and root and old and new:
                    directory = root / key if key != "." else root
                    count = git.count(directory, old, new) if (directory / ".git").exists() else None
                    if count:
                        text += f" (+{count})"
            parts.append(text)
        parts += [f"+{self.label(k)}" for k in sorted(self.added)]
        parts += [f"-{self.label(k)}" for k in sorted(self.removed)]
        for key, patch in self.patchesAdded:
            text = f"+{patch.get('source')} on {self.label(key)}"
            if patch.get("description"):
                text += f' "{patch["description"][:80]}"'
            if patch.get("task"):
                text += f" ({patch['task']})"
            parts.append(text)
        parts += [f"-patch {patch['name']}" for _, patch in self.patchesRemoved]
        text = ", ".join(parts)
        return text if len(text) <= limit else text[: limit - 1] + "…"
