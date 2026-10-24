from Support import Support


class OAuthKeyTest(Support):
    def setUp(self) -> None:
        super().setUp()
        self.farm()

    def live(self, rel: str) -> str:
        return (self.tmp / "live" / rel).read_text()

    def test_deploy_copies_oauth_key_into_config(self) -> None:
        self.slop("deploy", "-y", "-m", "first")
        self.assertIn("test", self.live("config/OAuth.key"))
        self.assertIn("test", (self.tmp / "srv" / "build" / "config" / "OAuth.key").read_text())

    def test_changed_source_is_copied_again(self) -> None:
        self.slop("deploy", "-y", "-m", "first")
        self.oauthKey.write_text("rotated\n")
        self.slop("deploy", "config", "--resync", "-y", "-m", "rotate key")
        self.assertIn("rotated", self.live("config/OAuth.key"))
