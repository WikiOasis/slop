import os
import re
from typing import TextIO


class Style:
    CODES = {
        "info": "32",
        "comment": "33",
        "error": "31",
        "question": "36",
        "success": "1;32",
        "warning": "1;33",
        "highlight": "1;36",
        "bold": "1",
        "dim": "2",
        "block": "97;41",
    }
    TAG = re.compile(r"<(/?)(" + "|".join(CODES) + r")>")
    ANSI = re.compile(r"\033\[[0-9;]*[A-Za-z]")

    def __init__(self, decorated: bool):
        self.decorated = decorated

    @classmethod
    def detect(cls, stream: TextIO) -> bool:
        if os.environ.get("NO_COLOR"):
            return False
        if os.environ.get("FORCE_COLOR") or os.environ.get("CLICOLOR_FORCE"):
            return True
        return stream.isatty() and os.environ.get("TERM") != "dumb"

    def format(self, text: str) -> str:
        if not self.decorated:
            return self.TAG.sub("", text)

        stack: list[str] = []

        def replace(match: re.Match) -> str:
            closing, tag = match.groups()
            if closing:
                if tag in stack:
                    stack.reverse()
                    stack.remove(tag)
                    stack.reverse()
            else:
                stack.append(tag)
            return "\033[0m" + "".join(f"\033[{self.CODES[t]}m" for t in stack)

        out = self.TAG.sub(replace, text)
        return out + "\033[0m" if stack or out != text else out

    @classmethod
    def strip(cls, text: str) -> str:
        return cls.ANSI.sub("", cls.TAG.sub("", text))
