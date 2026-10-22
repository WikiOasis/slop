import Slop.Project
from Slop.Autoload.Autoloader import Autoloader
from Slop.Project.Project import Project
from Slop.SlopError import SlopError


class ProjectLoader:
    _types: dict[str, type[Project]] | None = None

    @classmethod
    def all(cls) -> dict[str, type[Project]]:
        if cls._types is None:
            cls._types = {klass.type: klass for klass in Autoloader.subclasses(Slop.Project, Project)}
        return cls._types

    @classmethod
    def get(cls, type: str) -> type[Project]:
        try:
            return cls.all()[type]
        except KeyError:
            raise SlopError(f"unknown project type {type!r} ({', '.join(cls.all())})") from None
