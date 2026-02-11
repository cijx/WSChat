from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone
from ipaddress import ip_address, ip_network
from pathlib import Path
from threading import RLock
from time import monotonic
from typing import Optional

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, ValidationError

from app.rooms import (
    ChatMessage,
    DEFAULT_MAX_ACTIVE_ROOMS,
    DEFAULT_MAX_MESSAGES_PER_ROOM,
    LeaveResult,
    MAX_MESSAGE_LENGTH,
    MAX_SESSION_TOKEN_LENGTH,
    MAX_TOPIC_LENGTH,
    MAX_USER_ID_LENGTH,
    MAX_USER_NAME_LENGTH,
    Participant,
    Room,
    RoomError,
    RoomJoinError,
    RoomNotFoundError,
    RoomPermissionError,
    RoomService,
    RoomValidationError,
)

app = FastAPI(title="Chat Backend", version="0.2.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

_connections_by_room: dict[str, dict[str, set[WebSocket]]] = {}
_connections_lock = asyncio.Lock()
_lobby_connections: set[WebSocket] = set()
_lobby_connections_lock = asyncio.Lock()
_author_close_deadlines: dict[str, tuple[str, float]] = {}
_author_close_deadlines_lock = asyncio.Lock()
_participant_disconnect_deadlines: dict[tuple[str, str], float] = {}
_participant_disconnect_deadlines_lock = asyncio.Lock()
_author_close_worker_task: Optional[asyncio.Task] = None


def _read_positive_int_env(var_name: str, default_value: int) -> int:
    raw_value = os.getenv(var_name, str(default_value))
    try:
        parsed = int(raw_value)
    except ValueError:
        return default_value
    return max(parsed, 1)


def _read_max_active_rooms() -> int:
    return _read_positive_int_env("ROOM_MAX_ACTIVE_ROOMS", DEFAULT_MAX_ACTIVE_ROOMS)


def _read_max_messages_per_room() -> int:
    return _read_positive_int_env("ROOM_MAX_MESSAGES_PER_ROOM", DEFAULT_MAX_MESSAGES_PER_ROOM)


def _read_room_close_timeout_seconds() -> int:
    raw_value = os.getenv("ROOM_CLOSE_TIMEOUT_SECONDS", "300")
    try:
        parsed = int(raw_value)
    except ValueError:
        return 300
    return max(parsed, 0)


ROOM_CLOSE_TIMEOUT_SECONDS = _read_room_close_timeout_seconds()


def _read_participant_reconnect_grace_seconds() -> int:
    raw_value = os.getenv("ROOM_PARTICIPANT_RECONNECT_GRACE_SECONDS", "300")
    try:
        parsed = int(raw_value)
    except ValueError:
        return 300
    return max(parsed, 0)


ROOM_PARTICIPANT_RECONNECT_GRACE_SECONDS = _read_participant_reconnect_grace_seconds()
ROOM_MAX_ACTIVE_ROOMS = _read_max_active_rooms()
ROOM_MAX_MESSAGES_PER_ROOM = _read_max_messages_per_room()

_DEFAULT_GEOIP_COUNTRY_HEADERS = "CF-IPCountry,X-Country-Code,X-Geo-Country"
_DEFAULT_GEOIP_TRUSTED_PROXIES = "127.0.0.1,::1,testclient"
_GEOIP_RESTRICTED_ACTIONS_DETAIL = "Для вашей страны создание комнат и подключение к комнатам недоступны."
_geoip_cache_lock = RLock()
_geoip_cache_path: Optional[Path] = None
_geoip_cache_mtime_ns: Optional[int] = None
_geoip_cache_codes: set[str] = set()
_ROOM_SESSION_TOKEN_HEADER = "x-room-session-token"

room_service = RoomService(
    max_active_rooms=ROOM_MAX_ACTIVE_ROOMS,
    max_messages_per_room=ROOM_MAX_MESSAGES_PER_ROOM,
)


def _read_geoip_country_headers() -> list[str]:
    raw_value = os.getenv("GEOIP_COUNTRY_HEADERS", _DEFAULT_GEOIP_COUNTRY_HEADERS)
    parsed = [header.strip() for header in raw_value.split(",") if header.strip()]
    if parsed:
        return parsed
    return _DEFAULT_GEOIP_COUNTRY_HEADERS.split(",")


def _read_geoip_blocklist_path() -> Path:
    raw_value = os.getenv("GEOIP_BLOCKLIST_FILE", "").strip()
    if raw_value:
        return Path(raw_value)
    return Path(__file__).with_name("blocked_countries.txt")


def _read_geoip_trusted_proxies() -> list[str]:
    raw_value = os.getenv("GEOIP_TRUSTED_PROXIES", _DEFAULT_GEOIP_TRUSTED_PROXIES)
    return [proxy.strip() for proxy in raw_value.split(",") if proxy.strip()]


def _is_request_from_trusted_geoip_proxy(request: Request) -> bool:
    client_host = request.client.host.strip() if request.client is not None and request.client.host else ""
    if not client_host:
        return False

    trusted_hosts: set[str] = set()
    trusted_ips: set[str] = set()
    trusted_networks = []

    for raw_value in _read_geoip_trusted_proxies():
        if "/" in raw_value:
            try:
                trusted_networks.append(ip_network(raw_value, strict=False))
                continue
            except ValueError:
                trusted_hosts.add(raw_value.lower())
                continue

        try:
            trusted_ips.add(str(ip_address(raw_value)))
            continue
        except ValueError:
            trusted_hosts.add(raw_value.lower())

    normalized_host = client_host.lower()
    if normalized_host in trusted_hosts:
        return True

    try:
        parsed_client_ip = ip_address(client_host)
    except ValueError:
        return False

    if str(parsed_client_ip) in trusted_ips:
        return True

    return any(parsed_client_ip in trusted_network for trusted_network in trusted_networks)


def _normalize_country_code(raw_value: str) -> str:
    normalized = raw_value.strip().upper()
    if not normalized:
        return ""
    if "," in normalized:
        normalized = normalized.split(",", 1)[0].strip()
    return normalized


def _parse_geoip_blocklist(text: str) -> set[str]:
    blocked: set[str] = set()
    for raw_line in text.splitlines():
        line = raw_line.split("#", 1)[0].strip().upper()
        if len(line) == 2 and line.isalpha():
            blocked.add(line)
    return blocked


def _load_geoip_blocked_countries() -> set[str]:
    global _geoip_cache_codes, _geoip_cache_mtime_ns, _geoip_cache_path

    blocklist_path = _read_geoip_blocklist_path()

    try:
        mtime_ns = blocklist_path.stat().st_mtime_ns
    except OSError:
        with _geoip_cache_lock:
            _geoip_cache_path = blocklist_path
            _geoip_cache_mtime_ns = None
            _geoip_cache_codes = set()
            return set()

    with _geoip_cache_lock:
        if _geoip_cache_path == blocklist_path and _geoip_cache_mtime_ns == mtime_ns:
            return set(_geoip_cache_codes)

    try:
        parsed_codes = _parse_geoip_blocklist(blocklist_path.read_text(encoding="utf-8"))
    except OSError:
        parsed_codes = set()

    with _geoip_cache_lock:
        _geoip_cache_path = blocklist_path
        _geoip_cache_mtime_ns = mtime_ns
        _geoip_cache_codes = set(parsed_codes)

    return parsed_codes


def _extract_country_code(headers) -> str:
    for header_name in _read_geoip_country_headers():
        raw_value = headers.get(header_name)
        if not raw_value:
            continue
        normalized = _normalize_country_code(raw_value)
        if normalized:
            return normalized
    return ""


def _extract_country_code_from_request(request: Request) -> str:
    if not _is_request_from_trusted_geoip_proxy(request):
        return ""
    return _extract_country_code(headers=request.headers)


def _is_geoip_blocked(request: Request) -> bool:
    country_code = _extract_country_code_from_request(request=request)
    if not country_code:
        return False
    return country_code in _load_geoip_blocked_countries()


class ParticipantPayload(BaseModel):
    user_id: str = Field(min_length=1, max_length=MAX_USER_ID_LENGTH)
    user_name: str = Field(min_length=1, max_length=MAX_USER_NAME_LENGTH)


class CreateRoomPayload(ParticipantPayload):
    topic: str = Field(min_length=1, max_length=MAX_TOPIC_LENGTH)


class JoinRoomPayload(ParticipantPayload):
    session_token: Optional[str] = Field(default=None, max_length=MAX_SESSION_TOKEN_LENGTH)


class RoomPayload(BaseModel):
    room_id: str
    topic: str
    created_at: str
    author: ParticipantPayload
    guest: Optional[ParticipantPayload]
    is_free: bool


class MessagePayload(BaseModel):
    room_id: str
    sender_id: str
    sender_name: str
    text: str = Field(min_length=1, max_length=MAX_MESSAGE_LENGTH)
    created_at: str


class WebSocketAuthPayload(BaseModel):
    type: str
    session_token: str = Field(min_length=1, max_length=MAX_SESSION_TOKEN_LENGTH)


class WebSocketMessagePayload(BaseModel):
    type: str
    text: Optional[str] = Field(default=None, max_length=MAX_MESSAGE_LENGTH)


class RoomAccessPayload(RoomPayload):
    session_token: str = Field(min_length=1, max_length=MAX_SESSION_TOKEN_LENGTH)


class LeaveRoomPayload(BaseModel):
    session_token: str = Field(min_length=1, max_length=MAX_SESSION_TOKEN_LENGTH)


class LeaveRoomResultPayload(BaseModel):
    status: str


def _utc_now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _participant_event(
    room_id: str,
    event: str,
    participant: Participant,
    role: str,
) -> dict:
    return {
        "type": "participant_event",
        "event": event,
        "room_id": room_id,
        "participant": {
            "user_id": participant.user_id,
            "user_name": participant.user_name,
            "role": role,
        },
        "created_at": _utc_now_iso(),
    }


def _to_participant_payload(participant: Participant) -> ParticipantPayload:
    return ParticipantPayload(user_id=participant.user_id, user_name=participant.user_name)


def _to_room_payload(room: Room) -> RoomPayload:
    return RoomPayload(
        room_id=room.room_id,
        topic=room.topic,
        created_at=room.created_at,
        author=_to_participant_payload(room.author),
        guest=_to_participant_payload(room.guest) if room.guest is not None else None,
        is_free=room.is_free,
    )


def _to_room_access_payload(room: Room, session_token: str) -> RoomAccessPayload:
    room_payload = _to_room_payload(room)
    return RoomAccessPayload(**room_payload.model_dump(), session_token=session_token)


def _to_message_payload(message: ChatMessage) -> MessagePayload:
    return MessagePayload(
        room_id=message.room_id,
        sender_id=message.sender_id,
        sender_name=message.sender_name,
        text=message.text,
        created_at=message.created_at,
    )


def _extract_session_token_from_headers(headers) -> str:
    header_token = headers.get(_ROOM_SESSION_TOKEN_HEADER, "").strip()
    if header_token:
        return header_token

    authorization_value = headers.get("authorization", "")
    scheme, _, credentials = authorization_value.partition(" ")
    if scheme.lower() != "bearer":
        return ""
    return credentials.strip()


def _resolve_session_for_room_request(room_id: str, request: Request):
    session_token = _extract_session_token_from_headers(request.headers)
    if not session_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Room session token is required.",
        )

    try:
        return room_service.resolve_session(room_id=room_id, session_token=session_token)
    except Exception as error:
        _raise_http_from_room_error(error)


