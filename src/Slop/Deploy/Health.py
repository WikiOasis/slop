import shlex
import time

from Slop.Config.Inventory import Inventory
from Slop.Deploy.CheckResult import CheckResult
from Slop.Remote.Ssh import Ssh
from Slop.SlopError import SlopError


class Health:
    MARK = "SLOPCHECK"
    FORBID = [
        "Fatal error",
        "Parse error",
        "MediaWiki internal error",
        "Exception caught inside exception handler",
        "Internal error - ",
    ]

    def __init__(self, ssh: Ssh, spec: dict):
        self.ssh = ssh
        self.spec = spec or {}
        self.tests = [dict(t) for t in self.spec.get("tests") or []]
        if not self.tests and self.spec.get("path"):
            self.tests = [
                {"name": "health", **{k: self.spec[k] for k in ("path", "expect", "status") if k in self.spec}}
            ]
        self.forbid = [str(f) for f in self.spec.get("forbid", self.FORBID) or []]

    @property
    def enabled(self) -> bool:
        return bool(self.tests)

    def testsFor(self, role: str | None) -> list[dict]:
        return [t for t in self.tests if not t.get("roles") or role is None or role in t["roles"]]

    def name(self, test: dict) -> str:
        return str(test.get("name") or test.get("path"))

    def script(self, tests: list[dict]) -> str:
        base = str(self.spec.get("url", "http://127.0.0.1")).rstrip("/")
        timeout = str(self.spec.get("timeout", 15))
        lines = ["tmp=$(mktemp)", "trap 'rm -f \"$tmp\"' EXIT"]
        for i, test in enumerate(tests):
            curl = ["curl", "-sS", "--max-time", timeout, "-w", "%{http_code} %{time_total}"]
            host = test.get("host", self.spec.get("host"))
            if host:
                curl += ["-H", f"Host: {host}"]
            for header in test.get("headers") or []:
                curl += ["-H", str(header)]
            curl.append(base + str(test.get("path", "/")))
            command = shlex.join(curl[:1]) + ' -o "$tmp" ' + shlex.join(curl[1:])
            expect = test.get("expect")
            found = f'$(grep -qiF -- {shlex.quote(str(expect))} "$tmp" && echo 1 || echo 0)' if expect else "1"
            forbid = [*self.forbid, *(test.get("forbid") or [])]
            bad = " ".join(
                f'grep -qF -- {shlex.quote(f)} "$tmp" && {{ echo {j}; exit; }};' for j, f in enumerate(forbid)
            )
            lines.append(f': > "$tmp"; out=$({command} 2>/dev/null); [ -n "$out" ] || out="000 0"')
            lines.append(f"bad=$( ( {bad} echo - ) )")
            lines.append(f'echo "{self.MARK} {i} $out {found} $bad"')
        return "\n".join(lines)

    def runOnce(self, host: str, tests: list[dict]) -> list[CheckResult]:
        proc = self.ssh.run(host, self.script(tests), check=False, mutates=False, timeout=180)
        results: dict[int, CheckResult] = {}
        for line in (proc.stdout or "").splitlines():
            parts = line.split()
            if len(parts) != 6 or parts[0] != self.MARK:
                continue
            index, status, seconds, found, bad = int(parts[1]), parts[2], float(parts[3]), parts[4], parts[5]
            test = tests[index]
            forbid = [*self.forbid, *(test.get("forbid") or [])]
            wanted = str(test.get("status", 200))
            if status != wanted:
                result = CheckResult(self.name(test), False, f"HTTP {status} (expected {wanted})", seconds)
            elif bad != "-":
                result = CheckResult(self.name(test), False, f"page contains {forbid[int(bad)]!r}", seconds)
            elif found != "1":
                result = CheckResult(self.name(test), False, f"page does not contain {test.get('expect')!r}", seconds)
            else:
                result = CheckResult(self.name(test), True, f"HTTP {status}", seconds)
            results[index] = result
        for i, test in enumerate(tests):
            if i not in results:
                lines = (proc.stderr or "").strip().splitlines()
                results[i] = CheckResult(self.name(test), False, lines[-1] if lines else "no result", 0.0)
        return [results[i] for i in range(len(tests))]

    def run(self, host: str, role: str | None = None) -> list[CheckResult]:
        tests = self.testsFor(role)
        if not tests:
            return []
        results = self.runOnce(host, tests)
        for _ in range(int(self.spec.get("retries", 3)) - 1):
            failing = [i for i, r in enumerate(results) if not r.ok]
            if not failing:
                break
            time.sleep(float(self.spec.get("delay", 2)))
            for i, result in zip(failing, self.runOnce(host, [tests[i] for i in failing]), strict=True):
                results[i] = result
        return results

    def check(self, host: str, role: str | None = None) -> str:
        results = self.run(host, role)
        failed = [r for r in results if not r.ok]
        if failed:
            raise SlopError(
                f"{Inventory.short(host)}: {len(failed)} of {len(results)} smoke tests failed: "
                + "; ".join(f"{r.name}: {r.detail}" for r in failed)
            )
        slowest = max((r.seconds for r in results), default=0.0)
        return f"{len(results)}/{len(results)} smoke tests passed, slowest {slowest:.2f}s"
