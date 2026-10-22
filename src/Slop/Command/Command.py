import argparse
from abc import ABC, abstractmethod

from Slop.Output.Console import Console


class Command(ABC):
    name: str
    help: str
    forward = True

    def configure(self, parser: argparse.ArgumentParser) -> None:
        pass

    @staticmethod
    def csv(value: str) -> list[str]:
        return [x for x in value.split(",") if x]

    @abstractmethod
    def run(self, args: argparse.Namespace, console: Console) -> int | None: ...