def _raise_geoip_restricted_for_actions(request: Request) -> None:
    if _is_geoip_blocked(request=request):
        raise HTTPException(
            status_code=status.HTTP_451_UNAVAILABLE_FOR_LEGAL_REASONS,
            detail=_GEOIP_RESTRICTED_ACTIONS_DETAIL,
        )


def _raise_http_from_room_error(error: Exception) -> None:
    if isinstance(error, RoomValidationError):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(error)) from error
    if isinstance(error, RoomNotFoundError):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    if isinstance(error, RoomJoinError):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    if isinstance(error, RoomPermissionError):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(error)) from error
    raise error


async def _register_connection(room_id: str, user_id: str, websocket: WebSocket) -> None:
    async with _connections_lock:
        room_connections = _connections_by_room.setdefault(room_id, {})
        user_connections = room_connections.setdefault(user_id, set())
        user_connections.add(websocket)


async def _remove_connection(room_id: str, user_id: str, websocket: WebSocket) -> int:
    async with _connections_lock:
        room_connections = _connections_by_room.get(room_id)
        if room_connections is None:
            return 0

        user_connections = room_connections.get(user_id)
        if user_connections is None:
            return 0

        user_connections.discard(websocket)
        user_connection_count = len(user_connections)

        if not user_connections:
            room_connections.pop(user_id, None)
        if not room_connections:
            _connections_by_room.pop(room_id, None)

        return user_connection_count


