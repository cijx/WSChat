import os
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import (
    _author_close_deadlines,
    _connections_by_room,
    _lobby_connections,
    _participant_disconnect_deadlines,
    app,
    room_service,
)


class GeoIPRestrictionTests(unittest.TestCase):
    @staticmethod
    def _clear_author_close_deadlines() -> None:
        _author_close_deadlines.clear()

    @staticmethod
    def _clear_participant_disconnect_deadlines() -> None:
        _participant_disconnect_deadlines.clear()

    def setUp(self) -> None:
        room_service._rooms.clear()
        _connections_by_room.clear()
        _lobby_connections.clear()
        self._clear_author_close_deadlines()
        self._clear_participant_disconnect_deadlines()
        self.client = TestClient(app)
        self.client.__enter__()

    def tearDown(self) -> None:
        room_service._rooms.clear()
        _connections_by_room.clear()
        _lobby_connections.clear()
        self._clear_author_close_deadlines()
        self._clear_participant_disconnect_deadlines()
        self.client.__exit__(None, None, None)

    @contextmanager
    def _geoip_env(self, blocklist: str, headers: str = "X-Country-Code"):
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as temp_file:
            temp_file.write(blocklist)
            temp_path = Path(temp_file.name)

        try:
            with patch.dict(
                os.environ,
                {
                    "GEOIP_BLOCKLIST_FILE": str(temp_path),
                    "GEOIP_COUNTRY_HEADERS": headers,
                },
                clear=False,
            ):
                yield
        finally:
            temp_path.unlink(missing_ok=True)

    def test_blocked_country_cannot_create_room(self) -> None:
        with self._geoip_env("# blocked countries\nus\n"):
            response = self.client.post(
                "/rooms",
                json={"user_id": "author-1", "user_name": "Alice", "topic": "Geo blocked room"},
                headers={"X-Country-Code": "US"},
            )

        self.assertEqual(451, response.status_code)
        self.assertIn("недоступны", response.json()["detail"].lower())

    def test_allowed_country_can_create_room(self) -> None:
        with self._geoip_env("US\n"):
            response = self.client.post(
                "/rooms",
                json={"user_id": "author-1", "user_name": "Alice", "topic": "Geo allowed room"},
                headers={"X-Country-Code": "DE"},
            )

        self.assertEqual(201, response.status_code)
        self.assertEqual("Geo allowed room", response.json()["topic"])

    def test_blocked_country_can_open_lobby_websocket(self) -> None:
        with self._geoip_env("US\n"):
            with self.client.websocket_connect("/ws/lobby", headers={"X-Country-Code": "US"}) as lobby_ws:
                snapshot = lobby_ws.receive_json()

        self.assertEqual("rooms_catalog_snapshot", snapshot["type"])

    def test_blocked_country_cannot_join_room(self) -> None:
        with self._geoip_env("US\n"):
            create_response = self.client.post(
                "/rooms",
                json={"user_id": "author-1", "user_name": "Alice", "topic": "Geo socket room"},
                headers={"X-Country-Code": "DE"},
            )
            room_id = create_response.json()["room_id"]

            join_response = self.client.post(
                f"/rooms/{room_id}/join",
                json={"user_id": "guest-1", "user_name": "Bob"},
                headers={"X-Country-Code": "US"},
            )

        self.assertEqual(451, join_response.status_code)
        self.assertIn("недоступны", join_response.json()["detail"].lower())

    def test_health_endpoint_is_not_blocked(self) -> None:
        with self._geoip_env("US\n"):
            response = self.client.get("/health", headers={"X-Country-Code": "US"})

        self.assertEqual(200, response.status_code)
        self.assertEqual({"status": "ok"}, response.json())


if __name__ == "__main__":
    unittest.main()
