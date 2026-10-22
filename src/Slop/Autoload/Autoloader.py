import importlib
import pkgutil
from types import ModuleType

from Slop.SlopError import SlopError


class Autoloader:
    @staticmethod
    def subclasses[T](namespace: ModuleType, base: type[T]) -> list[type[T]]:
        found = []
        for info in sorted(pkgutil.iter_modules(namespace.__path__), key=lambda i: i.name):
            name = f"{namespace.__name__}.{info.name}"
            if info.ispkg:
                try:
                    file = importlib.import_module(f"{name}.{info.name}")
                except ModuleNotFoundError as e:
                    if e.name != f"{name}.{info.name}":
                        raise
                    continue
            else:
                file = importlib.import_module(name)
            klass = getattr(file, info.name, None)
            if klass is None:
                raise SlopError(f"{file.__file__} does not define class {info.name}")
            if isinstance(klass, type) and issubclass(klass, base) and klass is not base:
                found.append(klass)
        return found