async def _snapshot_room_connections(room_id: str) -> list[WebSocket]:
    async with _connections_lock:
        room_connections = _connections_by_room.get(room_id, {})
        return [socket for user_sockets in room_connections.values() for socket in user_sockets]


async def _register_lobby_connection(websocket: WebSocket) -> None:
    async with _lobby_connections_lock:
        _lobby_connections.add(websocket)


async def _remove_lobby_connection(websocket: WebSocket) -> None:
    async with _lobby_connections_lock:
        _lobby_connections.discard(websocket)


async def _snapshot_lobby_connections() -> list[WebSocket]:
    async with _lobby_connections_lock:
        return list(_lobby_connections)


async def _broadcast_room_event(room_id: str, payload: dict) -> None:
    sockets = await _snapshot_room_connections(room_id=room_id)
    for socket in sockets:
        try:
            await socket.send_json(payload)
        except Exception:
            continue


async def _broadcast_lobby_event(payload: dict) -> None:
    sockets = await _snapshot_lobby_connections()
    for socket in sockets:
        try:
            await socket.send_json(payload)
        except Exception:
            continue


def _rooms_catalog_updated_payload(reason: str, room: Optional[Room] = None, room_id: Optional[str] = None) -> dict:
    resolved_room_id = room.room_id if room is not None else room_id
    return {
        "type": "rooms_catalog_updated",
        "reason": reason,
        "room_id": resolved_room_id,
        "room": _to_room_payload(room).model_dump() if room is not None else None,
        "created_at": _utc_now_iso(),
    }


