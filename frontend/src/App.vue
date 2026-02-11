<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'

const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL || defaultApiBaseUrl()).replace(/\/$/, '')
const WS_PROTOCOL_MODE = normalizeWsProtocolMode(import.meta.env.VITE_WS_PROTOCOL_MODE)
const WS_BASE_URL = toWebSocketBaseUrl(import.meta.env.VITE_WS_BASE_URL || API_BASE_URL, WS_PROTOCOL_MODE).replace(
  /\/$/,
  ''
)
const ACTIVE_ROOM_STORAGE_KEY = 'chat.active_room_id'
const ACTIVE_ROOM_SESSION_TOKEN_STORAGE_KEY = 'chat.active_room_session_token'
const LOBBY_RECONNECT_DELAY_MS = 1000
const QUICK_EMOJIS = ['😀', '😂', '😎', '🤝', '🎉', '🔥', '❤️', '👍']
const INLINE_FORMAT_PATTERN = /`[^`\n]+`|\*\*[^*\n]+\*\*|~~[^~\n]+~~|\*[^*\n]+\*/g

const userId = ref(getOrCreateUserId())
const userName = ref(getStoredUserName())
const createTopic = ref('')
const joinRoomId = ref('')
const freeRooms = ref([])
const currentRoom = ref(null)
const activeRoomSessionToken = ref(getStoredActiveRoomSessionToken())
const messages = ref([])
const messageDraft = ref('')
const composerInputRef = ref(null)
const messagesPanelRef = ref(null)
const statusText = ref('Не подключено')
const errorText = ref('')
const loadingRooms = ref(false)
const creatingRoom = ref(false)
const joiningRoom = ref(false)

let socket = null
let lobbySocket = null
let lobbyReconnectTimer = null
let shouldKeepLobbyConnection = true

const isInRoom = computed(() => currentRoom.value !== null)
const canCreateRoom = computed(() => userName.value.trim() !== '' && createTopic.value.trim() !== '' && !creatingRoom.value)
const canJoinById = computed(() => userName.value.trim() !== '' && joinRoomId.value.trim() !== '' && !joiningRoom.value)
const canJoinFromList = computed(() => userName.value.trim() !== '' && !joiningRoom.value)
const sortedFreeRooms = computed(() => {
  return [...freeRooms.value].sort((left, right) => {
    return new Date(right.created_at).getTime() - new Date(left.created_at).getTime()
  })
})

function defaultApiBaseUrl() {
  if (typeof window !== 'undefined' && window.location) {
    const protocol = window.location.protocol === 'https:' ? 'https:' : 'http:'
    return `${protocol}//${window.location.hostname}:8000`
  }
  return 'http://localhost:8000'
}

function normalizeWsProtocolMode(rawMode) {
  const normalized = String(rawMode || 'auto')
    .trim()
    .toLowerCase()
  if (normalized === 'wss' || normalized === 'ws' || normalized === 'auto') {
    return normalized
  }
  return 'auto'
}

function forceWebSocketProtocol(baseUrl, secure) {
  if (baseUrl.startsWith('https://')) {
    return `${secure ? 'wss' : 'ws'}://${baseUrl.slice('https://'.length)}`
  }
  if (baseUrl.startsWith('http://')) {
    return `${secure ? 'wss' : 'ws'}://${baseUrl.slice('http://'.length)}`
  }
  if (baseUrl.startsWith('wss://')) {
    return `${secure ? 'wss' : 'ws'}://${baseUrl.slice('wss://'.length)}`
  }
  if (baseUrl.startsWith('ws://')) {
    return `${secure ? 'wss' : 'ws'}://${baseUrl.slice('ws://'.length)}`
  }
  return `${secure ? 'wss' : 'ws'}://${baseUrl.replace(/^\/\//, '')}`
}

function toWebSocketBaseUrl(baseUrl, protocolMode = 'auto') {
  const mode = normalizeWsProtocolMode(protocolMode)

  if (mode === 'wss') {
    return forceWebSocketProtocol(baseUrl, true)
  }

  if (mode === 'ws') {
    return forceWebSocketProtocol(baseUrl, false)
  }

  if (baseUrl.startsWith('wss://') || baseUrl.startsWith('ws://')) {
    return baseUrl
  }
  if (baseUrl.startsWith('https://')) {
    return forceWebSocketProtocol(baseUrl, true)
  }
  if (baseUrl.startsWith('http://')) {
    return forceWebSocketProtocol(baseUrl, false)
  }
  return forceWebSocketProtocol(baseUrl, false)
}

function makeFallbackId() {
  return `u-${Date.now()}-${Math.random().toString(16).slice(2)}`
}

function getOrCreateUserId() {
  try {
    const localExisting = localStorage.getItem('chat.user_id')
    if (localExisting && localExisting.trim() !== '') return localExisting

    const sessionExisting = sessionStorage.getItem('chat.user_id')
    if (sessionExisting && sessionExisting.trim() !== '') {
      localStorage.setItem('chat.user_id', sessionExisting)
      return sessionExisting
    }

    const next =
      typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function'
        ? crypto.randomUUID()
        : makeFallbackId()
    localStorage.setItem('chat.user_id', next)
    return next
  } catch {
    return makeFallbackId()
  }
}

