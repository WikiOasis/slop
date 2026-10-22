import re

from Slop.Forge.Http import Http
from Slop.SlopError import SlopError


class GitHub:
    API = "https://api.github.com"

    @staticmethod
    def parseRef(text: str) -> tuple[str, int]:
        text = text.strip().removeprefix("github:")
        if match := re.fullmatch(r"([\w.-]+/[\w.-]+)#(\d+)", text):
            return match.group(1), int(match.group(2))
        if match := re.search(r"github\.com/([\w.-]+/[\w.-]+)/pull/(\d+)", text):
            return match.group(1), int(match.group(2))
        raise SlopError(f"{text!r} is not a GitHub pull request (use Owner/Repo#12 or a PR URL)")

    def pull(self, repo: str, number: int) -> dict:
        data = Http.json(f"{self.API}/repos/{repo}/pulls/{number}", headers=Http.githubHeaders())
        return {
            "repo": repo,
            "number": number,
            "commit": data["head"]["sha"],
            "commits": int(data.get("commits") or 1),
            "title": data.get("title", ""),
            "state": data.get("state"),
            "merged": bool(data.get("merged")),
            "url": data.get("html_url"),
            "base": data["base"]["ref"],
        }
