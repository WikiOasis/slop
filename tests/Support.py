import io
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml

from Slop.Cli import Cli
from Slop.Context import Context
from Slop.Log.RunLog import RunLog
from Slop.Output.Console import Console

GIT_ENV = {
    "GIT_AUTHOR_NAME": "Test",
    "GIT_AUTHOR_EMAIL": "test@example.org",
    "GIT_COMMITTER_NAME": "Test",
    "GIT_COMMITTER_EMAIL": "test@example.org",
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_PROTOCOL_FROM_USER": "1",
}


class Support(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="slop-test-")).resolve()
        self.environ = dict(os.environ)
        os.environ.update(GIT_ENV)
        os.environ["SLOP_FORWARDED"] = "1"
        os.environ.pop("SLOP_CONFIG", None)
        os.environ.pop("SLOP_ROOT", None)
        os.environ.pop("SLOP_MASTER", None)
        subprocess.run(
            ["git", "config", "--file", str(self.tmp / "gitconfig"), "protocol.file.allow", "always"], check=True
        )
        os.environ["GIT_CONFIG_GLOBAL"] = str(self.tmp / "gitconfig")

    def tearDown(self) -> None:
        os.environ.clear()
        os.environ.update(self.environ)
        subprocess.run(["rm", "-rf", str(self.tmp)], check=False)

    def git(self, cwd: Path, *args: str) -> str:
        return subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True).stdout.strip()

    def upstream(self, name: str, files: dict[str, str], branch: str = "main") -> Path:
        work = self.tmp / "work" / name
        work.mkdir(parents=True)
        self.git(work, "init", "-q", "-b", branch)
        self.write(work, files)
        self.git(work, "add", "-A")
        self.git(work, "commit", "-q", "-m", f"init {name}")
        bare = self.tmp / "upstream" / f"{name}.git"
        bare.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "clone", "-q", "--bare", str(work), str(bare)], check=True)
        self.git(work, "remote", "add", "origin", str(bare))
        return work

    def push(self, work: Path, files: dict[str, str], message: str) -> str:
        self.write(work, files)
        self.git(work, "add", "-A")
        self.git(work, "commit", "-q", "-m", message)
        self.git(work, "push", "-q", "origin", "HEAD")
        return self.git(work, "rev-parse", "HEAD")

    @staticmethod
    def write(root: Path, files: dict[str, str]) -> None:
        for rel, content in files.items():
            path = root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)

    def url(self, name: str) -> str:
        return f"file://{self.tmp / 'upstream' / f'{name}.git'}"

    def context(self, interactive: bool = False) -> Context:
        console = Console(decorated=False, out=io.StringIO(), err=io.StringIO())
        return Context(console, RunLog(None, "test", [], None, False), None, interactive=interactive, jobs=4)

    def farm(self) -> Path:
        self.core = self.upstream(
            "core",
            {
                "composer.json": "{}\n",
                "extension.json": '{\n\t"name": "Thing",\n\t"version": "1.0"\n}\n',
                "includes/Main.php": "<?php\necho 'one';\necho 'two';\necho 'three';\n",
            },
            branch="REL1_46",
        )
        self.ext = self.upstream("Echo", {"extension.json": '{\n\t"name": "Echo"\n}\n'}, branch="REL1_46")
        self.mwconfig = self.upstream(
            "mw-config",
            {
                "LocalSettings.php": "<?php\n",
                "initialise/entrypoints/index.php": "<?php\n",
                "initialise/entrypoints/api.php": "<?php\n",
            },
        )
        conf = self.tmp / "conf"
        (conf / "mediawiki" / "patches").mkdir(parents=True)
        self.dump(
            conf / "slop.yaml",
            {
                "root": str(self.tmp / "srv"),
                "log-dir": str(self.tmp / "log"),
                "lock-dir": str(self.tmp / "locks"),
                "default": "mediawiki",
                "sal": {"enabled": False},
                "config-repo": {"pull": False, "push": False},
                "projects": {
                    "mediawiki": {
                        "type": "mediawiki",
                        "path": "build",
                        "mw-config": {"url": self.url("mw-config"), "branch": "main"},
                        "versions": {"1.46": {"core": {"url": self.url("core")}}},
                        "entrypoints": ["index.php", "api.php"],
                        "stages": {"build": ["layout", "sync", "patches", "links"]},
                        "deploy": {
                            "path": str(self.tmp / "live"),
                            "roles": ["canary", "app"],
                            "l10n": {"when": "never"},
                            "after-sync": ['grep -q \'"name": "Thing"\' {path}/versions/1.46/extension.json'],
                        },
                    }
                },
            },
        )
        self.dump(
            conf / "inventory.yaml",
            {
                "ssh": {"sudo": False},
                "local": ["canary-1", "app-1", "app-2"],
                "hosts": ["canary-1", "app-1", "app-2"],
                "roles": {"canary": {"hosts": ["canary-*"]}, "app": {"hosts": ["app-*"], "batch": 2}},
            },
        )
        self.dump(
            conf / "mediawiki" / "repos-1.46.yaml",
            {"extensions": [{"path": "extensions/Echo", "url": self.url("Echo")}]},
        )
        self.dump(conf / "mediawiki" / "patches-1.46.yaml", {"patches": []})
        self.git(conf, "init", "-q", "-b", "main")
        self.git(conf, "add", "-A")
        self.git(conf, "commit", "-q", "-m", "config")
        self.conf = conf
        return conf

    @staticmethod
    def dump(path: Path, data: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.safe_dump(data, sort_keys=False))

    def slop(self, *args: str, expect: int = 0) -> str:
        out, err = io.StringIO(), io.StringIO()
        import sys

        saved = sys.stdout, sys.stderr
        sys.stdout, sys.stderr = out, err
        try:
            code = Cli.main(["-c", str(self.conf), "--no-ansi", *args])
        finally:
            sys.stdout, sys.stderr = saved
        text = out.getvalue() + err.getvalue()
        self.assertEqual(code, expect, text)
        return text

    def state(self) -> dict:
        return yaml.safe_load((self.conf / "state" / "mediawiki.yaml").read_text())