function isRoomOwnedByCurrentUser(room) {
  if (!room || !room.author) {
    return false
  }
  return room.author.user_id === userId.value
}

function getStoredUserName() {
  try {
    return localStorage.getItem('chat.user_name') || ''
  } catch {
    return ''
  }
}

function persistUserName() {
  try {
    localStorage.setItem('chat.user_name', userName.value.trim())
  } catch {
    return
  }
}

function getStoredActiveRoomId() {
  try {
    return localStorage.getItem(ACTIVE_ROOM_STORAGE_KEY) || ''
  } catch {
    return ''
  }
}

function persistActiveRoomId(roomId) {
  try {
    localStorage.setItem(ACTIVE_ROOM_STORAGE_KEY, roomId)
  } catch {
    return
  }
}

function getStoredActiveRoomSessionToken() {
  try {
    return localStorage.getItem(ACTIVE_ROOM_SESSION_TOKEN_STORAGE_KEY) || ''
  } catch {
    return ''
  }
}

function persistActiveRoomSessionToken(sessionToken) {
  try {
    localStorage.setItem(ACTIVE_ROOM_SESSION_TOKEN_STORAGE_KEY, sessionToken)
  } catch {
    return
  }
}

function clearStoredActiveRoom() {
  try {
    localStorage.removeItem(ACTIVE_ROOM_STORAGE_KEY)
    localStorage.removeItem(ACTIVE_ROOM_SESSION_TOKEN_STORAGE_KEY)
    activeRoomSessionToken.value = ''
  } catch {
    return
  }
}

function closeSocket() {
  if (!socket) {
    return
  }

  const previousSocket = socket
  socket = null
  previousSocket.onopen = null
  previousSocket.onclose = null
  previousSocket.onerror = null
  previousSocket.onmessage = null

  if (previousSocket.readyState === WebSocket.OPEN || previousSocket.readyState === WebSocket.CONNECTING) {
    previousSocket.close()
  }
}

function clearLobbyReconnectTimer() {
  if (lobbyReconnectTimer === null) {
    return
  }
  clearTimeout(lobbyReconnectTimer)
  lobbyReconnectTimer = null
}

function closeLobbySocket() {
  if (!lobbySocket) {
    return
  }

  const previousSocket = lobbySocket
  lobbySocket = null
  previousSocket.onopen = null
  previousSocket.onclose = null
  previousSocket.onerror = null
  previousSocket.onmessage = null

  if (previousSocket.readyState === WebSocket.OPEN || previousSocket.readyState === WebSocket.CONNECTING) {
    previousSocket.close()
  }
}

function scheduleLobbyReconnect() {
  if (!shouldKeepLobbyConnection || lobbyReconnectTimer !== null) {
    return
  }

  lobbyReconnectTimer = setTimeout(() => {
    lobbyReconnectTimer = null
    connectLobbySocket()
  }, LOBBY_RECONNECT_DELAY_MS)
}

function handleLobbyMessage(payload) {
  if (payload.type === 'rooms_catalog_snapshot') {
    if (Array.isArray(payload.rooms)) {
      freeRooms.value = payload.rooms
    }
    return
  }

  if (payload.type === 'rooms_catalog_updated') {
    if (!isInRoom.value) {
      loadFreeRooms()
    }
  }
}

function connectLobbySocket() {
  if (!shouldKeepLobbyConnection) {
    return
  }

  clearLobbyReconnectTimer()
  closeLobbySocket()

  const ws = new WebSocket(`${WS_BASE_URL}/ws/lobby`)
  lobbySocket = ws

  ws.onmessage = (event) => {
    let payload = null
    try {
      payload = JSON.parse(event.data)
    } catch {
      return
    }
    handleLobbyMessage(payload)
  }

  ws.onerror = () => {
    // reconnection happens in onclose
  }

  ws.onclose = () => {
    if (lobbySocket === ws) {
      lobbySocket = null
    }
    scheduleLobbyReconnect()
  }
}

async function apiRequest(path, options = {}) {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    headers: {
      'Content-Type': 'application/json',
      ...(options.headers || {})
    },
    ...options
  })

  if (!response.ok) {
    let detail = `HTTP ${response.status}`
    try {
      const payload = await response.json()
      detail = payload.detail || detail
    } catch {
      detail = `HTTP ${response.status}`
    }
    throw new Error(detail)
  }

  return response.json()
}

async function loadFreeRooms() {
  loadingRooms.value = true
  errorText.value = ''

  try {
    freeRooms.value = await apiRequest('/rooms/free')
  } catch (error) {
    errorText.value = error instanceof Error ? error.message : 'Не удалось загрузить комнаты'
  } finally {
    loadingRooms.value = false
  }
}

