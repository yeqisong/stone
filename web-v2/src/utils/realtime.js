/**
 * 实时页（盘中作战台）纯函数：格式化、持仓动作元数据、决策线汇总。
 * 不依赖 Vue / axios，供 web-v2/tests/realtime.test.mjs 用 node --test 直接测。
 * 口径沿用来源组件（盘中雷达 / 实时大盘）：涨红跌绿；未知数值显示「—」而不是 0。
 */

const num = v => (v == null || Number.isNaN(Number(v)) ? null : Number(v))

export const signed = v => (num(v) == null ? '—' : (num(v) >= 0 ? '+' : '') + num(v).toFixed(2))
export const yi = v => (num(v) == null ? '—' : (num(v) / 1e8).toFixed(1) + '亿')
export const yiNum = v => num(v) ?? 0
export const pctText = v => (num(v) == null ? '—' : (num(v) * 100).toFixed(1) + '%')
export const ratio0 = v => (num(v) == null ? '—' : Math.round(num(v) * 100) + '%')
// 缺值不上色（旧实现对 null 也判成涨色，「—」配红色会误导）
export const chgCls = v => (num(v) == null ? '' : (num(v) >= 0 ? 'up' : 'down'))
export const fmtPrice = v => (num(v) == null ? '—' : num(v).toFixed(2))
export const radarPct = v => (num(v) == null ? '—' : (v >= 0 ? '+' : '') + num(v).toFixed(2) + '%')
export const radarScoreLabel = s => (s >= 70 ? '强' : s >= 55 ? '偏强' : s > 45 ? '中性' : s > 30 ? '偏弱' : '弱')
export const sentiLabel = lv => ({ panic: '恐慌', weak: '偏弱', neutral: '中性', strong: '强势' }[lv] || lv || '—')

// mk% 盘面异动类型中文标签（后端 title 已含详情，此处仅分类徽标）
export const MK_LABELS = {
  mk_idx_surge: '指数急涨', mk_idx_drop: '指数急跌', mk_breadth_up: '宽度转强',
  mk_breadth_dn: '宽度转弱', mk_height: '连板新高', mk_panic: '盘面恐慌', mk_strong: '盘面亢奋',
}
export const mkLabel = k => MK_LABELS[k] || k

// ── 持仓动作（与后端 app/intraday_signal.holding_signal 的 action 一一对应） ──
export const SIGNAL_META = {
  stop:         { label: '止损',     color: 'var(--c-error)' },
  reduce:       { label: '减仓',     color: 'var(--c-error)' },
  trim:         { label: '超配减仓', color: 'var(--c-warning)' },
  reduce_watch: { label: '冲高不追', color: 'var(--c-warning)' },
  watch_stop:   { label: '减仓观察', color: 'var(--c-warning)' },
  hold:         { label: '持有',     color: 'var(--c-info)' },
}
// 严重度：越靠前越要先处理
export const SIGNAL_ORDER = { stop: 0, reduce: 1, trim: 2, reduce_watch: 3, watch_stop: 4, hold: 5 }
export const signalMeta = a => SIGNAL_META[a] || { label: a || '—', color: 'var(--c-text-dim)' }

// 决策线把六种动作归成四组：止损 / 减仓 / 观察 / 持有
const HOLDING_GROUP = { stop: 'stop', reduce: 'reduce', trim: 'reduce', reduce_watch: 'watch', watch_stop: 'watch', hold: 'hold' }

/** 持仓动作计数：{ stop, reduce, watch, hold, signaled, positions }。 */
export function holdingCounts(rows) {
  const c = { stop: 0, reduce: 0, watch: 0, hold: 0, signaled: 0, positions: 0 }
  for (const r of rows || []) {
    if (!r || !r.is_holding) continue
    c.positions++
    if (!r.signal) continue
    const g = HOLDING_GROUP[r.signal.action]
    if (g) c[g]++
    c.signaled++
  }
  return c
}

/** 持仓信号行：只留有信号的持仓，按严重度排序（止损 → 减仓 → 观察 → 持有）。 */
export function holdingsBySeverity(rows) {
  return (rows || [])
    .filter(r => r && r.is_holding && r.signal)
    .sort((a, b) => (SIGNAL_ORDER[a.signal.action] ?? 9) - (SIGNAL_ORDER[b.signal.action] ?? 9))
}

/** 雷达表排序：持仓置顶（风险优先），组内沿用后端 score 降序（sort 是稳定排序）。 */
export function radarRowsRiskFirst(rows) {
  return [...(rows || [])].sort((a, b) => (b.is_holding ? 1 : 0) - (a.is_holding ? 1 : 0))
}

