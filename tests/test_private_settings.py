from Support import Support


class PrivateSettingsTest(Support):
    def setUp(self) -> None:
        super().setUp()
        self.farm()

    def live(self, rel: str) -> str:
        return (self.tmp / "live" / rel).read_text()

    def test_deploy_copies_private_settings_into_config(self) -> None:
        self.slop("deploy", "-y", "-m", "first")
        self.assertIn("$wgSecretKey = 'test'", self.live("config/PrivateSettings.php"))
        self.assertIn("$wgSecretKey = 'test'", (self.tmp / "srv" / "build" / "config" / "PrivateSettings.php").read_text())

    def test_changed_source_is_copied_again(self) -> None:
        self.slop("deploy", "-y", "-m", "first")
        self.privateSettings.write_text("<?php\n$wgSecretKey = 'rotated';\n")
        self.slop("deploy", "config", "-y", "-m", "rotate key")
        self.assertIn("rotated", self.live("config/PrivateSettings.php"))
