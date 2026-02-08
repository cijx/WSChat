import unittest
from uuid import UUID

from app.rooms import (
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

    def test_guest_join_makes_room_unavailable(self) -> None:
        room = self._create_room()
        self.service.join_room(room_id=room.room_id, user_id=self.guest_id, user_name=self.guest_name)

        self.assertEqual([], self.service.list_free_rooms())

    def test_room_allows_only_one_guest(self) -> None:
        room = self._create_room()
        self.service.join_room(room_id=room.room_id, user_id=self.guest_id, user_name=self.guest_name)

        with self.assertRaises(RoomJoinError):
            self.service.join_room(room_id=room.room_id, user_id="guest-2", user_name="Charlie")

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


if __name__ == "__main__":
    unittest.main()
