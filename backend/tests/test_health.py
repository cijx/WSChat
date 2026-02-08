import unittest

try:
    from fastapi.testclient import TestClient

    from app.main import app
except ModuleNotFoundError:
    TestClient = None
    app = None


@unittest.skipIf(TestClient is None, "fastapi is not installed in current environment")
class HealthcheckTests(unittest.TestCase):
    def test_healthcheck_returns_ok(self) -> None:
        client = TestClient(app)
        response = client.get("/health")

        self.assertEqual(200, response.status_code)
        self.assertEqual({"status": "ok"}, response.json())


if __name__ == "__main__":
    unittest.main()