function applyRoomState(room, sessionToken = null) {
  currentRoom.value = room
  persistActiveRoomId(room.room_id)
  if (typeof sessionToken === 'string' && sessionToken.trim() !== '') {
    activeRoomSessionToken.value = sessionToken
    persistActiveRoomSessionToken(sessionToken)
  }
  statusText.value = `Подключено к комнате ${room.room_id}`
}

function requireSessionToken(roomPayload) {
  const sessionToken = typeof roomPayload.session_token === 'string' ? roomPayload.session_token.trim() : ''
  if (!sessionToken) {
    throw new Error('Не удалось получить токен сессии комнаты.')
  }
  return sessionToken
}

function normalizeChatMessage(message) {
  return {
    room_id: message.room_id,
    sender_id: message.sender_id,
    sender_name: message.sender_name,
    text: message.text,
    created_at: message.created_at
  }
}

function createSystemMessage(text, createdAt) {
  return {
    room_id: currentRoom.value ? currentRoom.value.room_id : '',
    sender_id: 'system',
    sender_name: 'Система',
    text,
    created_at: createdAt || new Date().toISOString()
  }
}

function messageClass(message) {
  if (message.sender_id === 'system') {
    return 'msg-system'
  }
  if (message.sender_id === userId.value) {
    return 'msg-own'
  }
  return 'msg-peer'
}

function messageAuthorLabel(message) {
  if (message.sender_id === 'system') {
    return 'Система'
  }
  if (message.sender_id === userId.value) {
    return 'Вы'
  }
  return message.sender_name
}

function formatMessageTime(value) {
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) {
    return ''
  }
  return parsed.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
}

function formatRoomCreatedAt(value) {
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) {
    return ''
  }
  return parsed.toLocaleString([], {
    month: 'short',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit'
  })
}

function parseInlineSegments(textLine) {
  const segments = []
  let currentIndex = 0

  for (const match of textLine.matchAll(new RegExp(INLINE_FORMAT_PATTERN))) {
    const startIndex = match.index ?? 0
    const token = match[0]

    if (startIndex > currentIndex) {
      segments.push({
        type: 'text',
        text: textLine.slice(currentIndex, startIndex)
      })
    }

    let type = 'text'
    let text = token
    if (token.startsWith('**') && token.endsWith('**')) {
      type = 'bold'
      text = token.slice(2, -2)
    } else if (token.startsWith('~~') && token.endsWith('~~')) {
      type = 'strike'
      text = token.slice(2, -2)
    } else if (token.startsWith('*') && token.endsWith('*')) {
      type = 'italic'
      text = token.slice(1, -1)
    } else if (token.startsWith('`') && token.endsWith('`')) {
      type = 'code'
      text = token.slice(1, -1)
    }

    segments.push({ type, text })
    currentIndex = startIndex + token.length
  }

  if (currentIndex < textLine.length || segments.length === 0) {
    segments.push({
      type: 'text',
      text: textLine.slice(currentIndex)
    })
  }

  return segments
}

function formatMessageSegments(value) {
  const lines = String(value || '').split('\n')
  return lines.map((line, index) => ({
    isLast: index === lines.length - 1,
    segments: parseInlineSegments(line)
  }))
}

function scrollMessagesToBottom() {
  const panel = messagesPanelRef.value
  if (!panel) {
    return
  }

  panel.scrollTop = panel.scrollHeight
}

function connectToRoom(roomId, sessionToken) {
  closeSocket()
  errorText.value = ''

  const cleanSessionToken = String(sessionToken || '').trim()
  if (!cleanSessionToken) {
    errorText.value = 'Не удалось получить токен доступа к комнате.'
    return
  }

  const target = `${WS_BASE_URL}/ws/rooms/${encodeURIComponent(roomId)}?session_token=${encodeURIComponent(cleanSessionToken)}`
  const ws = new WebSocket(target)
  socket = ws
  let receivedRoomState = false
  statusText.value = 'Подключение...'

  ws.onopen = () => {
    statusText.value = 'Подключено'
  }

  ws.onmessage = (event) => {
    let payload = null
    try {
      payload = JSON.parse(event.data)
    } catch {
      return
    }

    if (payload.type === 'room_state') {
      receivedRoomState = true
      applyRoomState(payload.room, cleanSessionToken)
      messages.value = (payload.history || []).map(normalizeChatMessage)
      return
    }

    if (payload.type === 'room_updated') {
      if (currentRoom.value && payload.room && payload.room.room_id === currentRoom.value.room_id) {
        currentRoom.value = payload.room
      }
      loadFreeRooms()
      return
    }

    if (payload.type === 'chat_message') {
      if (payload.message) {
        messages.value = [...messages.value, normalizeChatMessage(payload.message)]
      }
      return
    }

    if (payload.type === 'participant_event') {
      const participant = payload.participant
      if (participant && participant.user_name) {
        let action = 'вышел(ла) из комнаты.'
        if (payload.event === 'joined') {
          action = 'вошел(а) в комнату.'
        } else if (payload.event === 'disconnected') {
          action = 'потерял(а) соединение.'
        }
        messages.value = [...messages.value, createSystemMessage(`${participant.user_name} ${action}`, payload.created_at)]
      }
      return
    }

    if (payload.type === 'room_close_scheduled') {
      const seconds = Number(payload.seconds)
      if (Number.isFinite(seconds) && seconds >= 0) {
        messages.value = [
          ...messages.value,
          createSystemMessage(`Комната закроется через ${seconds}с, если автор не переподключится.`)
        ]
      }
      return
    }

    if (payload.type === 'room_closed') {
      const closerName = payload.participant && payload.participant.user_name ? payload.participant.user_name : null
      statusText.value = closerName
        ? `Комната закрыта: ${closerName} вышел(ла).`
        : 'Комната закрыта: автор вышел.'
      currentRoom.value = null
      messages.value = []
      clearStoredActiveRoom()
      closeSocket()
      loadFreeRooms()
      return
    }

    if (payload.type === 'error') {
      errorText.value = payload.message || 'Ошибка сервера'
    }
  }

  ws.onerror = () => {
    errorText.value = 'Ошибка WebSocket'
  }

  ws.onclose = () => {
    if (socket === ws) {
      socket = null
    }
    if (!receivedRoomState) {
      if (currentRoom.value && currentRoom.value.room_id === roomId) {
        currentRoom.value = null
        messages.value = []
      }
      clearStoredActiveRoom()
      statusText.value = 'Не подключено'
      if (!errorText.value) {
        errorText.value = 'Подключение к комнате отклонено.'
      }
      loadFreeRooms()
      return
    }
    if (currentRoom.value) {
      statusText.value = 'Отключено'
    }
  }
}

