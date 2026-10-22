from abc import ABC, abstractmethod
from typing import Any

from Slop.Context import Context


class Module(ABC):
    name: str
    title: str
    description: str

    def __init__(self, ctx: Context):
        self.ctx = ctx
        self.project: Any = ctx.project

    @abstractmethod
    def run(self) -> None: ...
