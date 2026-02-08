import unittest
from time import sleep
from unittest.mock import patch

from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.main import (
    _author_close_deadlines,
    _connections_by_room,
    _lobby_connections,
    _participant_disconnect_deadlines,
    app,
    room_service,
)


class RoomApiFlowTests(unittest.TestCase):
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

    def test_guest_can_join_room_and_open_websocket(self) -> None:
        create_response = self.client.post(
            "/rooms",
            json={"user_id": "author-1", "user_name": "Alice", "topic": "Integration topic"},
        )
        self.assertEqual(201, create_response.status_code)
        room_id = create_response.json()["room_id"]

        join_response = self.client.post(
            f"/rooms/{room_id}/join",
            json={"user_id": "guest-1", "user_name": "Bob"},
        )
        self.assertEqual(200, join_response.status_code)
        self.assertEqual("guest-1", join_response.json()["guest"]["user_id"])

        with self.client.websocket_connect(f"/ws/rooms/{room_id}?user_id=guest-1") as websocket:
            payload = websocket.receive_json()

        self.assertEqual("room_state", payload["type"])
        self.assertEqual(room_id, payload["room"]["room_id"])
        self.assertEqual("guest-1", payload["room"]["guest"]["user_id"])

    def test_third_user_cannot_join_busy_room(self) -> None:
        create_response = self.client.post(
            "/rooms",
            json={"user_id": "author-1", "user_name": "Alice", "topic": "Busy room"},
        )
        room_id = create_response.json()["room_id"]

        first_join = self.client.post(
            f"/rooms/{room_id}/join",
            json={"user_id": "guest-1", "user_name": "Bob"},
        )
        self.assertEqual(200, first_join.status_code)

        second_join = self.client.post(
            f"/rooms/{room_id}/join",
            json={"user_id": "guest-2", "user_name": "Charlie"},
        )
        self.assertEqual(409, second_join.status_code)
        self.assertIn("already has a guest", second_join.json()["detail"])

    def test_author_cannot_join_own_room(self) -> None:
        create_response = self.client.post(
            "/rooms",
            json={"user_id": "author-1", "user_name": "Alice", "topic": "Own room"},
        )
        room_id = create_response.json()["room_id"]

        join_response = self.client.post(
            f"/rooms/{room_id}/join",
            json={"user_id": "author-1", "user_name": "Alice"},
        )
        self.assertEqual(409, join_response.status_code)
        self.assertIn("cannot join own room", join_response.json()["detail"].lower())

    @staticmethod
    def _receive_until_type(websocket, expected_type: str, max_messages: int = 6):
        for _ in range(max_messages):
            payload = websocket.receive_json()
            if payload.get("type") == expected_type:
                return payload
        raise AssertionError(f"Did not receive event '{expected_type}' within {max_messages} messages")

    @staticmethod
    def _receive_participant_event(websocket, expected_event: str, max_messages: int = 12):
        for _ in range(max_messages):
            payload = websocket.receive_json()
            if payload.get("type") != "participant_event":
                continue
            if payload.get("event") == expected_event:
                return payload
        raise AssertionError(f"Did not receive participant_event '{expected_event}' within {max_messages} messages")

    @staticmethod
    def _receive_until_catalog_reason(websocket, expected_reason: str, max_messages: int = 10):
        for _ in range(max_messages):
            payload = websocket.receive_json()
            if payload.get("type") == "rooms_catalog_updated" and payload.get("reason") == expected_reason:
                return payload
        raise AssertionError(f"Did not receive catalog event '{expected_reason}' within {max_messages} messages")

    def test_author_receives_join_and_leave_participant_events(self) -> None:
        create_response = self.client.post(
            "/rooms",
            json={"user_id": "author-1", "user_name": "Alice", "topic": "Event room"},
        )
        room_id = create_response.json()["room_id"]

        with self.client.websocket_connect(f"/ws/rooms/{room_id}?user_id=author-1") as author_ws:
            first_payload = author_ws.receive_json()
            self.assertEqual("room_state", first_payload["type"])

            join_response = self.client.post(
                f"/rooms/{room_id}/join",
                json={"user_id": "guest-1", "user_name": "Bob"},
            )
            self.assertEqual(200, join_response.status_code)

            joined_event = self._receive_until_type(author_ws, "participant_event")
            self.assertEqual("joined", joined_event["event"])
            self.assertEqual("guest-1", joined_event["participant"]["user_id"])
            self.assertEqual("Bob", joined_event["participant"]["user_name"])

            leave_response = self.client.post(
                f"/rooms/{room_id}/leave",
                json={"user_id": "guest-1"},
            )
            self.assertEqual(200, leave_response.status_code)

            left_event = self._receive_until_type(author_ws, "participant_event")
            self.assertEqual("left", left_event["event"])
            self.assertEqual("guest-1", left_event["participant"]["user_id"])
            self.assertEqual("Bob", left_event["participant"]["user_name"])

    def test_websocket_rejects_non_participant(self) -> None:
        create_response = self.client.post(
            "/rooms",
            json={"user_id": "author-1", "user_name": "Alice", "topic": "Private room"},
        )
        room_id = create_response.json()["room_id"]

        with self.assertRaises(WebSocketDisconnect):
            with self.client.websocket_connect(f"/ws/rooms/{room_id}?user_id=intruder"):
                pass

    def test_lobby_websocket_receives_room_created_event(self) -> None:
        with self.client.websocket_connect("/ws/lobby") as lobby_ws:
            snapshot = lobby_ws.receive_json()
            self.assertEqual("rooms_catalog_snapshot", snapshot["type"])
            self.assertEqual([], snapshot["rooms"])

            create_response = self.client.post(
                "/rooms",
                json={"user_id": "author-1", "user_name": "Alice", "topic": "Lobby room"},
            )
            self.assertEqual(201, create_response.status_code)

            created_event = self._receive_until_catalog_reason(lobby_ws, "room_created")
            self.assertEqual("room_created", created_event["reason"])
            self.assertTrue(created_event["room"]["is_free"])
            self.assertEqual("Lobby room", created_event["room"]["topic"])

    def test_lobby_websocket_receives_room_freed_event(self) -> None:
        with self.client.websocket_connect("/ws/lobby") as lobby_ws:
            snapshot = lobby_ws.receive_json()
            self.assertEqual("rooms_catalog_snapshot", snapshot["type"])

            create_response = self.client.post(
                "/rooms",
                json={"user_id": "author-1", "user_name": "Alice", "topic": "Free later room"},
            )
            self.assertEqual(201, create_response.status_code)
            room_id = create_response.json()["room_id"]
            self._receive_until_catalog_reason(lobby_ws, "room_created")

            join_response = self.client.post(
                f"/rooms/{room_id}/join",
                json={"user_id": "guest-1", "user_name": "Bob"},
            )
            self.assertEqual(200, join_response.status_code)
            self._receive_until_catalog_reason(lobby_ws, "room_became_busy")

            leave_response = self.client.post(
                f"/rooms/{room_id}/leave",
                json={"user_id": "guest-1"},
            )
            self.assertEqual(200, leave_response.status_code)

            freed_event = self._receive_until_catalog_reason(lobby_ws, "room_freed")
            self.assertEqual(room_id, freed_event["room_id"])
            self.assertIsNotNone(freed_event["room"])
            self.assertTrue(freed_event["room"]["is_free"])

    def test_room_is_not_closed_immediately_after_author_disconnect(self) -> None:
        with patch("app.main.ROOM_CLOSE_TIMEOUT_SECONDS", 1):
            create_response = self.client.post(
                "/rooms",
                json={"user_id": "author-1", "user_name": "Alice", "topic": "Timeout room"},
            )
            room_id = create_response.json()["room_id"]

            with self.client.websocket_connect(f"/ws/rooms/{room_id}?user_id=author-1") as author_ws:
                room_state = author_ws.receive_json()
                self.assertEqual("room_state", room_state["type"])

            sleep(0.2)
            not_closed_yet = self.client.get(f"/rooms/{room_id}")
            self.assertEqual(200, not_closed_yet.status_code)

            sleep(1.8)
            closed_after_timeout = self.client.get(f"/rooms/{room_id}")
            self.assertEqual(404, closed_after_timeout.status_code)

    def test_author_reconnect_cancels_scheduled_room_close(self) -> None:
        with patch("app.main.ROOM_CLOSE_TIMEOUT_SECONDS", 1):
            create_response = self.client.post(
                "/rooms",
                json={"user_id": "author-1", "user_name": "Alice", "topic": "Reconnect room"},
            )
            room_id = create_response.json()["room_id"]

            with self.client.websocket_connect(f"/ws/rooms/{room_id}?user_id=author-1") as first_author_ws:
                room_state = first_author_ws.receive_json()
                self.assertEqual("room_state", room_state["type"])

            sleep(0.2)
            with self.client.websocket_connect(f"/ws/rooms/{room_id}?user_id=author-1") as second_author_ws:
                room_state = second_author_ws.receive_json()
                self.assertEqual("room_state", room_state["type"])
                sleep(1.8)
                still_exists = self.client.get(f"/rooms/{room_id}")
                self.assertEqual(200, still_exists.status_code)

    def test_guest_disconnect_does_not_free_slot_immediately(self) -> None:
        with patch("app.main.ROOM_PARTICIPANT_RECONNECT_GRACE_SECONDS", 1):
            create_response = self.client.post(
                "/rooms",
                json={"user_id": "author-1", "user_name": "Alice", "topic": "Guest timeout room"},
            )
            room_id = create_response.json()["room_id"]
            join_response = self.client.post(
                f"/rooms/{room_id}/join",
                json={"user_id": "guest-1", "user_name": "Bob"},
            )
            self.assertEqual(200, join_response.status_code)

            with self.client.websocket_connect(f"/ws/rooms/{room_id}?user_id=guest-1") as guest_ws:
                room_state = guest_ws.receive_json()
                self.assertEqual("room_state", room_state["type"])

            sleep(0.2)
            still_occupied = self.client.get(f"/rooms/{room_id}")
            self.assertEqual(200, still_occupied.status_code)
            self.assertEqual("guest-1", still_occupied.json()["guest"]["user_id"])

            sleep(1.8)
            freed_after_grace = self.client.get(f"/rooms/{room_id}")
            self.assertEqual(200, freed_after_grace.status_code)
            self.assertIsNone(freed_after_grace.json()["guest"])

    def test_author_sees_guest_disconnected_event_on_socket_close(self) -> None:
        with patch("app.main.ROOM_PARTICIPANT_RECONNECT_GRACE_SECONDS", 2):
            create_response = self.client.post(
                "/rooms",
                json={"user_id": "author-1", "user_name": "Alice", "topic": "Disconnect event room"},
            )
            room_id = create_response.json()["room_id"]

            with self.client.websocket_connect(f"/ws/rooms/{room_id}?user_id=author-1") as author_ws:
                author_state = author_ws.receive_json()
                self.assertEqual("room_state", author_state["type"])

                join_response = self.client.post(
                    f"/rooms/{room_id}/join",
                    json={"user_id": "guest-1", "user_name": "Bob"},
                )
                self.assertEqual(200, join_response.status_code)

                self._receive_participant_event(author_ws, "joined")

                with self.client.websocket_connect(f"/ws/rooms/{room_id}?user_id=guest-1") as guest_ws:
                    guest_state = guest_ws.receive_json()
                    self.assertEqual("room_state", guest_state["type"])

                disconnected_event = self._receive_participant_event(author_ws, "disconnected")
                self.assertEqual("guest-1", disconnected_event["participant"]["user_id"])
                self.assertEqual("Bob", disconnected_event["participant"]["user_name"])

    def test_guest_reconnect_cancels_slot_release(self) -> None:
        with patch("app.main.ROOM_PARTICIPANT_RECONNECT_GRACE_SECONDS", 1):
            create_response = self.client.post(
                "/rooms",
                json={"user_id": "author-1", "user_name": "Alice", "topic": "Guest reconnect room"},
            )
            room_id = create_response.json()["room_id"]
            join_response = self.client.post(
                f"/rooms/{room_id}/join",
                json={"user_id": "guest-1", "user_name": "Bob"},
            )
            self.assertEqual(200, join_response.status_code)

            with self.client.websocket_connect(f"/ws/rooms/{room_id}?user_id=guest-1") as first_guest_ws:
                room_state = first_guest_ws.receive_json()
                self.assertEqual("room_state", room_state["type"])

            sleep(0.2)
            with self.client.websocket_connect(f"/ws/rooms/{room_id}?user_id=guest-1") as second_guest_ws:
                room_state = second_guest_ws.receive_json()
                self.assertEqual("room_state", room_state["type"])
                sleep(1.8)
                still_occupied = self.client.get(f"/rooms/{room_id}")
                self.assertEqual(200, still_occupied.status_code)
                self.assertEqual("guest-1", still_occupied.json()["guest"]["user_id"])


if __name__ == "__main__":
    unittest.main()
