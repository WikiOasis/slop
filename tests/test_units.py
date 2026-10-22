import tempfile
import unittest
from pathlib import Path

from Slop.Config.Inventory import Inventory
from Slop.Config.Patch import Patch
from Slop.Config.YamlFile import YamlFile
from Slop.Deploy.StateDiff import StateDiff
from Slop.Forge.Gerrit import Gerrit
from Slop.Forge.GitHub import GitHub
from Slop.Sal.Sal import Sal
from Slop.SlopError import SlopError


class UnitTest(unittest.TestCase):
    def test_gerrit_refs(self) -> None:
        self.assertEqual(Gerrit.parseRef("1234567"), (1234567, None))
        self.assertEqual(Gerrit.parseRef("1234567,3"), (1234567, 3))
        self.assertEqual(Gerrit.parseRef("https://gerrit.wikimedia.org/r/c/mediawiki/core/+/1234567/4"), (1234567, 4))
        self.assertEqual(Gerrit.pathFor("mediawiki/extensions/CheckUser"), "extensions/CheckUser")
        self.assertEqual(Gerrit.pathFor("mediawiki/skins/Vector"), "skins/Vector")
        self.assertEqual(Gerrit.pathFor("mediawiki/core"), ".")
        with self.assertRaises(SlopError):
            Gerrit.parseRef("not-a-change")

    def test_github_refs(self) -> None:
        self.assertEqual(GitHub.parseRef("WikiOasis/WikiOasisMagic#42"), ("WikiOasis/WikiOasisMagic", 42))
        self.assertEqual(GitHub.parseRef("https://github.com/WikiOasis/Repo/pull/7"), ("WikiOasis/Repo", 7))

    def test_gerrit_patch_spec(self) -> None:
        patch = Patch.fromSpec(
            {"path": ".", "gerrit": 1234567, "patchset": 3, "project": "mediawiki/core"},
            Path("/nonexistent"),
            "https://gerrit.wikimedia.org/r",
            "test",
        )
        self.assertEqual(patch.ref, "refs/changes/67/1234567/3")
        self.assertEqual(patch.url, "https://gerrit.wikimedia.org/r/mediawiki/core")
        self.assertEqual(patch.name, "gerrit-1234567")

    def test_inventory_globs_and_order(self) -> None:
        inventory = Inventory.fromSpec(
            {
                "hosts": ["mw-1.x", "mw-2.x", "task-1.x"],
                "services": {"php": {"units": ["php8.4-fpm"]}},
                "roles": {"app": {"hosts": ["mw-*"], "services": ["php"]}, "jobs": {"hosts": ["task-*"]}},
            },
            "test",
        )
        self.assertEqual(inventory.role("app").hosts, ["mw-1.x", "mw-2.x"])
        self.assertEqual(inventory.resolveHost("task-1"), "task-1.x")
        with self.assertRaises(SlopError):
            Inventory.fromSpec({"roles": {"x": {"services": ["missing"]}}}, "test")

    def test_yaml_writer_is_stable(self) -> None:
        data = {"a": "1.46", "list": [{"path": "x", "commit": "0123abc"}], "nested": {"k": True}}
        with tempfile.TemporaryDirectory() as d:
            file = Path(d) / "x.yaml"
            self.assertTrue(YamlFile.write(file, data))
            self.assertFalse(YamlFile.write(file, YamlFile.read(file)))
            self.assertIn("  - path: x", file.read_text())

    def test_state_diff(self) -> None:
        old = {"a": {"base": "1", "head": "1"}, "b": {"base": "2", "head": "2"}}
        new = {
            "a": {"base": "3", "head": "3", "task": "T9"},
            "c": {"base": "4", "head": "5", "patches": [{"name": "p", "source": "gerrit 1,2", "task": "T8"}]},
        }
        diff = StateDiff.compare(old, new)
        self.assertEqual(set(diff.changed), {"a"})
        self.assertEqual(diff.added, ["c"])
        self.assertEqual(diff.removed, ["b"])
        self.assertEqual(diff.tasks(), ["T9", "T8"])
        self.assertIn("+gerrit 1,2", diff.summary())

    def test_sal_tasks_and_clip(self) -> None:
        self.assertEqual(Sal.tasks("fix T123 and T45, not XT9", "T123"), ["T123", "T45"])
        self.assertEqual(len(Sal.clip("x " * 2000)), Sal.LIMIT)
