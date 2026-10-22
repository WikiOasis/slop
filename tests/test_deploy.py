import json

import yaml

from Support import Support


class DeployTest(Support):
    def setUp(self) -> None:
        super().setUp()
        self.farm()

    def live(self, rel: str) -> str:
        return (self.tmp / "live" / rel).read_text()

    def test_deploy_records_state_and_commits_config(self) -> None:
        out = self.slop("deploy", "-y", "-m", "first", "-t", "T1")
        self.assertIn("Deployed mediawiki: first (T1)", out)
        state = self.state()
        self.assertEqual(state["tasks"], ["T1"])
        self.assertEqual(state["repos"]["versions/1.46"]["base"], self.git(self.core, "rev-parse", "HEAD"))
        self.assertIn('"name": "Thing"', self.live("versions/1.46/extension.json"))
        self.assertTrue((self.tmp / "live" / "w" / "index.php").is_symlink())
        marker = json.loads(self.live(".slop-state.json"))
        self.assertEqual(marker["run"], state["run"])
        log = self.git(self.conf, "log", "-1", "--format=%B")
        self.assertIn("Bug: T1", log)
        self.assertEqual(self.git(self.conf, "status", "--porcelain"), "")

    def test_nothing_to_deploy(self) -> None:
        self.slop("deploy", "-y", "-m", "first")
        out = self.slop("deploy", "-y")
        self.assertIn("nothing to deploy", out)

    def test_failed_deploy_rolls_back_to_statefile(self) -> None:
        self.slop("deploy", "-y", "-m", "first")
        before = self.git(self.conf, "rev-parse", "HEAD")
        good = self.live("versions/1.46/extension.json")
        self.push(self.core, {"extension.json": '{\n\t"name": "Broken"\n}\n'}, "break it")

        out = self.slop("deploy", "-y", "-m", "break", "-t", "T2", expect=1)
        self.assertIn("FAILED on canary-1", out)
        self.assertIn("rolled canary-1 back to", out)
        self.assertEqual(self.live("versions/1.46/extension.json"), good)
        self.assertEqual(self.git(self.conf, "rev-parse", "HEAD"), before)
        self.assertFalse((self.tmp / "live").joinpath("app").exists())

    def test_patch_add_and_deploy_then_manual_rollback(self) -> None:
        self.slop("deploy", "-y", "-m", "first")
        first = self.git(self.conf, "rev-parse", "HEAD")
        patch = self.tmp / "version.patch"
        patch.write_text(
            '--- a/extension.json\n+++ b/extension.json\n@@ -1,4 +1,4 @@\n {\n \t"name": "Thing",\n'
            '-\t"version": "1.0"\n+\t"version": "1.1"\n }\n'
        )
        self.slop("patch", "add", str(patch), "--path", ".", "-d", "Bump version", "-t", "T3")
        out = self.slop("deploy", "-y", "-m", "bump")
        self.assertIn("(T3)", out)
        self.assertIn('"1.1"', self.live("versions/1.46/extension.json"))
        patches = self.state()["repos"]["versions/1.46"]["patches"]
        self.assertEqual(patches[0]["name"], "version")

        out = self.slop("rollback", "-y", "-m", "undo")
        self.assertIn(f"Rolled back mediawiki to config {first[:7]}", out)
        self.assertIn('"1.0"', self.live("versions/1.46/extension.json"))
        entries = yaml.safe_load((self.conf / "mediawiki" / "patches-1.46.yaml").read_text())["patches"]
        self.assertEqual(entries, [])
        self.assertEqual(self.state()["rollback-of"][:7], self.git(self.conf, "rev-parse", "HEAD~1")[:7])

    def test_pin_deploys_exact_commit(self) -> None:
        first = self.git(self.ext, "rev-parse", "HEAD")
        self.push(self.ext, {"extension.json": '{\n\t"name": "Echo",\n\t"v": 2\n}\n'}, "v2")
        self.slop("deploy", "-y", "-m", "first")
        self.slop("repo", "pin", "Echo", first, "-t", "T4")
        out = self.slop("deploy", "extensions", "Echo", "-y", "-m", "pin echo")
        self.assertIn("(T4)", out)
        self.assertNotIn('"v"', self.live("versions/1.46/extensions/Echo/extension.json"))
        self.assertEqual(self.state()["repos"]["versions/1.46/extensions/Echo"]["base"], first)

    def test_partial_deploy_only_syncs_config(self) -> None:
        self.slop("deploy", "-y", "-m", "first")
        self.push(self.mwconfig, {"LocalSettings.php": "<?php\n$x = 1;\n"}, "setting")
        self.push(self.core, {"extension.json": '{\n\t"name": "Thing",\n\t"version": "2.0"\n}\n'}, "core change")
        self.slop("deploy", "config", "-y", "-m", "config only")
        self.assertIn("$x = 1", self.live("config/LocalSettings.php"))
        self.assertIn('"1.0"', self.live("versions/1.46/extension.json"))
        self.assertNotEqual(self.state()["repos"]["config"]["base"], None)

    def test_lock_blocks_other_users(self) -> None:
        import os

        self.slop("lock", "-m", "maintenance")
        os.environ["SLOP_INVOKER"] = "someone@else"
        out = self.slop("deploy", "-y", expect=1)
        self.assertIn("locked by", out)
        out = self.slop("unlock", expect=1)
        self.assertIn("--force", out)
        self.slop("unlock", "--force")
        self.slop("deploy", "-y", "-m", "after unlock")

    def test_lock_pushed_from_another_clone_blocks_deploys(self) -> None:
        import os
        import shutil
        import subprocess

        remote = self.tmp / "conf-remote.git"
        subprocess.run(["git", "clone", "-q", "--bare", str(self.conf), str(remote)], check=True)
        self.git(self.conf, "remote", "add", "origin", str(remote))
        self.git(self.conf, "fetch", "-q", "origin")
        self.git(self.conf, "branch", "-q", "--set-upstream-to=origin/main", "main")
        for name in ("slop.yaml",):
            data = yaml.safe_load((self.conf / name).read_text())
            data["config-repo"] = {"pull": True, "push": True}
            (self.conf / name).write_text(yaml.safe_dump(data, sort_keys=False))
        self.git(self.conf, "commit", "-qam", "enable push")
        self.git(self.conf, "push", "-q", "origin", "main")

        laptop = self.tmp / "laptop"
        subprocess.run(["git", "clone", "-q", str(remote), str(laptop)], check=True)
        deployer = self.conf
        self.conf = laptop
        os.environ["SLOP_INVOKER"] = "someone@laptop"
        self.slop("lock", "-m", "schema change T5")
        self.assertIn("locks/mediawiki.yaml", self.git(remote, "show", "--stat", "--format=%s", "main"))

        self.conf = deployer
        os.environ["SLOP_INVOKER"] = "deployer@master"
        out = self.slop("deploy", "-y", "-m", "should be blocked", expect=1)
        self.assertIn("locked by someone@laptop", out)
        self.assertFalse((self.tmp / "live").exists())

        self.conf = laptop
        os.environ["SLOP_INVOKER"] = "someone@laptop"
        self.slop("unlock")
        self.conf = deployer
        os.environ["SLOP_INVOKER"] = "deployer@master"
        self.slop("deploy", "-y", "-m", "after unlock")
        self.assertIn("Deploy mediawiki: after unlock", self.git(remote, "log", "-1", "--format=%s", "main"))
        shutil.rmtree(laptop, ignore_errors=True)

    def test_dry_run_changes_nothing(self) -> None:
        self.slop("deploy", "-y", "-n")
        self.assertFalse((self.tmp / "live").exists())
        self.assertFalse((self.conf / "state" / "mediawiki.yaml").exists())
