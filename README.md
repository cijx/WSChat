# Online Chat Project

Монорепозиторий веб-приложения онлайн-чата (backend + frontend).

## Стек
- Backend: Python (FastAPI).
- Frontend: Vue 3 (Vite).

## Базовые правила продукта
- В приложении используется WebSocket-чат.
- Регистрация и модерация не требуются.
- Любой пользователь может создать комнату.
- Любой другой один пользователь может присоединиться к свободной комнате.
- Автор не может присоединиться к собственной комнате как собеседник (проверка только по `user_id`).
- При создании комнаты тема обязательна.
- Если автор комнаты отключается, комната закрывается по таймауту.
- Если собеседник отключается, слот освобождается после grace-таймаута на переподключение.
- На стартовой странице пользователь может создать комнату или подключиться к свободной.
- Для подключения к комнате пользователь должен указать имя (поле `Ваше имя`).
- В комнате отображаются системные сообщения о входе и выходе участников.
- При обрыве соединения гостя (например, закрытии окна) в комнате сразу показывается системное уведомление о дисконнекте.
- Внутри комнаты отображается полный `room_id`, чтобы его можно было передать собеседнику для подключения.
- Список доступных комнат обновляется автоматически через отдельный WebSocket-канал лобби.
- В комнате доступны быстрые кнопки смайликов, смайлики отправляются как обычные сообщения.
- Текст сообщений поддерживает форматирование: `**жирный**`, `*курсив*`, `` `код` ``, `~~зачеркнутый~~`, переносы строк.
- Для backend доступна geoIP-ограничение: для запрещенных стран блокируются создание комнаты и подключение к комнате с явным сообщением.

## Структура
- `backend/` — серверная часть.
- `backend/app/` — приложение FastAPI.
- `backend/app/blocked_countries.txt` — список запрещенных стран (ISO alpha-2).
- `backend/tests/` — backend-тесты.
- `backend/.venv/` — локальное виртуальное окружение Python.
- `frontend/` — клиентская часть на Vue.
- `frontend/src/` — исходники frontend.
- `memory.md` — журнал выполненных задач.
- `AGENTS.md` — обязательные правила работы агента.

## Процесс работы агента
- Перед началом новой задачи агент читает актуальные записи в `memory.md`.
- По итогам каждой завершенной задачи агент обновляет `memory.md` (цель, действия, команды/проверки, результат).
- После изменений в коде/конфигурации агент перезапускает проект (по умолчанию через Docker Compose) и проверяет доступность сервисов.

## Быстрый старт

### Backend
```bash
cd backend
source .venv/bin/activate
pip install -r requirements-dev.txt
uvicorn app.main:app --reload
```

Основные backend endpoints:
- `GET /health`
- `GET /rooms/free`
- `GET /rooms/{room_id}`
- `POST /rooms`
- `POST /rooms/{room_id}/join`
- `POST /rooms/{room_id}/leave`
- `WS /ws/rooms/{room_id}?user_id=<id>`
- `WS /ws/lobby` (уведомления о создании/освобождении/изменении доступности комнат)
- `room_id` генерируется backend как `UUID4` (строка вида `xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx`).

Backend env:
- `ROOM_CLOSE_TIMEOUT_SECONDS` — таймаут автозакрытия комнаты после отключения автора (по умолчанию `300`).
- `ROOM_PARTICIPANT_RECONNECT_GRACE_SECONDS` — grace-таймаут (сек) перед удалением отключившегося участника из комнаты (по умолчанию `300`).
- `GEOIP_COUNTRY_HEADERS` — список HTTP-заголовков (через запятую), откуда читается код страны. По умолчанию: `CF-IPCountry,X-Country-Code,X-Geo-Country`.
- `GEOIP_BLOCKLIST_FILE` — путь к файлу со списком запрещенных стран (по умолчанию `backend/app/blocked_countries.txt` локально и `/app/app/blocked_countries.txt` в Docker).

GeoIP blocklist:
- формат файла: один код страны `ISO 3166-1 alpha-2` на строку;
- пустые строки и строки с `#` игнорируются;
- проверка применяется к операциям `POST /rooms` и `POST /rooms/{room_id}/join`;
- чтение списка комнат, healthcheck и открытие интерфейса остаются доступными.

### Frontend
```bash
cd frontend
npm install
npm run dev
```

По умолчанию фронтенд ходит в backend по `http://localhost:8000`.
Если страница открыта не на `localhost` (например, по IP), фронтенд автоматически использует хост из URL страницы и порт `8000`.
Идентификатор пользователя хранится в `localStorage` (общий для вкладок одного браузерного профиля).
Текущая комната сохраняется в `localStorage` и восстанавливается после обновления страницы.
Список доступных комнат обновляется автоматически при событиях `rooms_catalog_updated` из `ws/lobby`.
Режим WebSocket-протокола управляется через `VITE_WS_PROTOCOL_MODE`:
- `auto` (по умолчанию): `https -> wss`, `http -> ws`;
- `wss`: принудительно использовать `wss://`;
- `ws`: принудительно использовать `ws://` (отключает `wss`).

## Запуск в Docker

### Требования
- Docker Engine 24+
- Docker Compose v2

### Команды
```bash
docker compose up --build -d
```

После старта:
- frontend: `http://localhost:5173`
- backend: `http://localhost:8000`
- healthcheck: `http://localhost:8000/health`

Переопределить таймаут закрытия комнаты:
```bash
ROOM_CLOSE_TIMEOUT_SECONDS=120 docker compose up --build -d
```

