import logging
from pathlib import Path

from Slop.Config.YamlFile import YamlFile
from Slop.Context import Context
from Slop.Forge.Http import Http
from Slop.SlopError import SlopError


class Notifier:
    def __init__(self, ctx: Context, settings: dict):
        self.ctx = ctx
        secrets = (
            YamlFile.read(Path(settings.get("secrets-file", "/etc/slop/secrets.yaml")), missingOk=True)
            if settings
            else {}
        )
        self.discord = settings.get("discord") or secrets.get("discord-webhook")
        self.slack = settings.get("slack") or secrets.get("slack-webhook")

    def send(self, text: str, ok: bool = True) -> None:
        if self.ctx.dryRun:
            return
        targets = []
        if self.discord:
            colour = 0x2ECC71 if ok else 0xE74C3C
            targets.append((self.discord, {"embeds": [{"description": text[:4000], "color": colour}]}))
        if self.slack:
            targets.append((self.slack, {"text": (":white_check_mark: " if ok else ":x: ") + text[:3000]}))
        for url, body in targets:
            try:
                Http.json(url, method="POST", body=body, timeout=10)
            except SlopError as e:
                self.ctx.event(logging.WARNING, f"notification failed: {e}", event="notify.error")
