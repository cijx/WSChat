import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest'
import App from './App.vue'

class MockWebSocket {
  static CONNECTING = 0
  static OPEN = 1
  static CLOSED = 3
  static instances = []

  constructor(url) {
    this.url = url
    this.readyState = MockWebSocket.CONNECTING
    this.onopen = null
    this.onclose = null
    this.onerror = null
    this.onmessage = null
    this.sent = []
    MockWebSocket.instances.push(this)
  }

  send(data) {
    this.sent.push(data)
  }

  close() {
    this.readyState = MockWebSocket.CLOSED
    if (this.onclose) {
      this.onclose({})
    }
  }

  emitOpen() {
    this.readyState = MockWebSocket.OPEN
    if (this.onopen) {
      this.onopen({})
    }
  }

  emitMessage(payload) {
    if (this.onmessage) {
      this.onmessage({ data: JSON.stringify(payload) })
    }
  }
}

function createStorageMock() {
  const data = new Map()
  return {
    getItem(key) {
      return data.has(key) ? data.get(key) : null
    },
    setItem(key, value) {
      data.set(key, String(value))
    },
    removeItem(key) {
      data.delete(key)
    },
    clear() {
      data.clear()
    }
  }
}

function jsonResponse(payload) {
  return {
    ok: true,
    json: async () => payload
  }
}

