# Backend (Python / FastAPI)

Базовый backend для онлайн-чата на WebSocket с жизненным циклом комнат:
- при создании комнаты тема обязательна;
- в комнате одновременно максимум 2 участника (автор и один собеседник);
- автор не может присоединиться к собственной комнате как собеседник (только по совпадению `user_id`);
- если автор отключается, комната закрывается по таймауту;
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
- `GET /rooms/{room_id}` — состояние комнаты.
- `POST /rooms` — создать комнату.
- `POST /rooms/{room_id}/join` — присоединиться к комнате.
- `POST /rooms/{room_id}/leave` — выйти из комнаты.
- `WS /ws/rooms/{room_id}?user_id=<id>` — обмен сообщениями.
- `WS /ws/lobby` — push-уведомления об изменении каталога доступных комнат.
- `room_id` создается backend как `UUID4` (полный UUID-строкой).

Переменные окружения:
- `ROOM_CLOSE_TIMEOUT_SECONDS` — таймаут автозакрытия комнаты после отключения автора (по умолчанию `300`).
- `ROOM_PARTICIPANT_RECONNECT_GRACE_SECONDS` — grace-таймаут (сек) перед удалением отключившегося участника (по умолчанию `300`).

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

### Формат WebSocket-сообщения от клиента
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
