from pathlib import Path

from Slop.Config.Patch import Patch
from Slop.Config.Repo import Repo
from Slop.Git.Git import Git
from Slop.SlopError import SlopError
from Support import Support


class GitTest(Support):
    def setUp(self) -> None:
        super().setUp()
        self.work = self.upstream("thing", {"a.txt": "one\ntwo\nthree\n"})
        self.repo = Repo(path=Path("."), url=self.url("thing"), branch="main")
        self.dest = self.tmp / "checkout"

    def filePatch(self, name: str, old: str, new: str) -> Patch:
        patch = self.tmp / "patches" / f"{name}.patch"
        patch.parent.mkdir(exist_ok=True)
        patch.write_text(f"--- a/a.txt\n+++ b/a.txt\n@@ -1,3 +1,3 @@\n{old}\n-two\n+{new}\n three\n")
        return Patch.fromSpec({"name": name, "path": ".", "patch": patch.name}, patch.parent, "https://gerrit", "test")

    def test_sync_clones_then_skips_unchanged(self) -> None:
        git = Git(self.context())
        self.assertTrue(git.sync(self.repo, self.dest, "."))
        self.assertFalse(git.sync(self.repo, self.dest, "."))
        new = self.push(self.work, {"a.txt": "one\ntwo\nthree\nfour\n"}, "four")
        self.assertTrue(git.sync(self.repo, self.dest, "."))
        self.assertEqual(git.revParse(self.dest, "HEAD"), new)

    def test_pinned_commit_is_checked_out(self) -> None:
        first = self.git(self.work, "rev-parse", "HEAD")
        self.push(self.work, {"a.txt": "changed\n"}, "change")
        git = Git(self.context())
        git.sync(self.repo.pinnedTo(first), self.dest, ".")
        self.assertEqual(git.revParse(self.dest, "HEAD"), first)
        git.sync(self.repo, self.dest, ".")
        self.assertNotEqual(git.revParse(self.dest, "HEAD"), first)

    def test_checkout_owned_by_another_user_is_trusted(self) -> None:
        git = Git(self.context())
        git.sync(self.repo, self.dest, ".")
        seen = []
        real = git.ctx.run

        def spy(argv, *a, **kw):
            seen.append(argv)
            return real(argv, *a, **kw)

        git.ctx.run = spy
        git.git(self.dest, "status", readOnly=True)
        self.assertIn(f"safe.directory={self.dest}", seen[-1])

    def test_local_edits_block_sync(self) -> None:
        git = Git(self.context())
        git.sync(self.repo, self.dest, ".")
        (self.dest / "a.txt").write_text("edited\n")
        with self.assertRaisesRegex(SlopError, "uncommitted changes"):
            git.sync(self.repo, self.dest, ".")

    def test_patches_apply_once_and_reapply_after_sync(self) -> None:
        git = Git(self.context())
        git.sync(self.repo, self.dest, ".")
        patch = self.filePatch("fix", " one", "TWO")
        self.assertTrue(git.applyPatches(self.dest, [patch]))
        self.assertIn("TWO", (self.dest / "a.txt").read_text())
        self.assertFalse(git.applyPatches(self.dest, [patch]))
        self.push(self.work, {"b.txt": "new\n"}, "unrelated")
        git.sync(self.repo, self.dest, ".")
        self.assertNotIn("TWO", (self.dest / "a.txt").read_text())
        self.assertTrue(git.applyPatches(self.dest, [patch]))
        self.assertIn("TWO", (self.dest / "a.txt").read_text())
        self.assertTrue((self.dest / "b.txt").exists())

    def test_removed_patch_is_dropped(self) -> None:
        git = Git(self.context())
        git.sync(self.repo, self.dest, ".")
        git.applyPatches(self.dest, [self.filePatch("fix", " one", "TWO")])
        self.assertTrue(git.applyPatches(self.dest, []))
        self.assertEqual((self.dest / "a.txt").read_text(), "one\ntwo\nthree\n")

    def test_conflict_fails_without_a_terminal(self) -> None:
        git = Git(self.context())
        git.sync(self.repo, self.dest, ".")
        self.push(self.work, {"a.txt": "one\nzwei\nthree\n"}, "upstream edit")
        git.sync(self.repo, self.dest, ".")
        with self.assertRaisesRegex(SlopError, "conflicts with"):
            git.applyPatches(self.dest, [self.filePatch("fix", " one", "TWO")])
        self.assertEqual((self.dest / "a.txt").read_text(), "one\nzwei\nthree\n")

    def test_conflict_resolved_interactively_is_saved(self) -> None:
        ctx = self.context(interactive=True)
        git = Git(ctx)
        git.sync(self.repo, self.dest, ".")
        commit = self.push(self.work, {"a.txt": "one\nTWO\nthree\n"}, "feature")
        self.git(self.work, "reset", "-q", "--hard", "HEAD~1")
        self.git(self.work, "push", "-q", "-f", "origin", "HEAD")
        self.push(self.work, {"a.txt": "one\nzwei\nthree\n"}, "upstream edit")
        self.git(self.work, "push", "-q", "origin", f"{commit}:refs/heads/feature")
        git.sync(self.repo, self.dest, ".")

        answers = iter(["s"])
        ctx.console.ask = lambda question, options, default=None: next(answers)
        ctx.console.confirm = lambda question, default=False: True

        def resolve(cmd, cwd=None, env=None):
            (cwd / "a.txt").write_text("one\nTWO and zwei\nthree\n")
            self.git(cwd, "add", "a.txt")
            return 0

        ctx.attached = resolve
        saved = {}
        patch = Patch.fromSpec(
            {"name": "feature", "path": ".", "commit": commit, "url": self.url("thing")},
            self.tmp,
            "https://gerrit",
            "t",
        )
        git.applyPatches(self.dest, [patch], lambda p, diff: saved.update({p.name: diff}))
        self.assertEqual((self.dest / "a.txt").read_text(), "one\nTWO and zwei\nthree\n")
        self.assertIn("+TWO and zwei", saved["feature"])
        self.assertFalse(git.pickInProgress(self.dest))

    def test_skip_leaves_checkout_clean(self) -> None:
        ctx = self.context(interactive=True)
        git = Git(ctx)
        git.sync(self.repo, self.dest, ".")
        self.push(self.work, {"a.txt": "one\nzwei\nthree\n"}, "upstream edit")
        git.sync(self.repo, self.dest, ".")
        ctx.console.ask = lambda question, options, default=None: "k"
        git.applyPatches(self.dest, [self.filePatch("fix", " one", "TWO")])
        self.assertEqual((self.dest / "a.txt").read_text(), "one\nzwei\nthree\n")
        self.assertFalse(git.dirty(self.dest))