async def _close_room_sockets(room_id: str) -> None:
    sockets = await _snapshot_room_connections(room_id=room_id)
    for socket in sockets:
        try:
            await socket.close(code=status.WS_1001_GOING_AWAY)
        except Exception:
            continue


async def _broadcast_room_state(room: Room) -> None:
    await _broadcast_room_event(
        room_id=room.room_id,
        payload={"type": "room_updated", "room": _to_room_payload(room).model_dump()},
    )


async def _cancel_author_close_task(room_id: str) -> bool:
    async with _author_close_deadlines_lock:
        return _author_close_deadlines.pop(room_id, None) is not None


async def _clear_author_close_task(room_id: str) -> None:
    await _cancel_author_close_task(room_id=room_id)


async def _schedule_author_close(room_id: str, user_id: str) -> None:
    await _cancel_author_close_task(room_id=room_id)
    deadline = monotonic() + ROOM_CLOSE_TIMEOUT_SECONDS
    async with _author_close_deadlines_lock:
        _author_close_deadlines[room_id] = (user_id, deadline)


async def _cancel_participant_disconnect(room_id: str, user_id: str) -> bool:
    key = (room_id, user_id)
    async with _participant_disconnect_deadlines_lock:
        return _participant_disconnect_deadlines.pop(key, None) is not None


async def _schedule_participant_disconnect(room_id: str, user_id: str) -> None:
    key = (room_id, user_id)
    await _cancel_participant_disconnect(room_id=room_id, user_id=user_id)
    deadline = monotonic() + ROOM_PARTICIPANT_RECONNECT_GRACE_SECONDS
    async with _participant_disconnect_deadlines_lock:
        _participant_disconnect_deadlines[key] = deadline


async def _collect_due_author_closures() -> list[tuple[str, str]]:
    due_items: list[tuple[str, str]] = []
    now = monotonic()

    async with _author_close_deadlines_lock:
        for room_id, (user_id, deadline) in list(_author_close_deadlines.items()):
            if deadline <= now:
                due_items.append((room_id, user_id))
                _author_close_deadlines.pop(room_id, None)

    return due_items


