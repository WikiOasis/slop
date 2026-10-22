import json
import logging
import os
import re
from pathlib import Path

from Slop.Context import Context
from Slop.Forge.Http import Http
from Slop.Log.Invoker import Invoker
from Slop.SlopError import SlopError


class Sal:
    LIMIT = 1990
    TASK = re.compile(r"(?<![\w/#.-])(T[1-9][0-9]*)(?![\w-])")

    def __init__(self, ctx: Context, settings: dict):
        self.ctx = ctx
        self.enabled = bool(settings.get("enabled", False))
        self.clientConfig = Path(settings.get("client-config", "/etc/sal/client.json"))
        self.url = os.environ.get("SAL_URL") or settings.get("url")

    @classmethod
    def tasks(cls, *texts: str | None) -> list[str]:
        found: list[str] = []
        for text in texts:
            for match in cls.TASK.finditer(text or ""):
                if match.group(1) not in found:
                    found.append(match.group(1))
        return found

    @classmethod
    def clip(cls, message: str) -> str:
        message = " ".join(message.split())
        return message if len(message) <= cls.LIMIT else message[: cls.LIMIT - 1] + "…"

    def log(self, message: str) -> int | None:
        message = self.clip(message)
        self.ctx.event(logging.INFO, f"SAL: {message}", event="sal", sal_message=message)
        if not self.enabled:
            self.ctx.console.write(f"  <dim>SAL (disabled): {message}</dim>")
            return None
        if self.ctx.dryRun:
            self.ctx.console.write(f"  <dim>SAL (dry run): {message}</dim>")
            return None
        entry = self.request("POST", "/v1/entries", {"user": Invoker.user(), "message": message}, message)
        if entry is None:
            return None
        self.ctx.console.write(f"  <dim>SAL #{entry.get('id', '?')}:</dim> {message}")
        return entry.get("id")

    def request(self, method: str, path: str, body: dict, message: str) -> dict | None:
        try:
            config = json.loads(self.clientConfig.read_text())
            url = (self.url or config["server_url"]).rstrip("/")
            return Http.json(
                f"{url}{path}",
                method=method,
                body=body,
                headers={"Authorization": f"Bearer {config['token']}", "X-SAL-Host": config["host"]},
                timeout=15,
            )
        except (OSError, ValueError, KeyError) as e:
            self.ctx.warn(f"not logged to the SAL ({self.clientConfig}: {e}): {message}")
        except SlopError as e:
            self.ctx.warn(f"not logged to the SAL ({e}): {message}")
        return None
