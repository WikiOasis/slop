import os
import shutil
import sys
import textwrap
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import TextIO

from Slop.Output.ProgressBar import ProgressBar
from Slop.Output.Style import Style


class Console:
    def __init__(self, decorated: bool | None = None, out: TextIO | None = None, err: TextIO | None = None):
        self.out = out or sys.stdout
        self.err = err or sys.stderr
        out, err = self.out, self.err
        self.lock = threading.RLock()
        self.interactive = err.isatty() and os.environ.get("TERM") != "dumb"
        self.style = Style(Style.detect(out) if decorated is None else decorated)
        self.bar: ProgressBar | None = None
        self.paused = False

    @property
    def barShown(self) -> bool:
        return self.interactive and self.bar is not None and not self.paused

    @property
    def width(self) -> int:
        return shutil.get_terminal_size().columns

    def write(self, text: str = "", *, err: bool = False) -> None:
        with self.lock:
            self.clearBar()
            print(self.style.format(text), file=self.err if err else self.out, flush=True)
            self.drawBar()

    def section(self, title: str) -> None:
        self.write()
        self.write(f"<info>{title}</info>")

    def success(self, text: str) -> None:
        self.write(f"  <success>✔</success> {text}")

    def warning(self, text: str) -> None:
        lines = text.splitlines() or [""]
        self.write(f"  <warning>⚠</warning> <comment>{lines[0]}</comment>", err=True)
        for line in lines[1:]:
            self.write(f"    <dim>{line}</dim>", err=True)

    def errorBlock(self, title: str, text: str) -> None:
        maxText = max(self.width - 6, 20)
        lines = [f"[{title}]"]
        for line in text.splitlines() or [""]:
            lines += textwrap.wrap(line, maxText, break_on_hyphens=False) or [""]
        width = max(len(line) for line in lines) + 4
        pad = " " * width
        self.write(err=True)
        self.write(f"<block>{pad}</block>", err=True)
        for line in lines:
            self.write(f"<block>  {line.ljust(width - 2)}</block>", err=True)
        self.write(f"<block>{pad}</block>", err=True)
        self.write(err=True)

    def progress(self, total: int, verb: str, noun: str, onClose: Callable[[str], None] | None = None) -> ProgressBar:
        return ProgressBar(self, total, verb, noun, onClose)

    @contextmanager
    def pause(self) -> Iterator[None]:
        with self.lock:
            self.clearBar()
            self.paused = True
        try:
            yield
        finally:
            with self.lock:
                self.paused = False
                self.drawBar()

    def ask(self, question: str, options: dict[str, str], default: str | None = None) -> str:
        with self.pause():
            self.write()
            self.write(f"<question>{question}</question>")
            for key, label in options.items():
                marker = " <dim>(default)</dim>" if key == default else ""
                self.write(f"  <info>[{key}]</info> {label}{marker}")
            while True:
                try:
                    answer = input(self.style.format("<question>> </question>")).strip().lower()
                except EOFError:
                    answer = ""
                if not answer and default:
                    return default
                if answer in options:
                    return answer
                self.write(f"<comment>Choose one of: {', '.join(options)}</comment>")

    def confirm(self, question: str, default: bool = False) -> bool:
        hint = "[Y/n]" if default else "[y/N]"
        with self.pause():
            try:
                answer = input(self.style.format(f"<question>{question}</question> {hint} ")).strip().lower()
            except EOFError:
                answer = ""
        if not answer:
            return default
        return answer in ("y", "yes")

    def attach(self, bar: ProgressBar) -> None:
        with self.lock:
            self.clearBar()
            self.bar = bar
            self.drawBar()

    def detach(self, bar: ProgressBar) -> None:
        with self.lock:
            if self.bar is bar:
                self.clearBar()
                self.bar = None

    def redraw(self) -> None:
        with self.lock:
            self.drawBar()

    def clearBar(self) -> None:
        if self.barShown:
            self.err.write("\r\033[2K")
            self.err.flush()

    def drawBar(self) -> None:
        if self.barShown:
            self.err.write("\r\033[2K" + self.style.format(self.bar.render(self.width)))
            self.err.flush()