async def _collect_due_participant_disconnects() -> list[tuple[str, str]]:
    due_items: list[tuple[str, str]] = []
    now = monotonic()

    async with _participant_disconnect_deadlines_lock:
        for key, deadline in list(_participant_disconnect_deadlines.items()):
            if deadline <= now:
                due_items.append(key)
                _participant_disconnect_deadlines.pop(key, None)

    return due_items


async def _author_close_worker() -> None:
    while True:
        await asyncio.sleep(0.5)
        due_items = await _collect_due_author_closures()
        for room_id, user_id in due_items:
            try:
                await _process_leave(room_id=room_id, user_id=user_id, emit_participant_event=False)
            except Exception:
                continue

        due_disconnects = await _collect_due_participant_disconnects()
        for room_id, user_id in due_disconnects:
            try:
                await _process_leave(room_id=room_id, user_id=user_id)
            except Exception:
                continue


@app.on_event("startup")
async def _start_author_close_worker() -> None:
    global _author_close_worker_task
    if _author_close_worker_task is not None and not _author_close_worker_task.done():
        return
    _author_close_worker_task = asyncio.create_task(_author_close_worker(), name="author-close-worker")


@app.on_event("shutdown")
async def _stop_author_close_worker() -> None:
    global _author_close_worker_task

    if _author_close_worker_task is not None:
        _author_close_worker_task.cancel()
        try:
            await _author_close_worker_task
        except asyncio.CancelledError:
            pass
        _author_close_worker_task = None

    async with _author_close_deadlines_lock:
        _author_close_deadlines.clear()
    async with _participant_disconnect_deadlines_lock:
        _participant_disconnect_deadlines.clear()
    async with _lobby_connections_lock:
        _lobby_connections.clear()


async def _handle_socket_disconnect(room_id: str, user_id: str) -> None:
    clean_user_id = user_id.strip()
    try:
        room = room_service.get_room(room_id=room_id)
    except RoomNotFoundError:
        return

    participant = room.participant_by_user_id(user_id=clean_user_id)
    if participant is None:
        return

    is_author = room.author.user_id == clean_user_id
    role = "author" if is_author else "guest"

    if is_author and ROOM_CLOSE_TIMEOUT_SECONDS > 0:
        await _broadcast_room_event(
            room_id=room_id,
            payload=_participant_event(
                room_id=room_id,
                event="left",
                participant=participant,
                role=role,
            ),
        )
        await _broadcast_room_event(
            room_id=room_id,
            payload={
                "type": "room_close_scheduled",
                "room_id": room_id,
                "seconds": ROOM_CLOSE_TIMEOUT_SECONDS,
                "participant": {
                    "user_id": participant.user_id,
                    "user_name": participant.user_name,
                    "role": role,
                },
            },
        )
        await _schedule_author_close(room_id=room_id, user_id=clean_user_id)
        return

    if not is_author and ROOM_PARTICIPANT_RECONNECT_GRACE_SECONDS > 0:
        await _broadcast_room_event(
            room_id=room_id,
            payload=_participant_event(
                room_id=room_id,
                event="disconnected",
                participant=participant,
                role=role,
            ),
        )
        await _schedule_participant_disconnect(room_id=room_id, user_id=clean_user_id)
        return

    await _process_leave(room_id=room_id, user_id=clean_user_id)


