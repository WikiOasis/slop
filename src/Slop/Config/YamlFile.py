from pathlib import Path

import yaml

from Slop.Config.YamlDumper import YamlDumper
from Slop.SlopError import SlopError


class YamlFile:
    @staticmethod
    def read(path: Path, *, missingOk: bool = False) -> dict:
        try:
            with path.open() as f:
                data = yaml.safe_load(f) or {}
        except FileNotFoundError:
            if missingOk:
                return {}
            raise SlopError(f"missing config file: {path}") from None
        except yaml.YAMLError as e:
            raise SlopError(f"invalid YAML in {path}: {e}") from None
        if not isinstance(data, dict):
            raise SlopError(f"{path}: expected a mapping at the top level")
        return data

    @staticmethod
    def parse(text: str, where: str) -> dict:
        try:
            data = yaml.safe_load(text) or {}
        except yaml.YAMLError as e:
            raise SlopError(f"invalid YAML in {where}: {e}") from None
        if not isinstance(data, dict):
            raise SlopError(f"{where}: expected a mapping at the top level")
        return data

    @staticmethod
    def dumps(data: dict) -> str:
        return yaml.dump(
            data, Dumper=YamlDumper, sort_keys=False, default_flow_style=False, allow_unicode=True, width=4096
        )

    @classmethod
    def write(cls, path: Path, data: dict) -> bool:
        content = cls.dumps(data)
        if path.is_file() and path.read_text() == content:
            return False
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{path.name}.tmp")
        tmp.write_text(content)
        tmp.replace(path)
        return True

    @staticmethod
    def merge(base: dict, overlay: dict) -> dict:
        merged = dict(base)
        for key, value in overlay.items():
            if isinstance(value, dict) and isinstance(merged.get(key), dict):
                merged[key] = YamlFile.merge(merged[key], value)
            else:
                merged[key] = value
        return merged
