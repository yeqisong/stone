// WebSocket 连接管理
// 开发时直连后端端口（避免 Vite WS 代理不稳定）
const WS_URL = 'ws://localhost:8000/api/ws/dag'

let ws = null
let reconnectTimer = null
let listeners = []

export const wsState = { connected: false }

export function connectWebSocket() {
  if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) return

  ws = new WebSocket(WS_URL)

  ws.onopen = () => {
    wsState.connected = true
    notifyListeners({ type: 'connection', connected: true })
    if (reconnectTimer) { clearTimeout(reconnectTimer); reconnectTimer = null }
  }

  ws.onmessage = (event) => {
    try {
      const data = JSON.parse(event.data)
      // dev 模式下 Console 直接可见所有 WS 消息，无需 Network → WS 子标签
      if (data.type !== 'pong') console.log('[WS]', data.type, data.type === 'dag_status' ? 'has_running=' + data.has_running : '', data.type === 'dag_log' ? 'nodes=' + (data.nodes || []).length : '')
      notifyListeners(data)
    } catch (e) {}
  }

  ws.onclose = () => {
    wsState.connected = false
    notifyListeners({ type: 'connection', connected: false })
    scheduleReconnect()
  }

  ws.onerror = () => {
    wsState.connected = false
    notifyListeners({ type: 'connection', connected: false })
  }
}

function scheduleReconnect() {
  if (reconnectTimer) return
  reconnectTimer = setTimeout(() => {
    reconnectTimer = null
    connectWebSocket()
  }, 3000)
}

export function disconnectWebSocket() {
  if (reconnectTimer) { clearTimeout(reconnectTimer); reconnectTimer = null }
  if (ws) { ws.close(); ws = null }
  wsState.connected = false
}

export function addWsListener(fn) {
  listeners.push(fn)
  return () => { listeners = listeners.filter(l => l !== fn) }
}

function notifyListeners(data) {
  listeners.forEach(fn => fn(data))
}
