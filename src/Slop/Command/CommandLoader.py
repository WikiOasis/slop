import Slop.Command
from Slop.Autoload.Autoloader import Autoloader
from Slop.Command.Command import Command


class CommandLoader:
    _commands: dict[str, Command] | None = None

    @classmethod
    def all(cls) -> dict[str, Command]:
        if cls._commands is None:
            cls._commands = {klass.name: klass() for klass in Autoloader.subclasses(Slop.Command, Command)}
        return cls._commands
