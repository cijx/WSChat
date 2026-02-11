import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]


class DockerConfigurationTests(unittest.TestCase):
    def test_required_docker_files_exist(self) -> None:
        expected_files = [
            PROJECT_ROOT / "docker-compose.yml",
            PROJECT_ROOT / "backend" / "Dockerfile",
            PROJECT_ROOT / "backend" / "app" / "blocked_countries.txt",
            PROJECT_ROOT / "frontend" / "Dockerfile",
            PROJECT_ROOT / "frontend" / "nginx.conf",
            PROJECT_ROOT / ".dockerignore",
        ]

        for path in expected_files:
            with self.subTest(path=str(path)):
                self.assertTrue(path.exists(), f"Missing docker file: {path}")

    def test_compose_exposes_expected_ports(self) -> None:
        compose_text = (PROJECT_ROOT / "docker-compose.yml").read_text(encoding="utf-8")

        self.assertIn("backend:", compose_text)
        self.assertIn("frontend:", compose_text)
        self.assertIn("ROOM_CLOSE_TIMEOUT_SECONDS", compose_text)
        self.assertIn("ROOM_PARTICIPANT_RECONNECT_GRACE_SECONDS", compose_text)
        self.assertIn("GEOIP_COUNTRY_HEADERS", compose_text)
        self.assertIn("GEOIP_BLOCKLIST_FILE", compose_text)
        self.assertIn("VITE_WS_PROTOCOL_MODE", compose_text)
        self.assertIn("${ROOM_PARTICIPANT_RECONNECT_GRACE_SECONDS:-300}", compose_text)
        self.assertIn("${GEOIP_BLOCKLIST_FILE:-/app/app/blocked_countries.txt}", compose_text)
        self.assertIn('"8000:8000"', compose_text)
        self.assertIn('"5173:80"', compose_text)

    def test_frontend_dockerfile_builds_static_bundle(self) -> None:
        dockerfile_text = (PROJECT_ROOT / "frontend" / "Dockerfile").read_text(encoding="utf-8")

        self.assertIn("FROM node:22-alpine AS build", dockerfile_text)
        self.assertIn("npm ci", dockerfile_text)
        self.assertIn("npm run build", dockerfile_text)
        self.assertIn("FROM nginx:1.27-alpine", dockerfile_text)
        self.assertIn("ARG VITE_WS_PROTOCOL_MODE=auto", dockerfile_text)

    def test_nginx_sets_security_headers(self) -> None:
        nginx_text = (PROJECT_ROOT / "frontend" / "nginx.conf").read_text(encoding="utf-8")

        self.assertIn("Content-Security-Policy", nginx_text)
        self.assertIn("X-Frame-Options", nginx_text)
        self.assertIn("X-Content-Type-Options", nginx_text)
        self.assertIn("Referrer-Policy", nginx_text)


if __name__ == "__main__":
    unittest.main()
