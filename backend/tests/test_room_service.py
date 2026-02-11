import unittest
from uuid import UUID

from app.rooms import (
    MAX_MESSAGE_LENGTH,
    MAX_TOPIC_LENGTH,
    MAX_USER_ID_LENGTH,
    MAX_USER_NAME_LENGTH,
    RoomJoinError,
    RoomNotFoundError,
    RoomPermissionError,
    RoomService,
    RoomValidationError,
)


class RoomServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = RoomService()
        self.author_id = "author-1"
        self.author_name = "Alice"
        self.guest_id = "guest-1"
        self.guest_name = "Bob"

    def _create_room(self):
        return self.service.create_room(
            topic="Weekend plans",
            author_id=self.author_id,
            author_name=self.author_name,
        )

    def test_create_room_requires_topic(self) -> None:
        with self.assertRaises(RoomValidationError):
            self.service.create_room(topic="   ", author_id=self.author_id, author_name=self.author_name)

    def test_room_is_free_after_creation(self) -> None:
        room = self._create_room()
        free_rooms = self.service.list_free_rooms()

        self.assertEqual(1, len(free_rooms))
        self.assertEqual(room.room_id, free_rooms[0].room_id)
        self.assertTrue(bool(room.author_session_token))

    def test_guest_join_makes_room_unavailable(self) -> None:
        room = self._create_room()
        self.service.join_room(room_id=room.room_id, user_id=self.guest_id, user_name=self.guest_name)

        self.assertEqual([], self.service.list_free_rooms())

    def test_room_allows_only_one_guest(self) -> None:
        room = self._create_room()
        self.service.join_room(room_id=room.room_id, user_id=self.guest_id, user_name=self.guest_name)

        with self.assertRaises(RoomJoinError):
            self.service.join_room(room_id=room.room_id, user_id="guest-2", user_name="Charlie")

    def test_guest_rejoin_requires_valid_session_token(self) -> None:
        room = self._create_room()
        self.service.join_room(room_id=room.room_id, user_id=self.guest_id, user_name=self.guest_name)
        guest_token = self.service.issue_session_token(room_id=room.room_id, user_id=self.guest_id)

        with self.assertRaises(RoomPermissionError):
            self.service.join_room(room_id=room.room_id, user_id=self.guest_id, user_name="Bobby")

        with self.assertRaises(RoomPermissionError):
            self.service.join_room(
                room_id=room.room_id,
                user_id=self.guest_id,
                user_name="Bobby",
                session_token="wrong-token",
            )

        updated = self.service.join_room(
            room_id=room.room_id,
            user_id=self.guest_id,
            user_name="Bobby",
            session_token=guest_token,
        )
        self.assertIsNotNone(updated.guest)
        self.assertEqual("Bobby", updated.guest.user_name)
        self.assertEqual(guest_token, self.service.issue_session_token(room_id=room.room_id, user_id=self.guest_id))

    def test_author_cannot_join_own_room(self) -> None:
        room = self._create_room()

        with self.assertRaises(RoomJoinError):
            self.service.join_room(room_id=room.room_id, user_id=self.author_id, user_name=self.author_name)

    def test_guest_leave_frees_slot(self) -> None:
        room = self._create_room()
        self.service.join_room(room_id=room.room_id, user_id=self.guest_id, user_name=self.guest_name)

        leave_result = self.service.leave_room(room_id=room.room_id, user_id=self.guest_id)
        room_after_leave = self.service.get_room(room_id=room.room_id)

        self.assertEqual("slot_freed", leave_result.status)
        self.assertTrue(room_after_leave.is_free)

    def test_author_leave_closes_room(self) -> None:
        room = self._create_room()
        leave_result = self.service.leave_room(room_id=room.room_id, user_id=self.author_id)

        self.assertEqual("room_closed", leave_result.status)
        with self.assertRaises(RoomNotFoundError):
            self.service.get_room(room_id=room.room_id)

    def test_only_participant_can_send_message(self) -> None:
        room = self._create_room()

        with self.assertRaises(RoomPermissionError):
            self.service.post_message(room_id=room.room_id, user_id="intruder", text="Hello")

    def test_participant_message_is_saved(self) -> None:
        room = self._create_room()
        self.service.join_room(room_id=room.room_id, user_id=self.guest_id, user_name=self.guest_name)

        message = self.service.post_message(room_id=room.room_id, user_id=self.guest_id, text="Hi Alice")
        room_after_message = self.service.get_room(room_id=room.room_id)

        self.assertEqual("Hi Alice", message.text)
        self.assertEqual(1, len(room_after_message.messages))
        self.assertEqual("guest-1", room_after_message.messages[0].sender_id)

    def test_create_room_assigns_uuid_room_id(self) -> None:
        room = self._create_room()

        parsed = UUID(room.room_id)
        self.assertEqual(room.room_id, str(parsed))

    def test_resolve_session_returns_author_and_guest(self) -> None:
        room = self._create_room()
        author_session = self.service.resolve_session(room_id=room.room_id, session_token=room.author_session_token)
        self.assertEqual("author", author_session.role)
        self.assertEqual(self.author_id, author_session.user_id)

        self.service.join_room(room_id=room.room_id, user_id=self.guest_id, user_name=self.guest_name)
        guest_token = self.service.issue_session_token(room_id=room.room_id, user_id=self.guest_id)
        guest_session = self.service.resolve_session(room_id=room.room_id, session_token=guest_token)
        self.assertEqual("guest", guest_session.role)
        self.assertEqual(self.guest_id, guest_session.user_id)

    def test_resolve_session_rejects_invalid_token(self) -> None:
        room = self._create_room()

        with self.assertRaises(RoomPermissionError):
            self.service.resolve_session(room_id=room.room_id, session_token="invalid-token")

    def test_create_room_rejects_too_long_topic(self) -> None:
        with self.assertRaises(RoomValidationError):
            self.service.create_room(
                topic="x" * (MAX_TOPIC_LENGTH + 1),
                author_id=self.author_id,
                author_name=self.author_name,
            )

    def test_create_room_rejects_too_long_user_name(self) -> None:
        with self.assertRaises(RoomValidationError):
            self.service.create_room(
                topic="Weekend plans",
                author_id=self.author_id,
                author_name="a" * (MAX_USER_NAME_LENGTH + 1),
            )

    def test_create_room_rejects_too_long_user_id(self) -> None:
        with self.assertRaises(RoomValidationError):
            self.service.create_room(
                topic="Weekend plans",
                author_id="u" * (MAX_USER_ID_LENGTH + 1),
                author_name=self.author_name,
            )

    def test_post_message_rejects_too_long_text(self) -> None:
        room = self._create_room()
        self.service.join_room(room_id=room.room_id, user_id=self.guest_id, user_name=self.guest_name)

        with self.assertRaises(RoomValidationError):
            self.service.post_message(
                room_id=room.room_id,
                user_id=self.guest_id,
                text="m" * (MAX_MESSAGE_LENGTH + 1),
            )

    def test_create_room_respects_active_room_limit(self) -> None:
        limited_service = RoomService(max_active_rooms=1)
        limited_service.create_room(topic="One", author_id="author-1", author_name="Alice")

        with self.assertRaises(RoomValidationError):
            limited_service.create_room(topic="Two", author_id="author-2", author_name="Bob")

    def test_post_message_keeps_bounded_history(self) -> None:
        limited_service = RoomService(max_messages_per_room=2)
        room = limited_service.create_room(topic="Bounded", author_id="author-1", author_name="Alice")

        limited_service.post_message(room_id=room.room_id, user_id="author-1", text="first")
        limited_service.post_message(room_id=room.room_id, user_id="author-1", text="second")
        limited_service.post_message(room_id=room.room_id, user_id="author-1", text="third")
        room_after_messages = limited_service.get_room(room_id=room.room_id)

        self.assertEqual(2, len(room_after_messages.messages))
        self.assertEqual(["second", "third"], [message.text for message in room_after_messages.messages])


if __name__ == "__main__":
    unittest.main()