async function createRoom() {
  if (!canCreateRoom.value) {
    return
  }

  creatingRoom.value = true
  errorText.value = ''
  persistUserName()

  try {
    const room = await apiRequest('/rooms', {
      method: 'POST',
      body: JSON.stringify({
        user_id: userId.value,
        user_name: userName.value.trim(),
        topic: createTopic.value.trim()
      })
    })

    const sessionToken = requireSessionToken(room)
    createTopic.value = ''
    messages.value = []
    applyRoomState(room, sessionToken)
    connectToRoom(room.room_id, sessionToken)
    await loadFreeRooms()
  } catch (error) {
    errorText.value = error instanceof Error ? error.message : 'Не удалось создать комнату'
  } finally {
    creatingRoom.value = false
  }
}

async function joinRoom(roomId, authorId = null) {
  const targetRoomId = roomId.trim()

  if (authorId && authorId === userId.value) {
    errorText.value = 'Нельзя подключиться к собственной комнате.'
    return
  }
  if (userName.value.trim() === '') {
    errorText.value = 'Введите имя перед подключением к комнате.'
    return
  }
  if (targetRoomId === '') {
    errorText.value = 'Нужно указать ID комнаты.'
    return
  }

  joiningRoom.value = true
  errorText.value = ''
  persistUserName()

  try {
    const room = await apiRequest(`/rooms/${encodeURIComponent(targetRoomId)}/join`, {
      method: 'POST',
      body: JSON.stringify({
        user_id: userId.value,
        user_name: userName.value.trim()
      })
    })

    const sessionToken = requireSessionToken(room)
    joinRoomId.value = ''
    messages.value = []
    applyRoomState(room, sessionToken)
    connectToRoom(room.room_id, sessionToken)
    await loadFreeRooms()
  } catch (error) {
    errorText.value = error instanceof Error ? error.message : 'Не удалось подключиться к комнате'
  } finally {
    joiningRoom.value = false
  }
}

async function leaveRoom() {
  if (!currentRoom.value) {
    return
  }

  const roomId = currentRoom.value.room_id
  const sessionToken = activeRoomSessionToken.value
  currentRoom.value = null
  messages.value = []
  clearStoredActiveRoom()
  statusText.value = 'Выход из комнаты...'

  closeSocket()

  try {
    await apiRequest(`/rooms/${encodeURIComponent(roomId)}/leave`, {
      method: 'POST',
      body: JSON.stringify({ session_token: sessionToken })
    })
  } catch {
    // websocket disconnect already handles the lifecycle; endpoint is best-effort
  } finally {
    statusText.value = 'Не подключено'
    await loadFreeRooms()
  }
}

function sendMessage() {
  if (!socket || socket.readyState !== WebSocket.OPEN) {
    return
  }

  const text = messageDraft.value.trim()
  if (!text) {
    return
  }

  socket.send(JSON.stringify({ type: 'chat_message', text }))
  messageDraft.value = ''
}

function appendEmoji(emoji) {
  if (!emoji) {
    return
  }

  const draft = messageDraft.value
  const needsSpace = draft.trim() !== '' && !/\s$/.test(draft)
  messageDraft.value = needsSpace ? `${draft} ${emoji}` : `${draft}${emoji}`

  nextTick(() => {
    if (composerInputRef.value && typeof composerInputRef.value.focus === 'function') {
      composerInputRef.value.focus()
    }
  })
}