async def _process_leave(room_id: str, user_id: str, emit_participant_event: bool = True) -> LeaveResult:
    clean_user_id = user_id.strip()
    await _cancel_participant_disconnect(room_id=room_id, user_id=clean_user_id)
    participant: Optional[Participant] = None
    participant_role: Optional[str] = None

    try:
        room_before_leave = room_service.get_room(room_id=room_id)
    except RoomNotFoundError:
        room_before_leave = None

    if room_before_leave is not None:
        participant = room_before_leave.participant_by_user_id(user_id=clean_user_id)
        if participant is not None:
            participant_role = "author" if room_before_leave.author.user_id == clean_user_id else "guest"

    leave_result = room_service.leave_room(room_id=room_id, user_id=clean_user_id)

    if leave_result.status == "room_closed":
        await _clear_author_close_task(room_id=room_id)

        if emit_participant_event and participant is not None and participant_role is not None:
            await _broadcast_room_event(
                room_id=room_id,
                payload=_participant_event(
                    room_id=room_id,
                    event="left",
                    participant=participant,
                    role=participant_role,
                ),
            )

        await _broadcast_room_event(
            room_id=room_id,
            payload={
                "type": "room_closed",
                "reason": "author_left",
                "participant": (
                    {
                        "user_id": participant.user_id,
                        "user_name": participant.user_name,
                        "role": participant_role,
                    }
                    if participant is not None and participant_role is not None
                    else None
                ),
            },
        )
        await _broadcast_lobby_event(
            payload=_rooms_catalog_updated_payload(reason="room_closed", room_id=room_id),
        )
        await _close_room_sockets(room_id=room_id)
        return leave_result

    if leave_result.status == "slot_freed":
        if emit_participant_event and participant is not None and participant_role is not None:
            await _broadcast_room_event(
                room_id=room_id,
                payload=_participant_event(
                    room_id=room_id,
                    event="left",
                    participant=participant,
                    role=participant_role,
                ),
            )

        try:
            room = room_service.get_room(room_id=room_id)
        except RoomNotFoundError:
            return leave_result
        await _broadcast_room_state(room=room)
        await _broadcast_lobby_event(
            payload=_rooms_catalog_updated_payload(reason="room_freed", room=room),
        )
        return leave_result

    return leave_result


@app.get("/health", tags=["system"])
def healthcheck() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/rooms/free", response_model=list[RoomPayload], tags=["rooms"])
def list_free_rooms() -> list[RoomPayload]:
    return [_to_room_payload(room) for room in room_service.list_free_rooms()]


@app.get("/rooms/{room_id}", response_model=RoomPayload, tags=["rooms"])
def get_room(room_id: str, request: Request) -> RoomPayload:
    try:
        _resolve_session_for_room_request(room_id=room_id, request=request)
        room = room_service.get_room(room_id=room_id)
    except Exception as error:
        _raise_http_from_room_error(error)
    return _to_room_payload(room)


@app.post("/rooms", response_model=RoomAccessPayload, status_code=status.HTTP_201_CREATED, tags=["rooms"])
async def create_room(payload: CreateRoomPayload, request: Request) -> RoomAccessPayload:
    try:
        _raise_geoip_restricted_for_actions(request=request)
        room = room_service.create_room(
            topic=payload.topic,
            author_id=payload.user_id,
            author_name=payload.user_name,
        )
        session_token = room_service.issue_session_token(room_id=room.room_id, user_id=payload.user_id)
    except Exception as error:
        _raise_http_from_room_error(error)
    if ROOM_CLOSE_TIMEOUT_SECONDS > 0:
        await _schedule_author_close(room_id=room.room_id, user_id=payload.user_id.strip())
    await _broadcast_lobby_event(
        payload=_rooms_catalog_updated_payload(reason="room_created", room=room),
    )
    return _to_room_access_payload(room=room, session_token=session_token)


@app.post("/rooms/{room_id}/join", response_model=RoomAccessPayload, tags=["rooms"])
async def join_room(room_id: str, payload: JoinRoomPayload, request: Request) -> RoomAccessPayload:
    previous_guest_id: Optional[str] = None
    clean_user_id = payload.user_id.strip()

    try:
        _raise_geoip_restricted_for_actions(request=request)
        room_before_join = room_service.get_room(room_id=room_id)
        if room_before_join.guest is not None:
            previous_guest_id = room_before_join.guest.user_id

        room = room_service.join_room(
            room_id=room_id,
            user_id=payload.user_id,
            user_name=payload.user_name,
            session_token=payload.session_token,
        )
        await _cancel_participant_disconnect(room_id=room_id, user_id=clean_user_id)
        session_token = room_service.issue_session_token(room_id=room_id, user_id=payload.user_id)
    except Exception as error:
        _raise_http_from_room_error(error)

    joined_participant = room.participant_by_user_id(user_id=clean_user_id)
    is_new_guest_join = (
        room.author.user_id != clean_user_id
        and joined_participant is not None
        and room.guest is not None
        and room.guest.user_id == clean_user_id
        and previous_guest_id != clean_user_id
    )

    if is_new_guest_join and joined_participant is not None:
        await _broadcast_room_event(
            room_id=room_id,
            payload=_participant_event(
                room_id=room_id,
                event="joined",
                participant=joined_participant,
                role="guest",
            ),
        )
        await _broadcast_lobby_event(
            payload=_rooms_catalog_updated_payload(reason="room_became_busy", room=room),
        )

    await _broadcast_room_state(room=room)
    return _to_room_access_payload(room=room, session_token=session_token)


