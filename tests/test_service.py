import yaml

from Support import Support


class ServiceTest(Support):
    def setUp(self) -> None:
        super().setUp()
        self.farm()
        self.svc = self.upstream("svc", {"app.php": "<?php echo 1;\n", "keep.txt": "repo\n"})
        path = self.conf / "slop.yaml"
        data = yaml.safe_load(path.read_text())
        data["projects"]["svc"] = {
            "type": "service",
            "path": "svc-build",
            "url": self.url("svc"),
            "branch": "main",
            "build": ["echo built > built.txt"],
            "deploy": {
                "path": str(self.tmp / "live-svc"),
                "roles": ["canary"],
                "exclude": [".git", ".slop", "/local"],
                "once": ["echo once >> {path}/once.log"],
                "after-sync": ["test ! -e {path}/BROKEN"],
                "restart": [],
            },
        }
        path.write_text(yaml.safe_dump(data, sort_keys=False))
        self.git(self.conf, "commit", "-qam", "svc")

    def live(self, rel: str) -> str:
        return (self.tmp / "live-svc" / rel).read_text()

    def test_service_builds_syncs_and_runs_once(self) -> None:
        (self.tmp / "live-svc" / "local").mkdir(parents=True)
        (self.tmp / "live-svc" / "local" / "env").write_text("secret\n")
        out = self.slop("deploy", "-p", "svc", "-y", "-m", "ship svc")
        self.assertIn("Deployed svc", out)
        self.assertEqual(self.live("app.php"), "<?php echo 1;\n")
        self.assertEqual(self.live("built.txt"), "built\n")
        self.assertEqual(self.live("local/env"), "secret\n")
        self.assertEqual(self.live("once.log").count("once"), 1)
        state = yaml.safe_load((self.conf / "state" / "svc.yaml").read_text())
        self.assertEqual(state["repos"]["."]["base"], self.git(self.svc, "rev-parse", "HEAD"))

    def test_service_failure_rolls_back(self) -> None:
        self.slop("deploy", "-p", "svc", "-y", "-m", "first")
        before = self.git(self.conf, "rev-parse", "HEAD")
        self.push(self.svc, {"BROKEN": "x\n", "app.php": "<?php echo 2;\n"}, "break it")
        out = self.slop("deploy", "-p", "svc", "-y", "-m", "break", expect=1)
        self.assertIn("FAILED", out)
        self.assertEqual(self.live("app.php"), "<?php echo 1;\n")
        self.assertFalse((self.tmp / "live-svc" / "BROKEN").exists())
        self.assertEqual(self.git(self.conf, "rev-parse", "HEAD"), before)
