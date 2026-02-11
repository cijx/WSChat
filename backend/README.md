# Backend (Python / FastAPI)

Базовый backend для онлайн-чата на WebSocket с жизненным циклом комнат:
- при создании комнаты тема обязательна;
- в комнате одновременно максимум 2 участника (автор и один собеседник);
- автор не может присоединиться к собственной комнате как собеседник;
- для доступа к операциям комнаты используется серверный `session_token` (анонимная authz без доверия к client-controlled `user_id`);
- если автор отключается, комната закрывается по таймауту;
- таймаут автора запускается при создании комнаты и после отключения автора; подключение гостя не отменяет закрытие при отсутствии автора;
- если собеседник отключается, слот освобождается после grace-таймаута на переподключение.

## Быстрый старт

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements-dev.txt
uvicorn app.main:app --reload
```

## API
- `GET /health` — healthcheck.
- `GET /rooms/free` — список свободных комнат (без собеседника).
- `GET /rooms/{room_id}` — состояние комнаты (только для участника; нужен `X-Room-Session-Token` или `Authorization: Bearer <token>`).
- `POST /rooms` — создать комнату (возвращает `session_token` автора).
- `POST /rooms/{room_id}/join` — присоединиться к комнате (возвращает `session_token` участника; для re-join уже занятого гостевого слота тем же `user_id` обязателен `session_token` в payload).
- `POST /rooms/{room_id}/leave` — выйти из комнаты (требует `session_token` в JSON body).
- `WS /ws/rooms/{room_id}` — обмен сообщениями (первым сообщением должен быть auth-пакет с `session_token`).
- `WS /ws/lobby` — push-уведомления об изменении каталога доступных комнат.
- `room_id` создается backend как `UUID4` (полный UUID-строкой).
- Ограничения длины данных:
  - `user_id <= 128`
  - `user_name <= 80`
  - `topic <= 200`
  - `message text <= 4000`

Переменные окружения:
- `ROOM_CLOSE_TIMEOUT_SECONDS` — таймаут автозакрытия комнаты после отключения автора (по умолчанию `300`).
- `ROOM_PARTICIPANT_RECONNECT_GRACE_SECONDS` — grace-таймаут (сек) перед удалением отключившегося участника (по умолчанию `300`).
- `ROOM_MAX_ACTIVE_ROOMS` — максимальное число одновременно активных комнат в памяти (по умолчанию `1000`).
- `ROOM_MAX_MESSAGES_PER_ROOM` — лимит длины истории сообщений на комнату в памяти (по умолчанию `500`).
- `GEOIP_COUNTRY_HEADERS` — список заголовков с кодом страны (по умолчанию `CF-IPCountry,X-Country-Code,X-Geo-Country`).
- `GEOIP_BLOCKLIST_FILE` — путь к файлу blocklist стран.
- `GEOIP_TRUSTED_PROXIES` — список доверенных адресов/сетей reverse-proxy, от которых разрешено принимать geoIP-заголовки.

WebSocket server также отправляет системные события:
- `participant_event` — кто вошел/вышел из комнаты.
- `room_close_scheduled` — автор отключился, закрытие запланировано по таймауту.
- `room_closed` — закрытие комнаты при выходе/отсутствии автора.
- `rooms_catalog_updated` (в `ws/lobby`) — изменения видимости/доступности комнат (`room_created`, `room_became_busy`, `room_freed`, `room_closed`).

### Пример создания комнаты
```json
{
  "user_id": "u-1",
  "user_name": "Alice",
  "topic": "Weekend plans"
}
```

Пример ответа:
```json
{
  "room_id": "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
  "topic": "Weekend plans",
  "created_at": "2026-02-11T08:00:00+00:00",
  "author": {
    "user_id": "u-1",
    "user_name": "Alice"
  },
  "guest": null,
  "is_free": true,
  "session_token": "<opaque-token>"
}
```

### Формат WebSocket-сообщения от клиента
Первое сообщение после открытия room socket:
```json
{
  "type": "auth",
  "session_token": "<opaque-token>"
}
```

После успешной авторизации:
```json
{
  "type": "chat_message",
  "text": "Hello!"
}
```

## Проверки

```bash
cd backend
source .venv/bin/activate
python -m unittest discover -s tests -p "test_*.py" -v
```

## Docker

Локальный запуск backend-контейнера:

```bash
docker build -f backend/Dockerfile -t chat-backend .
docker run --rm -p 8000:8000 chat-backend
```

Рекомендуемый запуск через compose (backend + frontend):

```bash
docker compose up --build -d
```