async function restoreRoomAfterReload() {
  const storedRoomId = getStoredActiveRoomId().trim()
  const storedSessionToken = getStoredActiveRoomSessionToken().trim()
  if (!storedRoomId || !storedSessionToken) {
    clearStoredActiveRoom()
    await loadFreeRooms()
    return
  }

  statusText.value = 'Восстановление комнаты...'
  errorText.value = ''

  try {
    activeRoomSessionToken.value = storedSessionToken
    connectToRoom(storedRoomId, storedSessionToken)
    await loadFreeRooms()
  } catch {
    clearStoredActiveRoom()
    statusText.value = 'Не подключено'
    await loadFreeRooms()
  }
}

onMounted(() => {
  shouldKeepLobbyConnection = true
  connectLobbySocket()
  restoreRoomAfterReload()
})

watch(
  () => messages.value.length,
  () => {
    nextTick(() => {
      scrollMessagesToBottom()
    })
  }
)

watch(
  () => (currentRoom.value ? currentRoom.value.room_id : ''),
  () => {
    nextTick(() => {
      scrollMessagesToBottom()
    })
  }
)

onBeforeUnmount(() => {
  shouldKeepLobbyConnection = false
  clearLobbyReconnectTimer()
  closeLobbySocket()
  closeSocket()
})
</script>

<template>
  <main
    class="app-shell"
    :class="{ 'app-shell-chat': isInRoom }"
  >
    <div
      class="bg-wash bg-wash-left"
      aria-hidden="true"
    />
    <div
      class="bg-wash bg-wash-right"
      aria-hidden="true"
    />

    <header class="page-head">
      <p class="kicker">
        Список комнат
      </p>
      <h1>
        Онлайн-чат
      </h1>
      <p class="subtitle">
        Создавайте комнаты с темами, подключайтесь к свободным и общайтесь в реальном времени.
      </p>
    </header>

    <section
      v-if="!isInRoom"
      class="lobby-layout"
    >
      <aside class="card control-panel">
        <h2>
          Создать комнату
        </h2>
        <p class="panel-copy">
          Укажите имя, задайте тему и начните диалог.
        </p>

        <label
          class="label"
          for="user-name"
        >
          Ваше имя
        </label>
        <input
          id="user-name"
          v-model="userName"
          class="input"
          placeholder="Введите отображаемое имя"
        >

        <label
          class="label"
          for="room-topic"
        >
          Тема комнаты
        </label>
        <input
          id="room-topic"
          v-model="createTopic"
          class="input"
          placeholder="Например: Планы на выходные"
        >

        <button
          class="button button-primary"
          :disabled="!canCreateRoom"
          @click="createRoom"
        >
          {{ creatingRoom ? 'Создание...' : 'Создать комнату' }}
        </button>

        <div class="join-section">
          <h3>
            Подключиться к свободной
          </h3>
          <div class="join-row">
            <input
              v-model="joinRoomId"
              class="input"
              placeholder="ID комнаты"
            >
            <button
              class="button button-primary"
              :disabled="!canJoinById"
              @click="joinRoom(joinRoomId)"
            >
              {{ joiningRoom ? 'Подключение...' : 'Войти по ID' }}
            </button>
          </div>
        </div>

        <div class="status-line">
          <p class="chip chip-status">
            <span class="status-dot" />
            {{ statusText }}
          </p>
          <p class="chip">
            id: {{ userId.slice(0, 8) }}
          </p>
          <button
            class="button button-soft"
            :disabled="loadingRooms"
            @click="loadFreeRooms"
          >
            Обновить
          </button>
        </div>

        <p
          v-if="errorText"
          class="error"
        >
          {{ errorText }}
        </p>
      </aside>

      <section class="card rooms-board">
        <div class="rooms-head">
          <h2>
            Доступные комнаты
          </h2>
          <p class="meta">
            {{ sortedFreeRooms.length }} открыто
          </p>
        </div>

        <ul
          v-if="sortedFreeRooms.length > 0"
          class="rooms-grid"
        >
          <li
            v-for="room in sortedFreeRooms"
            :key="room.room_id"
            class="room-row room-tile"
          >
            <div>
              <p class="tile-topic">
                {{ room.topic }}
              </p>
              <p class="tile-meta">
                Автор: {{ room.author.user_name }}
              </p>
              <p class="tile-meta tile-time">
                {{ formatRoomCreatedAt(room.created_at) }}
              </p>
            </div>
            <button
              class="button button-join"
              :disabled="!canJoinFromList || isRoomOwnedByCurrentUser(room)"
              @click="joinRoom(room.room_id, room.author.user_id)"
            >
              {{ isRoomOwnedByCurrentUser(room) ? 'Ваша комната' : 'Войти' }}
            </button>
          </li>
        </ul>

        <p
          v-else
          class="empty"
        >
          Сейчас нет свободных комнат.
        </p>
      </section>
    </section>

    <section
      v-else
      class="card chat-workspace"
    >
      <div class="chat-head">
        <div>
          <p class="kicker room-kicker">
            Активная комната
          </p>
          <h2>
            {{ currentRoom.topic }}
          </h2>
          <p class="meta">
            Автор: {{ currentRoom.author.user_name }} | Собеседник: {{ currentRoom.guest ? currentRoom.guest.user_name : 'ожидание...' }}
          </p>
        </div>

        <div class="chat-meta-actions">
          <p class="chip chip-room-id">
            ID комнаты: {{ currentRoom.room_id }}
          </p>
          <button
            class="button button-danger"
            @click="leaveRoom"
          >
            Выйти
          </button>
        </div>
      </div>

      <div
        ref="messagesPanelRef"
        class="messages-panel"
      >
        <p
          v-if="messages.length === 0"
          class="meta empty"
        >
          Сообщений пока нет. Напишите первым.
        </p>

        <article
          v-for="message in messages"
          :key="`${message.created_at}-${message.sender_id}-${message.text}`"
          class="msg"
          :class="messageClass(message)"
        >
          <p class="msg-meta">
            <span class="msg-author">{{ messageAuthorLabel(message) }}</span>
            <span v-if="formatMessageTime(message.created_at)">{{ formatMessageTime(message.created_at) }}</span>
          </p>
          <p class="msg-text">
            <template
              v-for="(line, lineIndex) in formatMessageSegments(message.text)"
              :key="`line-${lineIndex}`"
            >
              <template
                v-for="(segment, segmentIndex) in line.segments"
                :key="`segment-${lineIndex}-${segmentIndex}`"
              >
                <strong v-if="segment.type === 'bold'">{{ segment.text }}</strong>
                <em v-else-if="segment.type === 'italic'">{{ segment.text }}</em>
                <code v-else-if="segment.type === 'code'">{{ segment.text }}</code>
                <s v-else-if="segment.type === 'strike'">{{ segment.text }}</s>
                <template v-else>
                  {{ segment.text }}
                </template>
              </template>
              <br v-if="!line.isLast">
            </template>
          </p>
        </article>
      </div>

      <div class="composer">
        <div class="composer-stack">
          <div class="emoji-strip">
            <button
              v-for="emoji in QUICK_EMOJIS"
              :key="emoji"
              type="button"
              class="emoji-button"
              :data-emoji="emoji"
              @click="appendEmoji(emoji)"
            >
              {{ emoji }}
            </button>
          </div>
          <input
            ref="composerInputRef"
            v-model="messageDraft"
            class="input composer-input"
            placeholder="Введите сообщение"
            @keydown.enter.prevent="sendMessage"
          >
          <p class="composer-help">
            Формат: **жирный**, *курсив*, `код`, ~~зачеркнутый~~
          </p>
        </div>
        <button
          class="button button-primary"
          @click="sendMessage"
        >
          Отправить
        </button>
      </div>
    </section>
  </main>
