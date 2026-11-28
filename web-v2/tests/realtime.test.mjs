import test from 'node:test'
import assert from 'node:assert/strict'
import {
  alertCounts, asofText, buyPresetOf, chgCls, fflowDateText, fmtPrice, gateSummary, holdingCounts,
  holdingsBySeverity, localSession, newBuySummary, pctText, pollDelay, prevDataDate, radarPct, radarRowsRiskFirst,
  ratio0, signalMeta, signed, staleText, trackTitle, yi, ztDateText,
} from '../src/utils/realtime.js'

const holding = (code, action, extra = {}) => ({
  code, name: code, is_holding: true, score: 50, signal: { action, level: 'ok', reason: 'r' }, ...extra,
})
const watch = (code, score) => ({ code, name: code, is_holding: false, signal: null, score })

test('格式化：缺值显示「—」而不是 0，不让 0 伪装成数据', () => {
  for (const f of [signed, yi, pctText, ratio0, fmtPrice, radarPct]) {
    assert.equal(f(null), '—', f.name)
    assert.equal(f(undefined), '—', f.name)
  }
  assert.equal(signed(1.234), '+1.23')
  assert.equal(signed(-0.5), '-0.50')
  assert.equal(signed(0), '+0.00')
  assert.equal(yi(8_520_000_000), '85.2亿')
  assert.equal(ratio0(0.6234), '62%')
  assert.equal(radarPct(2.5), '+2.50%')
  assert.equal(radarPct(-1.006), '-1.01%')   // （-1.005 在二进制里略小于 1.005，toFixed 会给 -1.00，别拿它当用例）
})

test('涨跌配色：缺值不上色（旧实现对 null 判成涨色，「—」配红会误导）', () => {
  assert.equal(chgCls(null), '')
  assert.equal(chgCls(undefined), '')
  assert.equal(chgCls(1.2), 'up')
  assert.equal(chgCls(0), 'up')
  assert.equal(chgCls(-0.01), 'down')
})

test('持仓动作计数：六种动作归成 止损/减仓/观察/持有，无信号的持仓只计入 positions', () => {
  const rows = [
    holding('a', 'stop'), holding('b', 'reduce'), holding('c', 'trim'),
    holding('d', 'reduce_watch'), holding('e', 'watch_stop'), holding('f', 'hold'),
    { code: 'g', is_holding: true, signal: null },
    watch('h', 80),
  ]
  assert.deepEqual(holdingCounts(rows), { stop: 1, reduce: 2, watch: 2, hold: 1, signaled: 6, positions: 7 })
  assert.deepEqual(holdingCounts(null), { stop: 0, reduce: 0, watch: 0, hold: 0, signaled: 0, positions: 0 })
})

test('持仓信号按严重度排序：止损 → 减仓 → 观察 → 持有，非持仓与无信号行剔除', () => {
  const rows = [holding('h', 'hold'), watch('x', 99), holding('w', 'watch_stop'), holding('s', 'stop'), { code: 'n', is_holding: true, signal: null }, holding('r', 'reduce')]
  assert.deepEqual(holdingsBySeverity(rows).map(r => r.code), ['s', 'r', 'w', 'h'])
})

test('雷达表持仓置顶，组内保持后端给的原有（score 降序）顺序', () => {
  // 后端 items 已按 score 降序；这里只做稳定分组，不重排
  const rows = [watch('w1', 90), watch('w2', 70), holding('h1', 'stop', { score: 60 }), watch('w3', 50), holding('h2', 'hold', { score: 40 })]
  assert.deepEqual(radarRowsRiskFirst(rows).map(r => r.code), ['h1', 'h2', 'w1', 'w2', 'w3'])
  // 不改入参
  assert.deepEqual(rows.map(r => r.code), ['w1', 'w2', 'h1', 'w3', 'h2'])
})

test('买点检查预填：候选行取现价与结构位；树图叶子只有代码和现价；空值给 null', () => {
  assert.deepEqual(buyPresetOf({ code: '600519', price: 12.3, setup: { entry: 12, stop: 11.8, target: 13.6 } }),
    { code: '600519', entry: 12.3, stop: 11.8, target: 13.6 })
  assert.deepEqual(buyPresetOf({ code: '600519', setup: { entry: 12, stop: 11.8, target: 13.6 } }),
    { code: '600519', entry: 12, stop: 11.8, target: 13.6 })
  assert.deepEqual(buyPresetOf({ code: '000001', price: 10.5 }), { code: '000001', entry: 10.5, stop: null, target: null })
  assert.equal(buyPresetOf(null), null)
})

test('决策线「开新仓」：禁买给拦截原因，允许给仓位余量', () => {
  assert.equal(newBuySummary(null), null)
  assert.deepEqual(newBuySummary({ allowed: false, reason: '仓位高于目标，新开仓前先减仓', state: {} }),
    { allow: false, brief: '仓位高于目标，新开仓前先减仓' })
  assert.deepEqual(newBuySummary({ allowed: false, state: { advice: '今日只减不加' } }), { allow: false, brief: '今日只减不加' })
  assert.deepEqual(newBuySummary({ allowed: true, state: { position_ratio: 0.62, remaining_new_buys: 2 } }),
    { allow: true, brief: '仓位 62% · 还可 2 笔' })
  assert.deepEqual(newBuySummary({ allowed: true, state: {} }), { allow: true, brief: '' })
})

