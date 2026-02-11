from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from secrets import compare_digest, token_urlsafe
from threading import RLock
from typing import Literal
from uuid import uuid4


class RoomError(Exception):
    """Base exception for room operations."""


class RoomValidationError(RoomError):
    """Raised when incoming data is invalid."""


class RoomNotFoundError(RoomError):
    """Raised when room does not exist."""


class RoomJoinError(RoomError):
    """Raised when a room cannot be joined."""


class RoomPermissionError(RoomError):
    """Raised when user is not allowed to perform an action."""


MAX_USER_ID_LENGTH = 128
MAX_USER_NAME_LENGTH = 80
MAX_TOPIC_LENGTH = 200
MAX_MESSAGE_LENGTH = 4000
MAX_SESSION_TOKEN_LENGTH = 256


@dataclass(frozen=True)
class Participant:
    user_id: str
    user_name: str


@dataclass(frozen=True)
class ChatMessage:
    room_id: str
    sender_id: str
    sender_name: str
    text: str
    created_at: str


@dataclass
class Room:
    room_id: str
    topic: str
    author: Participant
    author_session_token: str
    guest: Participant | None = None
    guest_session_token: str | None = None
    created_at: str = field(default_factory=lambda: _utc_now_iso())
    messages: list[ChatMessage] = field(default_factory=list)

    @property
    def is_free(self) -> bool:
        return self.guest is None

    def is_participant(self, user_id: str) -> bool:
        if self.author.user_id == user_id:
            return True
        return self.guest is not None and self.guest.user_id == user_id

    def participant_by_user_id(self, user_id: str) -> Participant | None:
        if self.author.user_id == user_id:
            return self.author
        if self.guest is not None and self.guest.user_id == user_id:
            return self.guest
        return None


@dataclass(frozen=True)
class LeaveResult:
    status: Literal["room_closed", "slot_freed", "noop"]


@dataclass(frozen=True)
class SessionResolution:
    user_id: str
    role: Literal["author", "guest"]


def _utc_now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


