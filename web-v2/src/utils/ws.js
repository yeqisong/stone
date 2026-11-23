// WebSocket 连接管理 — 统一消息格式 v2.1 (迭代 6.1 + 安全加固)
//
// 消息格式规范:
//   { topic: "dag", task_id: "run-xxx", event: "status|log|progress",
//     timestamp: "2026-07-06T22:00:00", data: { ... } }
//
// 用 window.location.origin 自适应环境（本地=ws://127.0.0.1:3000, 生产=wss://s.pmlab.top）
const BASE_WS_URL = window.location.origin.replace('http', 'ws') + '/api/ws/dag'

function getWsUrl() {
  const token = localStorage.getItem('token')
  return token ? `${BASE_WS_URL}?token=${encodeURIComponent(token)}` : BASE_WS_URL
}

let ws = null
let reconnectTimer = null
let listeners = []
let reconnectAttempts = 0
let manualClose = false
const MAX_RECONNECT_ATTEMPTS = 20
const RECONNECT_BASE_MS = 1000
const RECONNECT_MAX_MS = 30000

export const wsState = { connected: false }

export function connectWebSocket(reset = true) {
  if (reset) {
    // 显式连接（登录/手动重连/切 token）必须重置退避，否则登出时
    // disconnect 把 attempts 拉到上限后，再登录将永远不再重连。
    manualClose = false
    reconnectAttempts = 0
    if (reconnectTimer) { clearTimeout(reconnectTimer); reconnectTimer = null }
  }
  if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) return
  // 丢弃已关闭/半关闭的旧连接，避免旧 onclose 再安排一次重连
  if (ws) {
    try { ws.onclose = null; ws.onerror = null; ws.close() } catch (_) {}
    ws = null
  }

  ws = new WebSocket(getWsUrl())

  ws.onopen = () => {
    wsState.connected = true
    reconnectAttempts = 0  // 重置退避计数器
    notifyListeners({ type: 'connection', connected: true })
    if (reconnectTimer) { clearTimeout(reconnectTimer); reconnectTimer = null }
  }

  ws.onmessage = (event) => {
    try {
      const data = JSON.parse(event.data)
      if (data.type !== 'pong') console.log('[WS]', data.type, data.type === 'dag_status' ? 'has_running=' + data.has_running : '', data.type === 'dag_log' ? 'nodes=' + (data.nodes || []).length : '')
      notifyListeners(data)
    } catch (e) {}
  }

  ws.onclose = () => {
    wsState.connected = false
    notifyListeners({ type: 'connection', connected: false })
    if (!manualClose) scheduleReconnect()
  }

  ws.onerror = () => {
    wsState.connected = false
    notifyListeners({ type: 'connection', connected: false })
  }
}

function scheduleReconnect() {
  if (manualClose || reconnectTimer) return
  if (reconnectAttempts >= MAX_RECONNECT_ATTEMPTS) {
    console.warn('[WS] 已达最大重试次数，停止重连')
    return
  }
  const delay = Math.min(RECONNECT_BASE_MS * Math.pow(2, reconnectAttempts), RECONNECT_MAX_MS)
  reconnectAttempts++
  console.log(`[WS] ${delay}ms 后重连 (第 ${reconnectAttempts} 次)`)
  reconnectTimer = setTimeout(() => {
    reconnectTimer = null
    connectWebSocket(false)
  }, delay)
}

export function disconnectWebSocket() {
  manualClose = true
  if (reconnectTimer) { clearTimeout(reconnectTimer); reconnectTimer = null }
  if (ws) {
    try { ws.onclose = null; ws.onerror = null; ws.close() } catch (_) {}
    ws = null
  }
  wsState.connected = false
}

export function addWsListener(fn) {
  listeners.push(fn)
  return () => { listeners = listeners.filter(l => l !== fn) }
}

function notifyListeners(data) {
  listeners.slice().forEach(fn => {
    try { fn(data) } catch (e) { console.error('[WS] listener error', e) }
  })
}
