import json
import os
import subprocess
from unittest import mock

import yaml

from Slop.Config.ConfigRepo import ConfigRepo
from Slop.Deploy.Lock import Lock
from Slop.SlopError import SlopError
from Support import Support


class LockTest(Support):
    def setUp(self) -> None:
        super().setUp()
        self.farm()
        self.svc = self.upstream("svc", {"app.php": "<?php echo 1;\n"})
        path = self.conf / "slop.yaml"
        data = yaml.safe_load(path.read_text())
        data["projects"]["svc"] = {
            "type": "service",
            "path": "svc-build",
            "url": self.url("svc"),
            "branch": "main",
            "deploy": {"path": str(self.tmp / "live-svc"), "roles": ["canary"]},
        }
        path.write_text(yaml.safe_dump(data, sort_keys=False))
        inventory = self.conf / "inventory.yaml"
        data = yaml.safe_load(inventory.read_text())
        data["services"] = {"web": {"units": ["web"]}}
        data["roles"]["app"]["services"] = ["web"]
        inventory.write_text(yaml.safe_dump(data, sort_keys=False))
        self.git(self.conf, "commit", "-qam", "svc")

    def as_user(self, name: str) -> None:
        os.environ["SLOP_INVOKER"] = f"{name}@test"

    def test_lock_several_projects_and_unlock(self) -> None:
        self.as_user("alice")
        out = self.slop("lock", "mediawiki", "svc", "-m", "freeze")
        self.assertIn("locked mediawiki", out)
        self.assertIn("locked svc", out)
        self.assertTrue((self.conf / "locks" / "svc.yaml").exists())
        self.as_user("bob")
        out = self.slop("deploy", "-p", "svc", "-y", "-m", "nope", expect=1)
        self.assertIn("svc is locked by alice", out)
        out = self.slop("locks")
        self.assertIn("svc", out)
        self.assertIn("mediawiki", out)
        self.slop("unlock", "svc", "mediawiki", expect=1)
        self.slop("unlock", "svc", "mediawiki", "--force")
        self.assertFalse((self.conf / "locks" / "svc.yaml").exists())
        self.slop("deploy", "-p", "svc", "-y", "-m", "now fine")

    def test_lock_all_blocks_every_project(self) -> None:
        self.as_user("alice")
        self.slop("lock", "--all", "-m", "incident")
        self.as_user("bob")
        out = self.slop("deploy", "-p", "svc", "-y", "-m", "nope", expect=1)
        self.assertIn("all deploys are locked by alice", out)
        out = self.slop("restart", "web", expect=1)
        self.assertIn("locked by alice", out)

    def test_restart_respects_project_lock(self) -> None:
        self.as_user("alice")
        self.slop("lock", "mediawiki", "-m", "freeze")
        self.as_user("bob")
        out = self.slop("restart", "web", expect=1)
        self.assertIn("mediawiki is locked by alice", out)

    def test_lock_pushed_during_a_build_is_caught_before_rollout(self) -> None:
        bare = self.tmp / "conf.git"
        subprocess.run(["git", "clone", "-q", "--bare", str(self.conf), str(bare)], check=True)
        self.git(self.conf, "remote", "add", "origin", str(bare))
        self.git(self.conf, "fetch", "-q", "origin")
        self.git(self.conf, "branch", "-q", "--set-upstream-to=origin/main", "main")
        other = self.tmp / "other"
        subprocess.run(["git", "clone", "-q", str(bare), str(other)], check=True)
        self.as_user("alice")
        Lock(other / "locks", ConfigRepo(other), push=True).hold("svc", "freeze", None)
        self.as_user("bob")
        locks = Lock(self.tmp / "locks-local", ConfigRepo(self.conf), push=True)
        locks.checkHeld("svc")
        with self.assertRaises(SlopError) as caught:
            locks.checkRemote("svc")
        self.assertIn("svc is locked by alice", str(caught.exception))
        self.as_user("alice")
        locks.checkRemote("svc")