class RoomService:
    """In-memory room registry for a two-person chat."""

    def __init__(self) -> None:
        self._rooms: dict[str, Room] = {}
        self._lock = RLock()

    def create_room(self, topic: str, author_id: str, author_name: str) -> Room:
        clean_topic = topic.strip()
        if not clean_topic:
            raise RoomValidationError("Room topic is required.")
        self._validate_max_length(clean_topic, MAX_TOPIC_LENGTH, "Room topic")

        author = self._build_participant(user_id=author_id, user_name=author_name)
        with self._lock:
            room_id = str(uuid4())
            room = Room(
                room_id=room_id,
                topic=clean_topic,
                author=author,
                author_session_token=self._build_session_token(),
            )
            self._rooms[room_id] = room
        return room

    def list_free_rooms(self) -> list[Room]:
        with self._lock:
            rooms = [room for room in self._rooms.values() if room.is_free]
        return sorted(rooms, key=lambda room: room.created_at)

    def get_room(self, room_id: str) -> Room:
        with self._lock:
            room = self._rooms.get(room_id)
            if room is None:
                raise RoomNotFoundError(f"Room '{room_id}' does not exist.")
            return room

    def join_room(self, room_id: str, user_id: str, user_name: str) -> Room:
        participant = self._build_participant(user_id=user_id, user_name=user_name)

        with self._lock:
            room = self._rooms.get(room_id)
            if room is None:
                raise RoomNotFoundError(f"Room '{room_id}' does not exist.")

            if room.author.user_id == participant.user_id:
                raise RoomJoinError("Author cannot join own room.")

            if room.guest is None:
                room.guest = participant
                room.guest_session_token = self._build_session_token()
                return room

            if room.guest.user_id == participant.user_id:
                room.guest = participant
                if room.guest_session_token is None:
                    room.guest_session_token = self._build_session_token()
                return room

            raise RoomJoinError("Room already has a guest.")

    def leave_room(self, room_id: str, user_id: str) -> LeaveResult:
        clean_user_id = user_id.strip()
        if not clean_user_id:
            raise RoomValidationError("User id is required.")

        with self._lock:
            room = self._rooms.get(room_id)
            if room is None:
                return LeaveResult(status="noop")

            if room.author.user_id == clean_user_id:
                del self._rooms[room_id]
                return LeaveResult(status="room_closed")

            if room.guest is not None and room.guest.user_id == clean_user_id:
                room.guest = None
                room.guest_session_token = None
                return LeaveResult(status="slot_freed")

            return LeaveResult(status="noop")

    def post_message(self, room_id: str, user_id: str, text: str) -> ChatMessage:
        clean_text = text.strip()
        if not clean_text:
            raise RoomValidationError("Message text is required.")
        self._validate_max_length(clean_text, MAX_MESSAGE_LENGTH, "Message text")

        with self._lock:
            room = self._rooms.get(room_id)
            if room is None:
                raise RoomNotFoundError(f"Room '{room_id}' does not exist.")

            sender = room.participant_by_user_id(user_id=user_id)
            if sender is None:
                raise RoomPermissionError("User is not a participant of this room.")

            message = ChatMessage(
                room_id=room_id,
                sender_id=sender.user_id,
                sender_name=sender.user_name,
                text=clean_text,
                created_at=_utc_now_iso(),
            )
            room.messages.append(message)
            return message

    def issue_session_token(self, room_id: str, user_id: str) -> str:
        clean_user_id = user_id.strip()
        if not clean_user_id:
            raise RoomValidationError("User id is required.")
        self._validate_max_length(clean_user_id, MAX_USER_ID_LENGTH, "User id")

        with self._lock:
            room = self._rooms.get(room_id)
            if room is None:
                raise RoomNotFoundError(f"Room '{room_id}' does not exist.")

            if room.author.user_id == clean_user_id:
                return room.author_session_token

            if room.guest is not None and room.guest.user_id == clean_user_id:
                if room.guest_session_token is None:
                    room.guest_session_token = self._build_session_token()
                return room.guest_session_token

            raise RoomPermissionError("User is not a participant of this room.")

    def resolve_session(self, room_id: str, session_token: str) -> SessionResolution:
        clean_session_token = session_token.strip()
        if not clean_session_token:
            raise RoomValidationError("Session token is required.")
        self._validate_max_length(clean_session_token, MAX_SESSION_TOKEN_LENGTH, "Session token")

        with self._lock:
            room = self._rooms.get(room_id)
            if room is None:
                raise RoomNotFoundError(f"Room '{room_id}' does not exist.")

            if compare_digest(room.author_session_token, clean_session_token):
                return SessionResolution(user_id=room.author.user_id, role="author")

            if room.guest is not None and room.guest_session_token is not None:
                if compare_digest(room.guest_session_token, clean_session_token):
                    return SessionResolution(user_id=room.guest.user_id, role="guest")

            raise RoomPermissionError("Invalid session token for this room.")

    def is_participant(self, room_id: str, user_id: str) -> bool:
        with self._lock:
            room = self._rooms.get(room_id)
            if room is None:
                return False
            return room.is_participant(user_id=user_id.strip())

    @staticmethod
    def _build_participant(user_id: str, user_name: str) -> Participant:
        clean_user_id = user_id.strip()
        clean_user_name = user_name.strip()

        if not clean_user_id:
            raise RoomValidationError("User id is required.")
        if not clean_user_name:
            raise RoomValidationError("User name is required.")
        RoomService._validate_max_length(clean_user_id, MAX_USER_ID_LENGTH, "User id")
        RoomService._validate_max_length(clean_user_name, MAX_USER_NAME_LENGTH, "User name")

        return Participant(user_id=clean_user_id, user_name=clean_user_name)

    @staticmethod
    def _build_session_token() -> str:
        return token_urlsafe(32)

    @staticmethod
    def _validate_max_length(value: str, max_length: int, field_name: str) -> None:
        if len(value) > max_length:
            raise RoomValidationError(f"{field_name} must be at most {max_length} characters.")
