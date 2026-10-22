from datetime import UTC, datetime
from pathlib import Path

from Slop.Config.YamlFile import YamlFile


class StateFile:
    DIR = "state"

    def __init__(self, configDir: Path, project: str):
        self.configDir = configDir
        self.project = project

    @property
    def relative(self) -> str:
        return f"{self.DIR}/{self.project}.yaml"

    @property
    def path(self) -> Path:
        return self.configDir / self.relative

    @property
    def filesDir(self) -> Path:
        return self.configDir / self.DIR / self.project

    @property
    def paths(self) -> list[str]:
        return [self.relative, f"{self.DIR}/{self.project}"]

    def exists(self) -> bool:
        return self.path.is_file()

    def load(self) -> dict:
        return YamlFile.read(self.path, missingOk=True)

    @staticmethod
    def parse(text: str | None, where: str) -> dict:
        return YamlFile.parse(text, where) if text else {}

    def save(self, state: dict) -> None:
        YamlFile.write(self.path, state)

    def lockFile(self, name: str) -> Path:
        return self.filesDir / name

    @staticmethod
    def now() -> str:
        return datetime.now(UTC).isoformat(timespec="seconds")

    @staticmethod
    def pins(state: dict) -> dict[str, str]:
        return {key: entry["base"] for key, entry in (state.get("repos") or {}).items() if entry.get("base")}