describe('App', () => {
  beforeEach(() => {
    const localStorageMock = createStorageMock()
    const sessionStorageMock = createStorageMock()

    vi.stubGlobal('localStorage', localStorageMock)
    vi.stubGlobal('sessionStorage', sessionStorageMock)

    if (typeof window !== 'undefined') {
      Object.defineProperty(window, 'localStorage', { value: localStorageMock, configurable: true })
      Object.defineProperty(window, 'sessionStorage', { value: sessionStorageMock, configurable: true })
    }

    MockWebSocket.instances = []

    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => []
      })
    )
    vi.stubGlobal('WebSocket', MockWebSocket)
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    vi.clearAllMocks()
  })

  test('renders create and join actions', () => {
    const wrapper = mount(App)

    expect(wrapper.text()).toContain('Создать комнату')
    expect(wrapper.text()).toContain('Подключиться к свободной')
  })

  test('renders free rooms as cards in lobby board', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        jsonResponse([
          {
            room_id: 'room-1',
            topic: 'Sky Room',
            created_at: '2026-02-07T00:00:00+00:00',
            author: { user_id: 'author-1', user_name: 'Alice' },
            guest: null,
            is_free: true
          }
        ])
      )

    vi.stubGlobal('fetch', fetchMock)

    const wrapper = mount(App)
    await flushPromises()

    expect(wrapper.find('.rooms-board').exists()).toBe(true)
    expect(wrapper.findAll('.room-tile').length).toBe(1)
  })

  test('joins free room and opens websocket', async () => {
    localStorage.setItem('chat.user_id', 'guest-1')

    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        jsonResponse([
          {
            room_id: 'room-1',
            topic: 'Topic',
            created_at: '2026-02-07T00:00:00+00:00',
            author: { user_id: 'author-1', user_name: 'Alice' },
            guest: null,
            is_free: true
          }
        ])
      )
      .mockResolvedValueOnce(
        jsonResponse({
          room_id: 'room-1',
          topic: 'Topic',
          created_at: '2026-02-07T00:00:00+00:00',
          author: { user_id: 'author-1', user_name: 'Alice' },
          guest: { user_id: 'guest-1', user_name: 'Bob' },
          is_free: false
        })
      )
      .mockResolvedValueOnce(jsonResponse([]))

    vi.stubGlobal('fetch', fetchMock)

    const wrapper = mount(App)
    await flushPromises()

    await wrapper.get('#user-name').setValue('Bob')
    await wrapper.get('.room-row .button').trigger('click')
    await flushPromises()

    const joinCall = fetchMock.mock.calls.find(([url]) => url.includes('/rooms/room-1/join'))
    expect(joinCall).toBeTruthy()
    expect(joinCall[1].method).toBe('POST')
    expect(JSON.parse(joinCall[1].body)).toEqual({ user_id: 'guest-1', user_name: 'Bob' })

    const lobbySocket = MockWebSocket.instances.find((ws) => ws.url.includes('/ws/lobby'))
    const roomSocket = MockWebSocket.instances.find((ws) => ws.url.includes('/ws/rooms/room-1?user_id=guest-1'))

    expect(lobbySocket).toBeTruthy()
    expect(roomSocket).toBeTruthy()
    expect(wrapper.text()).toContain('Выйти')
  })

  test('renders participant system event in room chat', async () => {
    localStorage.setItem('chat.user_id', 'guest-1')

    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        jsonResponse([
          {
            room_id: 'room-1',
            topic: 'Topic',
            created_at: '2026-02-07T00:00:00+00:00',
            author: { user_id: 'author-1', user_name: 'Alice' },
            guest: null,
            is_free: true
          }
        ])
      )
      .mockResolvedValueOnce(
        jsonResponse({
          room_id: 'room-1',
          topic: 'Topic',
          created_at: '2026-02-07T00:00:00+00:00',
          author: { user_id: 'author-1', user_name: 'Alice' },
          guest: { user_id: 'guest-1', user_name: 'Bob' },
          is_free: false
        })
      )
      .mockResolvedValueOnce(jsonResponse([]))

    vi.stubGlobal('fetch', fetchMock)

    const wrapper = mount(App)
    await flushPromises()

    await wrapper.get('#user-name').setValue('Bob')
    await wrapper.get('.room-row .button').trigger('click')
    await flushPromises()

    const roomSocket = MockWebSocket.instances.find((ws) => ws.url.includes('/ws/rooms/room-1?user_id=guest-1'))
    expect(roomSocket).toBeTruthy()

    roomSocket.emitMessage({
      type: 'participant_event',
      event: 'joined',
      participant: { user_id: 'guest-2', user_name: 'Charlie', role: 'guest' },
      created_at: '2026-02-07T00:01:00+00:00'
    })
    await flushPromises()

    expect(wrapper.text()).toContain('Charlie вошел(а) в комнату.')
  })

  test('renders participant disconnected system event in room chat', async () => {
    localStorage.setItem('chat.user_id', 'guest-1')

    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        jsonResponse([
          {
            room_id: 'room-1',
            topic: 'Topic',
            created_at: '2026-02-07T00:00:00+00:00',
            author: { user_id: 'author-1', user_name: 'Alice' },
            guest: null,
            is_free: true
          }
        ])
      )
      .mockResolvedValueOnce(
        jsonResponse({
          room_id: 'room-1',
          topic: 'Topic',
          created_at: '2026-02-07T00:00:00+00:00',
          author: { user_id: 'author-1', user_name: 'Alice' },
          guest: { user_id: 'guest-1', user_name: 'Bob' },
          is_free: false
        })
      )
      .mockResolvedValueOnce(jsonResponse([]))

    vi.stubGlobal('fetch', fetchMock)

    const wrapper = mount(App)
    await flushPromises()

    await wrapper.get('#user-name').setValue('Bob')
    await wrapper.get('.room-row .button').trigger('click')
    await flushPromises()

    const roomSocket = MockWebSocket.instances.find((ws) => ws.url.includes('/ws/rooms/room-1?user_id=guest-1'))
    expect(roomSocket).toBeTruthy()

    roomSocket.emitMessage({
      type: 'participant_event',
      event: 'disconnected',
      participant: { user_id: 'guest-2', user_name: 'Charlie', role: 'guest' },
      created_at: '2026-02-07T00:02:00+00:00'
    })
    await flushPromises()

    expect(wrapper.text()).toContain('Charlie потерял(а) соединение.')
  })

  test('renders safe formatted message content', async () => {
    localStorage.setItem('chat.user_id', 'guest-1')

    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        jsonResponse([
          {
            room_id: 'room-1',
            topic: 'Topic',
            created_at: '2026-02-07T00:00:00+00:00',
            author: { user_id: 'author-1', user_name: 'Alice' },
            guest: null,
            is_free: true
          }
        ])
      )
      .mockResolvedValueOnce(
        jsonResponse({
          room_id: 'room-1',
          topic: 'Topic',
          created_at: '2026-02-07T00:00:00+00:00',
          author: { user_id: 'author-1', user_name: 'Alice' },
          guest: { user_id: 'guest-1', user_name: 'Bob' },
          is_free: false
        })
      )
      .mockResolvedValueOnce(jsonResponse([]))

    vi.stubGlobal('fetch', fetchMock)

    const wrapper = mount(App)
    await flushPromises()

    await wrapper.get('#user-name').setValue('Bob')
    await wrapper.get('.room-row .button').trigger('click')
    await flushPromises()

    const roomSocket = MockWebSocket.instances.find((ws) => ws.url.includes('/ws/rooms/room-1?user_id=guest-1'))
    expect(roomSocket).toBeTruthy()

    roomSocket.emitMessage({
      type: 'chat_message',
      message: {
        room_id: 'room-1',
        sender_id: 'author-1',
        sender_name: 'Alice',
        text: '**жирный** *курсив* `код`\n~~зачеркнутый~~ <img src=x onerror=alert(1)>',
        created_at: '2026-02-07T00:01:00+00:00'
      }
    })
    await flushPromises()

    const msg = wrapper.get('.msg-text')
    expect(msg.find('strong').text()).toBe('жирный')
    expect(msg.find('em').text()).toBe('курсив')
    expect(msg.find('code').text()).toBe('код')
    expect(msg.find('s').text()).toBe('зачеркнутый')
    expect(msg.find('br').exists()).toBe(true)
    expect(msg.find('img').exists()).toBe(false)
    expect(msg.text()).toContain('<img src=x onerror=alert(1)>')
  })

  test('keeps messages panel scrollable and auto-scrolls on new messages', async () => {
    localStorage.setItem('chat.user_id', 'guest-1')

    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        jsonResponse([
          {
            room_id: 'room-1',
            topic: 'Topic',
            created_at: '2026-02-07T00:00:00+00:00',
            author: { user_id: 'author-1', user_name: 'Alice' },
            guest: null,
            is_free: true
          }
        ])
      )
      .mockResolvedValueOnce(
        jsonResponse({
          room_id: 'room-1',
          topic: 'Topic',
          created_at: '2026-02-07T00:00:00+00:00',
          author: { user_id: 'author-1', user_name: 'Alice' },
          guest: { user_id: 'guest-1', user_name: 'Bob' },
          is_free: false
        })
      )
      .mockResolvedValueOnce(jsonResponse([]))

    vi.stubGlobal('fetch', fetchMock)

    const wrapper = mount(App)
    await flushPromises()

    await wrapper.get('#user-name').setValue('Bob')
    await wrapper.get('.room-row .button').trigger('click')
    await flushPromises()

    const panel = wrapper.get('.messages-panel').element
    Object.defineProperty(panel, 'scrollHeight', {
      configurable: true,
      value: 640
    })
    panel.scrollTop = 0

    const roomSocket = MockWebSocket.instances.find((ws) => ws.url.includes('/ws/rooms/room-1?user_id=guest-1'))
    expect(roomSocket).toBeTruthy()

    roomSocket.emitMessage({
      type: 'chat_message',
      message: {
        room_id: 'room-1',
        sender_id: 'author-1',
        sender_name: 'Alice',
        text: 'hello',
        created_at: '2026-02-07T00:01:00+00:00'
      }
    })
    await flushPromises()

    expect(panel.scrollTop).toBe(640)
  })

  test('disables room list join button when name is empty', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        jsonResponse([
          {
            room_id: 'room-1',
            topic: 'Topic',
            created_at: '2026-02-07T00:00:00+00:00',
            author: { user_id: 'author-1', user_name: 'Alice' },
            guest: null,
            is_free: true
          }
        ])
      )

    vi.stubGlobal('fetch', fetchMock)

    const wrapper = mount(App)
    await flushPromises()

    const joinButton = wrapper.get('.room-row .button')
    expect(joinButton.attributes('disabled')).toBeDefined()

    await joinButton.trigger('click')
    await flushPromises()

    expect(fetchMock.mock.calls.some(([url]) => url.includes('/join'))).toBe(false)
  })

  test('does not allow joining own room from room list', async () => {
    localStorage.setItem('chat.user_id', 'author-1')

    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        jsonResponse([
          {
            room_id: 'room-1',
            topic: 'Topic',
            created_at: '2026-02-07T00:00:00+00:00',
            author: { user_id: 'author-1', user_name: 'Alice' },
            guest: null,
            is_free: true
          }
        ])
      )

    vi.stubGlobal('fetch', fetchMock)

    const wrapper = mount(App)
    await flushPromises()
    await wrapper.get('#user-name').setValue('Alice')

    const joinButton = wrapper.get('.room-row .button')
    expect(joinButton.text()).toContain('Ваша комната')
    expect(joinButton.attributes('disabled')).toBeDefined()
  })

  test('restores active room after page reload', async () => {
    localStorage.setItem('chat.user_id', 'guest-1')
    localStorage.setItem('chat.active_room_id', 'room-1')

    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        jsonResponse({
          room_id: 'room-1',
          topic: 'Topic',
          created_at: '2026-02-07T00:00:00+00:00',
          author: { user_id: 'author-1', user_name: 'Alice' },
          guest: { user_id: 'guest-1', user_name: 'Bob' },
          is_free: false
        })
      )
      .mockResolvedValueOnce(jsonResponse([]))

    vi.stubGlobal('fetch', fetchMock)

    const wrapper = mount(App)
    await flushPromises()

    expect(fetchMock.mock.calls[0][0]).toContain('/rooms/room-1')
    const lobbySocket = MockWebSocket.instances.find((ws) => ws.url.includes('/ws/lobby'))
    const roomSocket = MockWebSocket.instances.find((ws) => ws.url.includes('/ws/rooms/room-1?user_id=guest-1'))
    expect(lobbySocket).toBeTruthy()
    expect(roomSocket).toBeTruthy()
    expect(wrapper.text()).toContain('Выйти')
  })

  test('shows full room id inside active room header', async () => {
    localStorage.setItem('chat.user_id', 'guest-1')
    const fullRoomId = 'f711b17f-a5db-4f95-a9a3-0f6d6ff70b2c'

    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        jsonResponse({
          room_id: fullRoomId,
          topic: 'Topic',
          created_at: '2026-02-07T00:00:00+00:00',
          author: { user_id: 'author-1', user_name: 'Alice' },
          guest: { user_id: 'guest-1', user_name: 'Bob' },
          is_free: false
        })
      )
      .mockResolvedValueOnce(jsonResponse([]))

    localStorage.setItem('chat.active_room_id', fullRoomId)
    vi.stubGlobal('fetch', fetchMock)

    const wrapper = mount(App)
    await flushPromises()

    const roomIdChip = wrapper.get('.chip-room-id')
    expect(roomIdChip.text()).toContain(fullRoomId)
  })

  test('appends emoji from toolbar into composer input', async () => {
    localStorage.setItem('chat.user_id', 'guest-1')
    localStorage.setItem('chat.active_room_id', 'room-1')

    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        jsonResponse({
          room_id: 'room-1',
          topic: 'Topic',
          created_at: '2026-02-07T00:00:00+00:00',
          author: { user_id: 'author-1', user_name: 'Alice' },
          guest: { user_id: 'guest-1', user_name: 'Bob' },
          is_free: false
        })
      )
      .mockResolvedValueOnce(jsonResponse([]))

    vi.stubGlobal('fetch', fetchMock)

    const wrapper = mount(App)
    await flushPromises()

    await wrapper.get('[data-emoji="😀"]').trigger('click')

    expect(wrapper.get('.composer-input').element.value).toBe('😀')
  })

  test('sends emoji-only message over websocket', async () => {
    localStorage.setItem('chat.user_id', 'guest-1')
    localStorage.setItem('chat.active_room_id', 'room-1')

    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        jsonResponse({
          room_id: 'room-1',
          topic: 'Topic',
          created_at: '2026-02-07T00:00:00+00:00',
          author: { user_id: 'author-1', user_name: 'Alice' },
          guest: { user_id: 'guest-1', user_name: 'Bob' },
          is_free: false
        })
      )
      .mockResolvedValueOnce(jsonResponse([]))

    vi.stubGlobal('fetch', fetchMock)

    const wrapper = mount(App)
    await flushPromises()

    const roomSocket = MockWebSocket.instances.find((ws) => ws.url.includes('/ws/rooms/room-1?user_id=guest-1'))
    expect(roomSocket).toBeTruthy()
    roomSocket.emitOpen()

    await wrapper.get('[data-emoji="🎉"]').trigger('click')
    await wrapper.get('.composer .button.button-primary').trigger('click')

    expect(roomSocket.sent.length).toBe(1)
    expect(JSON.parse(roomSocket.sent[0])).toEqual({
      type: 'chat_message',
      text: '🎉'
    })
    expect(wrapper.get('.composer-input').element.value).toBe('')
  })

  test('refreshes free rooms on lobby websocket catalog update', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        jsonResponse([
          {
            room_id: 'room-1',
            topic: 'First',
            created_at: '2026-02-07T00:00:00+00:00',
            author: { user_id: 'author-1', user_name: 'Alice' },
            guest: null,
            is_free: true
          }
        ])
      )
      .mockResolvedValueOnce(
        jsonResponse([
          {
            room_id: 'room-2',
            topic: 'Second',
            created_at: '2026-02-07T00:01:00+00:00',
            author: { user_id: 'author-2', user_name: 'Bob' },
            guest: null,
            is_free: true
          }
        ])
      )

    vi.stubGlobal('fetch', fetchMock)

    mount(App)
    await flushPromises()

    const lobbySocket = MockWebSocket.instances.find((ws) => ws.url.includes('/ws/lobby'))
    expect(lobbySocket).toBeTruthy()

    lobbySocket.emitMessage({
      type: 'rooms_catalog_updated',
      reason: 'room_created',
      room_id: 'room-2'
    })
    await flushPromises()

    const freeRoomsCalls = fetchMock.mock.calls.filter(([url]) => url.includes('/rooms/free'))
    expect(freeRoomsCalls.length).toBe(2)
  })
})