@app.post("/rooms/{room_id}/leave", response_model=LeaveRoomResultPayload, tags=["rooms"])
async def leave_room(room_id: str, payload: LeaveRoomPayload) -> LeaveRoomResultPayload:
    try:
        session = room_service.resolve_session(room_id=room_id, session_token=payload.session_token)
        leave_result = await _process_leave(room_id=room_id, user_id=session.user_id)
    except Exception as error:
        _raise_http_from_room_error(error)
    return LeaveRoomResultPayload(status=leave_result.status)


@app.websocket("/ws/lobby")
async def lobby_socket(websocket: WebSocket) -> None:
    await websocket.accept()
    await _register_lobby_connection(websocket=websocket)

    await websocket.send_json(
        {
            "type": "rooms_catalog_snapshot",
            "rooms": [_to_room_payload(room).model_dump() for room in room_service.list_free_rooms()],
        }
    )

    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        await _remove_lobby_connection(websocket=websocket)


@app.websocket("/ws/rooms/{room_id}")
async def room_chat_socket(websocket: WebSocket, room_id: str) -> None:
    user_id: Optional[str] = None
    has_registered_connection = False
    await websocket.accept()

    try:
        try:
            auth_raw = await websocket.receive_json()
        except ValueError:
            await websocket.send_json({"type": "error", "message": "Invalid JSON payload."})
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return

        try:
            auth_message = WebSocketAuthPayload(**auth_raw)
        except ValidationError:
            await websocket.send_json({"type": "error", "message": "Invalid auth format."})
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return

        if auth_message.type != "auth":
            await websocket.send_json({"type": "error", "message": "Auth message is required."})
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return

        try:
            session = room_service.resolve_session(room_id=room_id, session_token=auth_message.session_token)
        except RoomError:
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return

        user_id = session.user_id
        await _register_connection(room_id=room_id, user_id=user_id, websocket=websocket)
        has_registered_connection = True
        await _cancel_participant_disconnect(room_id=room_id, user_id=user_id)

        try:
            room = room_service.get_room(room_id=room_id)
        except Exception as error:
            _raise_http_from_room_error(error)

        if room.author.user_id == user_id:
            await _cancel_author_close_task(room_id=room_id)

        await websocket.send_json(
            {
                "type": "room_state",
                "room": _to_room_payload(room).model_dump(),
                "history": [_to_message_payload(message).model_dump() for message in room.messages],
            }
        )

        while True:
            try:
                incoming_raw = await websocket.receive_json()
            except ValueError:
                await websocket.send_json({"type": "error", "message": "Invalid JSON payload."})
                continue

            try:
                incoming = WebSocketMessagePayload(**incoming_raw)
            except ValidationError:
                await websocket.send_json({"type": "error", "message": "Invalid message format."})
                continue

            if incoming.type != "chat_message":
                await websocket.send_json({"type": "error", "message": "Unsupported message type."})
                continue

            try:
                message = room_service.post_message(room_id=room_id, user_id=user_id, text=incoming.text or "")
            except RoomError as error:
                await websocket.send_json({"type": "error", "message": str(error)})
                continue

            await _broadcast_room_event(
                room_id=room_id,
                payload={"type": "chat_message", "message": _to_message_payload(message).model_dump()},
            )
    except WebSocketDisconnect:
        pass
    except HTTPException as error:
        try:
            await websocket.send_json({"type": "error", "message": error.detail})
        except Exception:
            pass
    finally:
        if has_registered_connection and user_id is not None:
            remaining_connections = await _remove_connection(room_id=room_id, user_id=user_id, websocket=websocket)
            if remaining_connections == 0:
                try:
                    await _handle_socket_disconnect(room_id=room_id, user_id=user_id)
                except RoomValidationError:
                    return