</template>

<style scoped>
@import url('https://fonts.googleapis.com/css2?family=Bricolage+Grotesque:wght@500;700;800&family=Manrope:wght@500;700&display=swap');

.app-shell {
  --ink: #171e2f;
  --ink-muted: #596276;
  --line: #1b2233;
  --paper: #fdf8f1;
  --paper-soft: #fffdf9;
  --cyan: #30b8c9;
  --orange: #f08c4b;
  --sand: #f6dfb3;
  --red: #d4545d;
  --mint: #9dd9c8;
  min-height: 100vh;
  padding: 1.2rem clamp(0.9rem, 2.5vw, 2rem) 1.8rem;
  box-sizing: border-box;
  position: relative;
  overflow: hidden;
  color: var(--ink);
  font-family: 'Manrope', 'Segoe UI', sans-serif;
  background:
    radial-gradient(circle at 16% 8%, rgb(157 217 200 / 42%), transparent 30%),
    radial-gradient(circle at 88% 12%, rgb(240 140 75 / 28%), transparent 32%),
    repeating-linear-gradient(
      120deg,
      rgb(255 255 255 / 50%) 0 14px,
      rgb(245 236 219 / 55%) 14px 28px
    ),
    #f3eadc;
}

.app-shell-chat {
  height: 100vh;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.bg-wash {
  position: absolute;
  border-radius: 999px;
  filter: blur(14px);
  pointer-events: none;
  z-index: 0;
}

.bg-wash-left {
  width: 340px;
  height: 340px;
  left: -150px;
  top: -130px;
  background: rgb(48 184 201 / 26%);
}

.bg-wash-right {
  width: 360px;
  height: 360px;
  right: -140px;
  bottom: -140px;
  background: rgb(240 140 75 / 28%);
}

.page-head,
.lobby-layout,
.chat-workspace {
  position: relative;
  z-index: 1;
}

.page-head {
  margin-bottom: 1rem;
  border: 2px solid var(--line);
  background: linear-gradient(135deg, #fff4dc, #fffdf6 42%, #e3f6f9 100%);
  border-radius: 18px;
  padding: 0.85rem 0.95rem;
  box-shadow: 8px 8px 0 #1b22331f;
}

.kicker {
  margin: 0;
  font-size: 0.7rem;
  font-weight: 800;
  text-transform: uppercase;
  letter-spacing: 0.16em;
  color: #00798b;
}

.page-head h1 {
  margin: 0.2rem 0 0.26rem;
  font-family: 'Bricolage Grotesque', 'Segoe UI', sans-serif;
  font-size: clamp(2rem, 3.6vw, 3rem);
  line-height: 1.02;
  letter-spacing: 0.01em;
}

.subtitle {
  margin: 0;
  color: var(--ink-muted);
  max-width: 62ch;
  font-weight: 700;
}

.card {
  border: 2px solid var(--line);
  border-radius: 18px;
  background: linear-gradient(165deg, var(--paper), var(--paper-soft));
  box-shadow: 8px 8px 0 #1b223321;
}

.lobby-layout {
  display: grid;
  grid-template-columns: minmax(290px, 360px) minmax(0, 1fr);
  gap: 1rem;
  align-items: start;
}

.control-panel {
  padding: 1rem;
  display: grid;
  gap: 0.45rem;
}

.control-panel h2,
.rooms-head h2,
.chat-head h2 {
  margin: 0;
  font-family: 'Bricolage Grotesque', 'Segoe UI', sans-serif;
}

.panel-copy {
  margin: 0 0 0.25rem;
  color: var(--ink-muted);
  font-size: 0.92rem;
}

.label {
  display: block;
  margin-top: 0.25rem;
  margin-bottom: 0.3rem;
  font-size: 0.84rem;
  font-weight: 800;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: #293046;
}

.input {
  width: 100%;
  box-sizing: border-box;
  border: 2px solid #2a3148;
  border-radius: 10px;
  background: #fffef9;
  color: var(--ink);
  font-size: 0.95rem;
  font-weight: 600;
  padding: 0.66rem 0.76rem;
  margin: 0;
  transition: transform 120ms ease, box-shadow 120ms ease;
}

.input:focus {
  outline: none;
  transform: translateY(-1px);
  box-shadow: 0 0 0 4px rgb(48 184 201 / 22%);
}

.join-section {
  margin-top: 0.45rem;
  display: grid;
  gap: 0.4rem;
}

.join-section h3 {
  margin: 0.08rem 0 0;
  font-family: 'Bricolage Grotesque', 'Segoe UI', sans-serif;
  font-size: 0.98rem;
}

.join-row {
  display: grid;
  grid-template-columns: 1fr auto;
  gap: 0.55rem;
}

.status-line {
  margin-top: 0.74rem;
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.45rem;
}

.chip {
  margin: 0;
  display: inline-flex;
  align-items: center;
  gap: 0.35rem;
  border-radius: 999px;
  border: 2px solid #2a3148;
  padding: 0.24rem 0.58rem;
  background: #fff4df;
  color: #37415a;
  font-size: 0.8rem;
  font-weight: 700;
}

.status-dot {
  width: 8px;
  height: 8px;
  border-radius: 999px;
  background: #2cab71;
  box-shadow: 0 0 0 3px rgb(44 171 113 / 19%);
}

.error {
  margin: 0.25rem 0 0;
  color: #a22f44;
  font-size: 0.9rem;
  font-weight: 700;
}

.button {
  border: 2px solid #1b2233;
  border-radius: 10px;
  padding: 0.62rem 0.96rem;
  font-size: 0.9rem;
  font-weight: 800;
  text-transform: uppercase;
  letter-spacing: 0.03em;
  cursor: pointer;
  transition: transform 120ms ease, box-shadow 120ms ease, filter 120ms ease;
}

.button:hover:not(:disabled) {
  transform: translate(-1px, -1px);
  box-shadow: 4px 4px 0 #1b22332a;
}

.button:disabled {
  opacity: 0.55;
  cursor: not-allowed;
  box-shadow: none;
}

.button-primary {
  background: linear-gradient(135deg, var(--cyan), #0f98a9);
  color: #f8feff;
}

.button-soft {
  margin-left: auto;
  background: #ffe5bb;
  color: #3c2c1b;
}

.button-join {
  border-radius: 999px;
  background: linear-gradient(135deg, var(--sand), var(--orange));
  color: #332216;
}

.button-danger {
  background: linear-gradient(135deg, var(--red), #bb3f4c);
  color: #fff6f7;
}

.meta {
  margin: 0;
  color: var(--ink-muted);
  font-size: 0.86rem;
  font-weight: 700;
}

.rooms-board {
  padding: 0.95rem;
}

.rooms-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 0.6rem;
  margin-bottom: 0.74rem;
}

.rooms-grid {
  list-style: none;
  margin: 0;
  padding: 0;
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
  gap: 0.72rem;
}

.room-tile {
  border: 2px solid #1e2537;
  border-radius: 14px;
  background: linear-gradient(145deg, #fff8ed, #fffdf8);
  padding: 0.78rem;
  display: grid;
  gap: 0.72rem;
  animation: tile-rise 320ms ease both;
}

.room-tile:nth-child(odd) {
  transform: rotate(-0.4deg);
}

.room-tile:nth-child(even) {
  transform: rotate(0.4deg);
}

.tile-topic {
  margin: 0;
  font-family: 'Bricolage Grotesque', 'Segoe UI', sans-serif;
  font-size: 1.02rem;
  line-height: 1.2;
  color: #192039;
}

.tile-meta {
  margin: 0.2rem 0 0;
  font-size: 0.8rem;
  color: #5d677f;
}

.tile-time {
  font-style: italic;
  color: #6c5364;
}

.empty {
  padding: 0.78rem;
  border: 2px dashed #2a3148;
  border-radius: 12px;
  background: #fff6e8;
  text-align: center;
  font-weight: 700;
}

.chat-workspace {
  padding: 1rem;
  display: grid;
  grid-template-rows: auto minmax(0, 1fr) auto;
  gap: 0.86rem;
  min-height: 0;
  flex: 1 1 auto;
}

.chat-head {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 0.9rem;
}

.room-kicker {
  color: #0b8093;
}

.chat-meta-actions {
  display: flex;
  align-items: center;
  gap: 0.5rem;
}

.chip-room-id {
  max-width: min(62vw, 520px);
  overflow-wrap: anywhere;
  user-select: text;
}

.messages-panel {
  min-height: 0;
  max-height: none;
  overflow-y: auto;
  border-radius: 14px;
  border: 2px solid #222a3d;
  background:
    radial-gradient(circle at 100% 0%, rgb(240 140 75 / 22%), transparent 35%),
    radial-gradient(circle at 0% 0%, rgb(48 184 201 / 18%), transparent 38%),
    #fffef9;
  padding: 0.76rem;
  display: flex;
  flex-direction: column;
  gap: 0.52rem;
}

.msg {
  max-width: min(82%, 560px);
  border-radius: 12px;
  border: 2px solid transparent;
  padding: 0.54rem 0.68rem;
  animation: msg-in 150ms ease;
}

.msg-peer {
  align-self: flex-start;
  background: #fff7ee;
  border-color: #dbb57b;
}

.msg-own {
  align-self: flex-end;
  background: linear-gradient(135deg, #1db2c4, #1491a2);
  border-color: #0f7989;
  color: #f4feff;
}

.msg-system {
  align-self: center;
  max-width: 94%;
  background: #f0f2f6;
  border-color: #c2c8d5;
  color: #515a71;
}

.msg-meta {
  margin: 0 0 0.18rem;
  display: flex;
  justify-content: space-between;
  gap: 0.5rem;
  font-size: 0.76rem;
  opacity: 0.9;
}

.msg-author {
  font-weight: 800;
}

.msg-text {
  margin: 0;
  line-height: 1.4;
  word-break: break-word;
}

.composer {
  display: grid;
  grid-template-columns: 1fr auto;
  gap: 0.56rem;
  align-items: end;
}

.composer-stack {
  display: grid;
  gap: 0.45rem;
}

.emoji-strip {
  display: flex;
  flex-wrap: wrap;
  gap: 0.35rem;
}

.emoji-button {
  border: 2px solid #2a3148;
  border-radius: 999px;
  background: #fff7e9;
  color: #1e2539;
  font-size: 1rem;
  line-height: 1;
  padding: 0.3rem 0.5rem;
  cursor: pointer;
  transition: transform 120ms ease, box-shadow 120ms ease;
}

.emoji-button:hover {
  transform: translateY(-1px);
  box-shadow: 0 2px 0 #1b223330;
}

.emoji-button:focus-visible {
  outline: none;
  box-shadow: 0 0 0 4px rgb(48 184 201 / 24%);
}

.composer-input {
  border-color: #252d42;
}

.composer-help {
  margin: 0;
  font-size: 0.78rem;
  color: #5c677f;
  font-weight: 700;
}

.msg-text strong {
  font-weight: 800;
}

.msg-text em {
  font-style: italic;
}

.msg-text code {
  font-family: 'Courier New', 'Consolas', monospace;
  font-size: 0.86em;
  border-radius: 6px;
  padding: 0.08rem 0.34rem;
  border: 1px solid rgb(27 34 51 / 30%);
  background: rgb(255 255 255 / 48%);
}

@keyframes msg-in {
  from {
    opacity: 0;
    transform: translateY(3px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

@keyframes tile-rise {
  from {
    opacity: 0;
    transform: translateY(8px) scale(0.98);
  }
  to {
    opacity: 1;
    transform: translateY(0) scale(1);
  }
}

@media (max-width: 980px) {
  .lobby-layout {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 760px) {
  .page-head h1 {
    font-size: 2rem;
  }

  .join-row,
  .composer {
    grid-template-columns: 1fr;
  }

  .button-soft {
    margin-left: 0;
  }

  .chat-head {
    flex-direction: column;
    align-items: stretch;
  }

  .chat-meta-actions {
    justify-content: space-between;
  }

  .app-shell-chat {
    height: 100dvh;
  }

  .msg {
    max-width: 95%;
  }
}
</style>
