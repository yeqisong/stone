/**
 * 实时页（盘中作战台）纯函数：格式化、持仓动作元数据、决策线汇总。
 * 不依赖 Vue / axios，供 web-v2/tests/realtime.test.mjs 用 node --test 直接测。
 * 口径沿用来源组件（盘中雷达 / 实时大盘）：涨红跌绿；未知数值显示「—」而不是 0。
 */

const num = v => (v == null || Number.isNaN(Number(v)) ? null : Number(v))

export const signed = v => (num(v) == null ? '—' : (num(v) >= 0 ? '+' : '') + num(v).toFixed(2))
export const yi = v => (num(v) == null ? '—' : (num(v) / 1e8).toFixed(1) + '亿')
// 元 → 亿（图表数值用；缺值按 0 才不会把折线打断）
export const yiNum = v => (num(v) == null ? 0 : num(v) / 1e8)
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

/**
 * 决策线「开仓闸门」：实时空仓闸门（市场闸门 × regime × 盘面情绪）缺失时给中性占位。
 * partial=结论不完整：后端把「情绪源不可用 / 仍是上一交易日口径」写在 reasons 靠后的位置，
 * 展示层不能把缺了一路的判断说成完整的「放行」，所以单独拎出来标在 chip 旁边。
 */
export function gateSummary(gate) {
  if (!gate || !gate.level) return { level: 'none', label: '—', reason: '', partial: false }
  const reasons = gate.reasons || []
  return {
    level: gate.level,
    label: gate.label || gate.level,
    reason: reasons[0] || '',
    partial: reasons.some(r => /不可用|未含|上一交易日/.test(String(r))),
  }
}

/**
 * 开新仓被拦时的「下一步」：按拦截原因分两类——
 * 仓位/纪律类（仓位超目标、现金不足、开仓数用完、回补/回转、连亏冷静期）→ 回持仓页处理；
 * 市场闸门类 → 今天只管持仓。纪律页面的价值在于被拦时告诉你该去哪，而不是留一片空白。
 * 优先用后端给的结构化字段 state.allow_new_buy（candidates 的 state digest 自带）；
 * 字段缺失时才退回文案启发式。
 */
export function blockedNextStep(cand) {
  if (!cand || cand.allowed) return null
  const st = cand.state || {}
  const why = [cand.reason, st.advice].filter(Boolean).join('；')
  const reduce = typeof st.allow_new_buy === 'boolean'
    ? st.allow_new_buy === false
    : /仓位|减仓|现金|额度|开仓|回转|回补|亏损|单票|冷静|只减/.test(why)
  return reduce
    ? { kind: 'reduce', text: '先回持仓页处理：减仓清单 / 交易前检查 / 记录今日操作' }
    : { kind: 'wait', text: '市场闸门未过：今天只管持仓，不开新仓' }
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
  if (kind === 'alerts') return live ? 30000 : 120000   // 今日盘中提醒（个人 it%）
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

/**
 * 决策线数据时点：「显示 <最近交易日>（最近交易日）数据 · 更新 HH:MM:SS」，同一天则只给时间。
 * 后端刚重启时缓存是空的（asof 为空）：要明说「后台采集中」，不能只丢一个日期让人以为数据就是今天的。
 */
export function asofText(d) {
  if (!d) return ''
  const parts = []
  if (prevDataDate(d)) parts.push(`显示 ${prevDataDate(d)}（最近交易日）数据`)
  parts.push(d.asof ? '更新 ' + d.asof : '后台采集中…')
  return parts.join(' · ')
}

export function staleText(d) {
  return prevDataDate(d) ? `当前为 ${prevDataDate(d)} 的数据，开盘后自动更新` : '数据延迟，后台采集中'
}

/** 北京时间的「今天」（YYYY-MM-DD）：过滤今日告警、判断雷达数据是否属于今天。 */
export function bjToday(d = new Date()) {
  const t = bjWallClock(d)
  return `${t.getFullYear()}-${String(t.getMonth() + 1).padStart(2, '0')}-${String(t.getDate()).padStart(2, '0')}`
}

/** 最近的「工作日」日期串（YYYY-MM-DD）：周末回退到周五。不含节假日，只作盘面数据未到时的兜底。 */
export function latestWeekday(d = new Date()) {
  const t = bjWallClock(d)
  while (t.getDay() === 0 || t.getDay() === 6) t.setDate(t.getDate() - 1)
  return `${t.getFullYear()}-${String(t.getMonth() + 1).padStart(2, '0')}-${String(t.getDate()).padStart(2, '0')}`
}

/**
 * 雷达/候选的数据所属交易日标注：快照不是今天的（盘前/休市日腾讯回退上一交易日）
 * 必须标出来——「更新 22:16」是缓存生成时间，不等于数据时间（与盘面 data_date 同一口径）。
 */
export function radarDateNote(dataDate, today) {
  return dataDate && today && dataDate !== today ? `显示 ${dataDate}（最近交易日）数据` : ''
}

/**
 * 机会区顶部与实时闸门的口径提示：候选扫描（allowed）是日频口径，决策线的实时闸门
 * 是盘中口径，两者可能矛盾——allowed 时若实时闸门关闭/警戒/不可用，要如实注明，
 * 不能让同一屏上「警戒」和「可以找买点」各说各话（v3.26 决策：不改候选行为，只补展示）。
 */
export function opportunityGateNote(gate) {
  const lv = gate && gate.level
  if (lv === 'closed') return '实时闸门已关闭：候选仍按日频口径扫描，开仓前以决策线闸门结论为准'
  if (lv === 'caution') return '实时闸门警戒：开仓前先看决策线闸门结论'
  if (lv === 'unknown') return '实时闸门结论不完整（数据源缺失）：开仓前自行核对盘面'
  return ''
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

// ── 持仓区雷达表的列档位 ──
// 表在窄栏里放不下全量 12 列：按「盯盘必需 → 结构指标 → 次要」逐级累加，容器有多宽就放开到哪一档。
// 放不下的整列不显示（横着截半列数字比没有更糟）；行业不在列里时挂在名称的 tooltip 上。
export const RADAR_FIXED_WIDTH = 72 + 112   // 代码 + 名称（固定在左侧；单元格内边距 8px）
export const RADAR_COL_LEVELS = [
  { key: 'price', title: '最新价', width: 82 },
  { key: 'change_pct', title: '涨跌幅', width: 80 },
  { key: 'signal', title: '信号', width: 76 },
  { key: 'score', title: '雷达分', width: 100 },
  { key: 'vwap_dev_pct', title: 'VWAP偏离', width: 88 },
  { key: 'day_position_pct', title: '日位置', width: 72 },
  { key: 'volume_ratio', title: '量比', width: 62 },
  { key: 'gap_pct', title: '跳空', width: 76 },
  { key: 'amount', title: '成交额', width: 86 },
  { key: 'industry', title: '行业', width: 110 },
]
/** 容器宽度 → { keys: 可见的指标列, width: 表格总宽 }。再窄也保留第一档（此时才横向滚动）。 */
export function radarColumnsFor(containerW) {
  const w = Number(containerW) || 0
  let sum = RADAR_FIXED_WIDTH
  let n = 0
  for (const c of RADAR_COL_LEVELS) {
    if (sum + c.width > w) break
    sum += c.width
    n++
  }
  if (n === 0) return { keys: [RADAR_COL_LEVELS[0].key], width: RADAR_FIXED_WIDTH + RADAR_COL_LEVELS[0].width }
  return { keys: RADAR_COL_LEVELS.slice(0, n).map(c => c.key), width: sum }
}
