import re
from dataclasses import dataclass
from urllib.parse import quote

from Slop.Forge.Http import Http
from Slop.SlopError import SlopError


@dataclass(frozen=True)
class Gerrit:
    base: str

    URL = re.compile(r"(?:/c/(?P<project>.+?)/\+/|/r/(?:#/c/)?)(?P<number>\d+)(?:/(?P<patchset>\d+))?/?$")

    @classmethod
    def parseRef(cls, text: str) -> tuple[int, int | None]:
        text = text.strip().removeprefix("gerrit:")
        if match := re.fullmatch(r"(\d+)(?:[,/](\d+))?", text):
            return int(match.group(1)), int(match.group(2)) if match.group(2) else None
        if match := cls.URL.search(text):
            return int(match.group("number")), int(match.group("patchset")) if match.group("patchset") else None
        raise SlopError(f"{text!r} is not a Gerrit change (use 1234567, 1234567,3 or a change URL)")

    def change(self, number: int, patchset: int | None = None) -> dict:
        data = Http.json(f"{self.base.rstrip('/')}/changes/{number}?o=ALL_REVISIONS&o=CURRENT_COMMIT", strip=")]}'")
        revisions = data.get("revisions") or {}
        if not revisions:
            raise SlopError(f"Gerrit change {number} has no patch sets")
        bySet = {rev["_number"]: (sha, rev) for sha, rev in revisions.items()}
        wanted = patchset or max(bySet)
        if wanted not in bySet:
            raise SlopError(f"Gerrit change {number} has no patch set {wanted} (latest is {max(bySet)})")
        sha, revision = bySet[wanted]
        return {
            "number": number,
            "patchset": wanted,
            "latest": max(bySet),
            "commit": sha,
            "project": data["project"],
            "branch": data.get("branch"),
            "subject": data.get("subject", ""),
            "status": data.get("status"),
            "ref": revision.get("ref"),
            "url": f"{self.base.rstrip('/')}/c/{quote(data['project'], safe='/')}/+/{number}",
        }

    @staticmethod
    def pathFor(project: str) -> str:
        if project == "mediawiki/core":
            return "."
        for prefix in ("mediawiki/extensions/", "mediawiki/skins/"):
            if project.startswith(prefix):
                return ("extensions/" if "extensions" in prefix else "skins/") + project[len(prefix) :]
        return project.rsplit("/", 1)[-1]
