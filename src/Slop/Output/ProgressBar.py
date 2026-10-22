import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import TYPE_CHECKING

from Slop.Output.Duration import Duration
from Slop.Output.Style import Style

if TYPE_CHECKING:
    from Slop.Output.Console import Console


class ProgressBar:
    SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
    WIDTH = 28
    TICK = 0.08

    def __init__(
        self, console: "Console", total: int, verb: str, noun: str, onClose: Callable[[str], None] | None = None
    ):
        self.console = console
        self.onClose = onClose
        self.total = total
        self.verb = verb
        self.noun = noun
        self.done = 0
        self.failed = 0
        self.active: list[str] = []
        self.message = ""
        self.started = time.monotonic()
        self.frame = 0
        self.stopped = threading.Event()
        self.ticker: threading.Thread | None = None

    def __enter__(self) -> "ProgressBar":
        self.started = time.monotonic()
        self.console.attach(self)
        if self.console.interactive:
            self.ticker = threading.Thread(target=self.tick, daemon=True)
            self.ticker.start()
        return self

    def __exit__(self, *exc) -> None:
        self.stopped.set()
        if self.ticker:
            self.ticker.join()
        self.console.detach(self)
        ok = self.done - self.failed
        noun = self.noun if ok == 1 else self.noun + "s"
        summary = f"{self.verb} <info>{ok}</info> {noun} <dim>in {Duration.format(self.elapsed)}</dim>"
        if self.failed:
            summary += f", <error>{self.failed} failed</error>"
        if self.onClose:
            self.onClose(summary)
        if self.failed:
            self.console.write(f"  <error>✖</error> {summary}")
        elif exc[0] is None:
            self.console.success(summary)

    @contextmanager
    def task(self, name: str) -> Iterator[None]:
        with self.console.lock:
            self.active.append(name)
            self.console.redraw()
        try:
            yield
        except BaseException:
            self.failed += 1
            raise
        finally:
            with self.console.lock:
                self.active.remove(name)
                self.done += 1
                self.console.redraw()

    def status(self, message: str) -> None:
        with self.console.lock:
            self.message = message
            self.console.redraw()

    def tick(self) -> None:
        while not self.stopped.wait(self.TICK):
            self.frame += 1
            self.console.redraw()

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self.started

    def render(self, width: int) -> str:
        fraction = self.done / self.total if self.total else 1.0
        filled = int(self.WIDTH * fraction)
        if self.console.style.decorated:
            head = "╸" if filled < self.WIDTH else ""
            bar = f"<info>{'━' * filled}{head}</info><dim>{'━' * (self.WIDTH - filled - len(head))}</dim>"
        else:
            bar = "[" + "#" * filled + "-" * (self.WIDTH - filled) + "]"

        spinner = self.SPINNER[self.frame % len(self.SPINNER)]
        count = f"{self.done}/{self.total}".rjust(len(str(self.total)) * 2 + 1)
        line = (
            f"  <highlight>{spinner}</highlight> {bar}  <bold>{count}</bold>"
            f"  <comment>{int(fraction * 100):>3}%</comment>  <dim>{Duration.live(self.elapsed)}</dim>"
        )

        tail = ", ".join(self.active)
        if self.message:
            tail = f"{tail} · {self.message}" if tail else self.message
        room = width - 1 - len(Style.strip(line)) - 2
        if tail and room > 3:
            if len(tail) > room:
                tail = tail[: room - 1] + "…"
            line += f"  <dim>{tail}</dim>"
        return line