test('决策线「开仓闸门」：缺失给中性占位，不假装开放也不假装关闭', () => {
  assert.deepEqual(gateSummary(null), { level: 'none', label: '—', reason: '' })
  assert.deepEqual(gateSummary({ level: 'caution', label: '谨慎', reasons: ['市场闸门走平', 'regime 放行'] }),
    { level: 'caution', label: '谨慎', reason: '市场闸门走平' })
})

test('盘面异动计数与动作元数据', () => {
  assert.deepEqual(alertCounts(undefined), { total: 0, critical: 0 })
  assert.deepEqual(alertCounts([{ level: 'warn' }, { level: 'critical' }, { level: 'critical' }]), { total: 3, critical: 2 })
  assert.equal(signalMeta('stop').label, '止损')
  assert.equal(signalMeta('nope').label, 'nope')
  assert.equal(signalMeta(undefined).label, '—')
})

test('交易时段粗判（北京时间，不含节假日）：只决定轮询快慢', () => {
  const at = s => localSession(new Date(s))
  assert.equal(at('2026-11-27T09:00:00+08:00'), 'pre')     // 周五盘前
  assert.equal(at('2026-11-27T09:30:00+08:00'), 'open')
  assert.equal(at('2026-11-27T11:29:00+08:00'), 'open')
  assert.equal(at('2026-11-27T11:30:00+08:00'), 'lunch')
  assert.equal(at('2026-11-27T13:00:00+08:00'), 'open')
  assert.equal(at('2026-11-27T14:59:00+08:00'), 'open')
  assert.equal(at('2026-11-27T15:00:00+08:00'), 'closed')
  assert.equal(at('2026-11-28T10:00:00+08:00'), 'closed')   // 周六
  // UTC 时刻也要落到北京时间判断：周五 02:00Z = 北京 10:00
  assert.equal(at('2026-11-27T02:00:00Z'), 'open')
})

test('轮询间隔：交易时段快、收盘后慢', () => {
  assert.equal(pollDelay('radar', 'open'), 15000)
  assert.equal(pollDelay('radar', 'pre'), 15000)
  assert.equal(pollDelay('radar', 'closed'), 60000)
  assert.equal(pollDelay('radar', 'lunch'), 60000)
  assert.equal(pollDelay('cand', 'open'), 60000)
  assert.equal(pollDelay('cand', 'closed'), 300000)
  assert.equal(pollDelay('market', 'open'), 15000)
  assert.equal(pollDelay('other', 'open'), 60000)
})

test('数据所属交易日：盘前/休市日各源回退成上一交易日，缓存更新再勤也不能冒充今天', () => {
  const stale = { date: '2026-11-28', data_date: '2026-11-27', asof: '09:20:05', zt: { date: '20261127' }, fflow: { date: '2026-11-27' }, track_date: '2026-11-27' }
  const fresh = { date: '2026-11-27', data_date: '2026-11-27', asof: '10:32:15', zt: { date: '20261127' }, fflow: { date: '2026-11-27' }, track_date: '2026-11-27' }
  assert.equal(prevDataDate(stale), '2026-11-27')
  assert.equal(prevDataDate(fresh), '')
  assert.equal(asofText(stale), '显示 2026-11-27（最近交易日）数据 · 更新 09:20:05')
  assert.equal(asofText(fresh), '更新 10:32:15')
  assert.equal(asofText({ date: '2026-11-27' }), '2026-11-27')     // 没有更新时间就只给日期
  assert.equal(asofText(null), '')
  assert.equal(staleText(stale), '当前为 2026-11-27 的数据，开盘后自动更新')
  assert.equal(staleText(fresh), '数据延迟，后台采集中')
  // 分区各自的数据日期：与今天不同才标
  assert.equal(ztDateText(stale), '2026-11-27 数据')
  assert.equal(ztDateText(fresh), '')
  assert.equal(ztDateText({ zt: { date: 'bad' } }), '')
  assert.equal(fflowDateText(stale), '2026-11-27 数据')
  assert.equal(fflowDateText(fresh), '')
  assert.equal(trackTitle(stale), '2026-11-27 情绪轨迹（最近交易日）')
  assert.equal(trackTitle(fresh), '当日情绪轨迹')
})

test('情绪轨迹 option：全空点不画；空点保留（缺口断线）；visualMap 不写 dimension（写 0 会沿横轴切段）', async () => {
  const { trackOption } = await import('../src/utils/realtimeCharts.js')
  assert.equal(trackOption([], null), null)
  assert.equal(trackOption([{ slot: '09:30', score: null }, { slot: '09:40', score: null }], null), null)
  const opt = trackOption([{ slot: '09:30', score: 60 }, { slot: '09:40', score: null }, { slot: '09:50', score: 62 }], { score: 58 })
  assert.deepEqual(opt.series[0].data, [60, null, 62])
  assert.equal(opt.series[0].connectNulls, undefined)
  assert.equal('dimension' in opt.visualMap, false)
})
