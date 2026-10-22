import functools
import threading
from http.server import HTTPServer, SimpleHTTPRequestHandler

from Slop.Config.Inventory import Inventory
from Slop.Deploy.Health import Health
from Slop.Remote.Ssh import Ssh
from Slop.SlopError import SlopError
from Support import Support


class Quiet(SimpleHTTPRequestHandler):
    def log_message(self, format, *args) -> None:
        pass


class HealthTest(Support):
    def setUp(self) -> None:
        super().setUp()
        site = self.tmp / "site"
        self.write(
            site,
            {
                "wiki/Main_Page": "<html>Welcome to WikiOasis</html>",
                "w/api.php": '{"query": {"general": {"sitename": "Test"}}}',
                "broken": "<b>Fatal error</b>: Uncaught Exception",
            },
        )
        self.server = HTTPServer(("127.0.0.1", 0), functools.partial(Quiet, directory=str(site)))
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.ssh = Ssh(self.context(), Inventory.fromSpec({"ssh": {"sudo": False}, "local": ["h1"]}, "test"))

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        super().tearDown()

    def health(self, tests: list[dict]) -> Health:
        return Health(self.ssh, {"url": self.url, "host": "test.wikioasis.org", "retries": 1, "tests": tests})

    def test_passing_suite(self) -> None:
        health = self.health(
            [
                {"name": "main", "path": "/wiki/Main_Page", "expect": "wikioasis"},
                {"name": "api", "path": "/w/api.php", "expect": '"sitename"'},
            ]
        )
        self.assertIn("2/2 smoke tests passed", health.check("h1"))

    def test_fatal_error_fails_even_with_200(self) -> None:
        health = self.health([{"name": "broken", "path": "/broken"}])
        with self.assertRaisesRegex(SlopError, "contains 'Fatal error'"):
            health.check("h1")

    def test_status_and_missing_text(self) -> None:
        health = self.health(
            [
                {"name": "missing", "path": "/nope"},
                {"name": "text", "path": "/wiki/Main_Page", "expect": "not there"},
            ]
        )
        results = health.run("h1")
        self.assertEqual([r.ok for r in results], [False, False])
        self.assertIn("HTTP 404", results[0].detail)
        self.assertIn("does not contain", results[1].detail)

    def test_canary_only_tests(self) -> None:
        health = self.health(
            [
                {"name": "main", "path": "/wiki/Main_Page"},
                {"name": "deep", "path": "/nope", "roles": ["canary"]},
            ]
        )
        self.assertIn("1/1", health.check("h1", "app"))
        with self.assertRaises(SlopError):
            health.check("h1", "canary")