class SalTest(Support):
    def setUp(self) -> None:
        super().setUp()
        self.farm()
        path = self.conf / "slop.yaml"
        data = yaml.safe_load(path.read_text())
        client = self.tmp / "client.json"
        client.write_text(json.dumps({"server_url": "http://sal.test", "token": "t", "host": "staging"}))
        data["sal"] = {"enabled": True, "client-config": str(client)}
        path.write_text(yaml.safe_dump(data, sort_keys=False))
        self.git(self.conf, "commit", "-qam", "sal")

    def collect(self, args: tuple[str, ...], expect: int = 0) -> list[str]:
        calls = []

        def fake(url, *, method="GET", body=None, **_):
            calls.append((method, url, body))
            return {"id": len(calls)}

        with mock.patch("Slop.Sal.Sal.Http.json", side_effect=fake):
            self.slop(*args, expect=expect)
        self.assertTrue(all(c[0] == "POST" and c[1].endswith("/v1/entries") for c in calls))
        return [c[2]["message"] for c in calls]

    def test_deploy_logs_a_start_and_an_end_summary(self) -> None:
        messages = self.collect(("deploy", "-y", "-m", "first", "-t", "T9"))
        self.assertEqual(len(messages), 2)
        self.assertTrue(messages[0].startswith("Deploying mediawiki: first (T9)"))
        self.assertTrue(messages[1].startswith("Deployed mediawiki: first (T9)"))

    def test_failed_deploy_logs_the_reason_then_a_summary(self) -> None:
        self.slop("deploy", "-y", "-m", "first")
        self.push(self.core, {"extension.json": '{\n\t"name": "Broken"\n}\n'}, "break it")
        messages = self.collect(("deploy", "-y", "-m", "break"), expect=1)
        self.assertEqual(len(messages), 3)
        self.assertTrue(messages[0].startswith("Deploying mediawiki: break"))
        self.assertIn("FAILED on canary-1", messages[1])
        self.assertTrue(messages[2].startswith("Deploy of mediawiki ended: rolled canary-1 back to"))


class ConfigRepoTest(Support):
    def setUp(self) -> None:
        super().setUp()
        self.farm()
        self.bare = self.tmp / "conf.git"
        subprocess.run(["git", "clone", "-q", "--bare", str(self.conf), str(self.bare)], check=True)
        self.git(self.conf, "remote", "add", "origin", str(self.bare))
        self.git(self.conf, "fetch", "-q", "origin")
        self.git(self.conf, "branch", "-q", "--set-upstream-to=origin/main", "main")
        path = self.conf / "slop.yaml"
        data = yaml.safe_load(path.read_text())
        data["config-repo"] = {"pull": True, "push": True}
        path.write_text(yaml.safe_dump(data, sort_keys=False))
        self.git(self.conf, "commit", "-qam", "enable sync")
        self.git(self.conf, "push", "-q", "origin", "main")

    def remote_head(self) -> str:
        return self.git(self.bare, "rev-parse", "main")

    def test_patch_edit_is_committed_and_pushed(self) -> None:
        patch = self.tmp / "version.patch"
        patch.write_text(
            '--- a/extension.json\n+++ b/extension.json\n@@ -1,4 +1,4 @@\n {\n \t"name": "Thing",\n'
            '-\t"version": "1.0"\n+\t"version": "1.1"\n }\n'
        )
        out = self.slop("patch", "add", str(patch), "--path", ".", "-d", "Bump", "-t", "T3", "-m", "bump version")
        self.assertIn("pushed to origin/main", out)
        self.assertEqual(self.remote_head(), self.git(self.conf, "rev-parse", "HEAD"))
        message = self.git(self.conf, "log", "-1", "--format=%B")
        self.assertIn("slop patch add", message)
        self.assertIn("bump version", message)
        self.assertIn("Bug: T3", message)
        self.assertEqual(self.git(self.conf, "status", "--porcelain"), "")

    def test_commands_pull_remote_changes_first(self) -> None:
        other = self.tmp / "other"
        subprocess.run(["git", "clone", "-q", str(self.bare), str(other)], check=True)
        self.write(other, {"mediawiki/note.txt": "from elsewhere\n"})
        self.git(other, "add", "-A")
        self.git(other, "commit", "-q", "-m", "elsewhere")
        self.git(other, "push", "-q", "origin", "main")
        self.assertNotEqual(self.git(self.conf, "rev-parse", "HEAD"), self.remote_head())
        self.slop("hosts")
        self.assertEqual(self.git(self.conf, "rev-parse", "HEAD"), self.remote_head())
        self.assertTrue((self.conf / "mediawiki" / "note.txt").exists())