/** 买点检查弹窗的预填参数（候选行 / 树图叶子共用）。 */
export function buyPresetOf(c) {
  if (!c) return null
  return {
    code: c.code,
    entry: c.price ?? c.setup?.entry ?? null,
    stop: c.setup?.stop ?? null,
    target: c.setup?.target ?? null,
  }
}

/** 决策线「开新仓」摘要：allow + 一句话（禁买给拦截原因，允许给仓位余量）。 */
export function newBuySummary(cand) {
  if (!cand) return null
  const st = cand.state || {}
  if (!cand.allowed) {
    return { allow: false, brief: cand.reason || st.advice || '当前仓位/纪律不允许开新仓' }
  }
  const parts = []
  if (st.position_ratio != null) parts.push('仓位 ' + ratio0(st.position_ratio))
  if (st.remaining_new_buys != null) parts.push('还可 ' + st.remaining_new_buys + ' 笔')
  return { allow: true, brief: parts.join(' · ') || st.advice || '' }
}

/** 决策线「开仓闸门」：实时空仓闸门（市场闸门 × regime × 盘面情绪）缺失时给中性占位。 */
export function gateSummary(gate) {
  if (!gate || !gate.level) return { level: 'none', label: '—', reason: '' }
  return { level: gate.level, label: gate.label || gate.level, reason: (gate.reasons || [])[0] || '' }
}

/** 盘面异动计数：总数 + 严重条数（critical）。 */
export function alertCounts(alerts) {
  const list = alerts || []
  return { total: list.length, critical: list.filter(a => a && a.level === 'critical').length }
}

/** 北京时间的「墙上时钟」：返回的 Date 用 getHours()/getDay() 等读到的就是北京时刻。 */
export function bjWallClock(d = new Date()) {
  return new Date(d.toLocaleString('en-US', { timeZone: 'Asia/Shanghai' }))
}

/**
 * 按客户端时钟粗判交易时段（不含节假日）：pre / open / lunch / closed。
 * 盘面接口不可用（无权限或请求失败）时的兜底，只用来决定轮询快慢，不用于展示。
 */
export function localSession(d = new Date()) {
  const t = bjWallClock(d)
  const day = t.getDay()
  if (day === 0 || day === 6) return 'closed'
  const mins = t.getHours() * 60 + t.getMinutes()
  if (mins < 570) return 'pre'
  if (mins < 690 || (mins >= 780 && mins < 900)) return 'open'
  if (mins < 780) return 'lunch'
  return 'closed'
}

/** 轮询间隔（ms）：交易时段快、收盘后慢，省外部行情接口。 */
export function pollDelay(kind, session) {
  const live = session === 'open' || session === 'pre'
  if (kind === 'radar') return live ? 15000 : 60000
  if (kind === 'cand') return live ? 60000 : 300000
  if (kind === 'market') return live ? 15000 : 60000
  return 60000
}

// ── 数据所属交易日 ──
// 盘前/休市日腾讯、东财都回退成上一交易日的整段数据：缓存刷新时间（asof）不等于数据时间，
// 缓存更新得再勤，画面上也可能全是上一交易日的数——凡是能判定数据日期的地方都要分开标出来。
const iso8 = s => (/^\d{8}$/.test(String(s || '')) ? `${s.slice(0, 4)}-${s.slice(4, 6)}-${s.slice(6)}` : '')

/** 数据真实所属交易日与「今天」不同时返回它，否则返回空串。 */
export function prevDataDate(d) {
  const dd = d?.data_date
  return dd && dd !== d?.date ? dd : ''
}

/** 决策线数据时点：「显示 <最近交易日>（最近交易日）数据 · 更新 HH:MM:SS」，同一天则只给时间。 */
export function asofText(d) {
  if (!d) return ''
  const parts = []
  if (prevDataDate(d)) parts.push(`显示 ${prevDataDate(d)}（最近交易日）数据`)
  if (d.asof) parts.push('更新 ' + d.asof)
  return parts.join(' · ') || d.date
}

export function staleText(d) {
  return prevDataDate(d) ? `当前为 ${prevDataDate(d)} 的数据，开盘后自动更新` : '数据延迟，后台采集中'
}

// 分区各自的数据日期（梯队 YYYYMMDD / 资金流 / 情绪轨迹），与今天不同才标
export function ztDateText(d) {
  const t = iso8(d?.zt?.date)
  return t && t !== d?.date ? `${t} 数据` : ''
}
export function fflowDateText(d) {
  const t = d?.fflow?.date
  return t && t !== d?.date ? `${t} 数据` : ''
}
/** 情绪轨迹标题跟随数据所属交易日（休市日/盘前显示最近交易日的，和其它卡片一致）。 */
export function trackTitle(d) {
  const t = d?.track_date
  return t && t !== d?.date ? `${t} 情绪轨迹（最近交易日）` : '当日情绪轨迹'
}
