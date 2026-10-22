import getpass
import os
import socket


class Invoker:
    @staticmethod
    def user() -> str:
        for candidate in (os.environ.get("SUDO_USER"), os.environ.get("SLOP_INVOKER")):
            if candidate and candidate.split("@", 1)[0] != "root":
                return candidate.split("@", 1)[0]
        return getpass.getuser()

    @staticmethod
    def host() -> str:
        invoker = os.environ.get("SLOP_INVOKER", "")
        if "@" in invoker:
            return invoker.split("@", 1)[1]
        return socket.gethostname().split(".", 1)[0]

    @classmethod
    def label(cls) -> str:
        return f"{cls.user()}@{cls.host()}"
