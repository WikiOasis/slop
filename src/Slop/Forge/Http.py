import json
import os
import urllib.error
import urllib.request

from Slop import VERSION
from Slop.SlopError import SlopError


class Http:
    @staticmethod
    def json(
        url: str,
        *,
        method: str = "GET",
        body: dict | None = None,
        headers: dict | None = None,
        strip: str = "",
        timeout: float = 20,
    ):
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(
            url,
            data=data,
            method=method,
            headers={
                "User-Agent": f"slop/{VERSION}",
                "Accept": "application/json",
                **({"Content-Type": "application/json"} if body is not None else {}),
                **(headers or {}),
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                text = response.read().decode()
        except urllib.error.HTTPError as e:
            raise SlopError(f"{method} {url} returned HTTP {e.code}") from None
        except (urllib.error.URLError, OSError) as e:
            raise SlopError(f"cannot reach {url}: {getattr(e, 'reason', e)}") from None
        if strip and text.startswith(strip):
            text = text[len(strip) :]
        try:
            return json.loads(text) if text.strip() else {}
        except ValueError:
            raise SlopError(f"{url} did not return JSON") from None

    @staticmethod
    def githubHeaders() -> dict:
        token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
        return {"Authorization": f"Bearer {token}"} if token else {}