Переопределить путь к файлу блокировки стран:
```bash
GEOIP_BLOCKLIST_FILE=/absolute/path/blocked_countries.txt docker compose up --build -d
```

Принудительно включить `wss` в frontend:
```bash
VITE_WS_PROTOCOL_MODE=wss docker compose up --build -d
```

Принудительно отключить `wss` (использовать только `ws`):
```bash
VITE_WS_PROTOCOL_MODE=ws docker compose up --build -d
```

Остановить и удалить контейнеры:
```bash
docker compose down
```

## Развертывание на VDS

Ниже минимальная прод-схема для Ubuntu 22.04/24.04:
- `chat.example.com` -> frontend
- `api.example.com` -> backend API + WebSocket
- TLS терминируется на Nginx (хостовая система), приложение работает в Docker.

### 1. Подготовка сервера
```bash
sudo apt update
sudo apt install -y ca-certificates curl git ufw nginx certbot python3-certbot-nginx
```

Разрешить только нужные порты:
```bash
sudo ufw allow OpenSSH
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw deny 8000/tcp
sudo ufw deny 5173/tcp
sudo ufw --force enable
```

### 2. Установить Docker и Compose plugin
```bash
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER
newgrp docker
docker --version
docker compose version
```

### 3. Настроить DNS
Создайте A-записи:
- `chat.example.com` -> IP вашей VDS
- `api.example.com` -> IP вашей VDS

### 4. Клонировать проект и создать `.env`
```bash
cd /opt
sudo mkdir -p /opt/chat
sudo chown -R $USER:$USER /opt/chat
cd /opt/chat
git clone <URL_ВАШЕГО_РЕПОЗИТОРИЯ> .
```

Создайте файл `/opt/chat/.env`:
```dotenv
ROOM_CLOSE_TIMEOUT_SECONDS=300
ROOM_PARTICIPANT_RECONNECT_GRACE_SECONDS=300

# Frontend build-time переменные:
VITE_API_BASE_URL=https://api.example.com
VITE_WS_BASE_URL=wss://api.example.com
VITE_WS_PROTOCOL_MODE=auto
```

Примечание по `wss/ws`:
- `VITE_WS_PROTOCOL_MODE=auto` — рекомендовано для production (`https -> wss`, `http -> ws`).
- `VITE_WS_PROTOCOL_MODE=wss` — принудительно `wss`.
- `VITE_WS_PROTOCOL_MODE=ws` — принудительно `ws` (только если frontend тоже работает по `http`; при `https` браузер заблокирует `ws` как mixed content).

### 5. Поднять контейнеры
```bash
cd /opt/chat
docker compose up --build -d
docker compose ps
```

### 6. Настроить Nginx reverse proxy
Создайте `/etc/nginx/sites-available/chat`:
```nginx
server {
    listen 80;
    server_name chat.example.com;

    location / {
        proxy_pass http://127.0.0.1:5173;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}

server {
    listen 80;
    server_name api.example.com;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

Активируйте конфиг:
```bash
sudo ln -s /etc/nginx/sites-available/chat /etc/nginx/sites-enabled/chat
sudo nginx -t
sudo systemctl reload nginx
```

### 7. Выпустить TLS-сертификаты
```bash
sudo certbot --nginx -d chat.example.com -d api.example.com
```

Проверить автообновление:
```bash
sudo systemctl status certbot.timer
```

### 8. Проверка после деплоя
```bash
curl -sS https://api.example.com/health
```
Должно вернуть:
```json
{"status":"ok"}
```

Проверьте в браузере:
- `https://chat.example.com` открывается frontend;
- создание комнаты, подключение и обмен сообщениями работают;
- WebSocket работает по `wss://`.

### 9. Обновление на VDS
```bash
cd /opt/chat
git pull
docker compose up --build -d --force-recreate
docker compose ps
```

### Что входит в Docker-конфигурацию
- `docker-compose.yml` — оркестрация сервисов `backend` и `frontend`.
- `backend/Dockerfile` — образ FastAPI + Uvicorn.
- `frontend/Dockerfile` — multi-stage build (Vite build + Nginx).
- `frontend/nginx.conf` — отдача SPA с fallback на `index.html`.
- `.dockerignore` — исключение лишних файлов из build-контекста.

## Проверки качества

### Backend
```bash
cd backend
source .venv/bin/activate
python -m unittest discover -s tests -p "test_*.py" -v
```

### Frontend
```bash
cd frontend
npm run lint
npm run test
npm run build
```

### Docker-конфигурация
```bash
cd backend
source .venv/bin/activate
python -m unittest discover -s tests -p "test_docker_configuration.py" -v
docker compose config
```

## Текущее состояние
- Реализован базовый lifecycle комнат и чат по WebSocket.
- Добавлены backend-тесты для логики комнат (создание, join/leave, ограничения участников, сообщения).
- Добавлен frontend экран с созданием/подключением к комнате и чатом.
- Обновлен визуальный стиль frontend: контрастное двухпанельное лобби (control-panel + board свободных комнат) и рабочее окно чата с bubble-сообщениями и системными событиями.
- В активной комнате область сообщений зафиксирована и прокручивается внутри панели (без растягивания всего чата).
- Интерфейс frontend полностью русифицирован (подписи, кнопки, статусы и системные сообщения).

## Ограничения текущего окружения
- Для frontend требуется установленный Node.js и npm.
- Для установки Python/npm зависимостей нужен доступ к сети.
