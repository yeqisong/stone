import test from 'node:test'
import assert from 'node:assert/strict'
import { analyzeKline, smaAt } from '../src/utils/klineSignals.js'

function bar(date, open, close, low, high, volume = 1e6, extra = {}) {
  return { trade_date: date, open, close, low, high, volume, amount: volume * close, ...extra }
}

function dates(n) {
  return Array.from({ length: n }, (_, i) => `2026-01-${String((i % 28) + 1).padStart(2, '0')}`)
}

test('smaAt 基本计算与预热', () => {
  assert.equal(smaAt([1, 2, 3, 4], 3, 2), 3.5)
  assert.equal(smaAt([1, 2, 3], 0, 2), null)
})

test('识别最新一根锤子线', () => {
  const ds = dates(40)
  const bars = ds.map((d, i) => {
    const p = 100 - i * 0.5
    return bar(d, p, p - 0.2, p - 0.4, p + 0.2)
  })
  bars[39] = bar(ds[39], 80, 80.8, 76, 81, 2e6)
  const r = analyzeKline(bars)
  assert.ok(r.patterns.length > 0, '应识别出形态')
  assert.equal(r.patterns[0].code, 'hammer')
  assert.equal(r.patterns[0].direction, 'bull')
})

test('识别低位看涨吞没', () => {
  const ds = dates(40)
  const bars = ds.map((d, i) => {
    const p = 100 - i * 0.35
    return bar(d, p, p - 0.1, p - 0.3, p + 0.2)
  })
  bars[38] = bar(ds[38], 88, 85, 84.5, 88.5)
  bars[39] = bar(ds[39], 84, 89, 83.5, 89.5, 2e6)
  const r = analyzeKline(bars)
  const p = r.patterns.find(x => x.idx === 39)
  assert.ok(p, '应识别最新一根形态')
  assert.equal(p.code, 'bull_engulf')
})

test('枢轴聚类给出支撑/压力', () => {
  const ds = dates(80)
  const bars = ds.map((d, i) => {
    const p = i % 2 === 0 ? 100 : 90
    return bar(d, p, p, p - 0.5, p + 0.5)
  })
  // 造出 90 附近的低点和 110 附近的高点
  for (let i = 10; i < 70; i += 10) {
    bars[i] = bar(ds[i], 90, 90.5, 89, 91)
    bars[i + 5] = bar(ds[i + 5], 110, 109.5, 109, 111)
  }
  bars[79] = bar(ds[79], 100, 100, 99, 101)
  const r = analyzeKline(bars)
  assert.ok(r.levels.supports.length >= 1)
  assert.ok(r.levels.resistances.length >= 1)
  assert.ok(r.levels.supports[0].price < 100)
  assert.ok(r.levels.resistances[0].price > 100)
})

test('多头排列 + 超买给出操作提示', () => {
  const ds = dates(100)
  const bars = ds.map((d, i) => {
    const p = 50 + i * 0.5
    return bar(d, p, p + 0.2, p - 0.2, p + 0.4, 1e6, { rsi: 80 })
  })
  const r = analyzeKline(bars)
  const keys = r.hints.map(h => h.key)
  assert.ok(keys.includes('trend_bull'))
  assert.ok(keys.includes('rsi_hot'))
})

test('缺失 RSI 不误判超卖', () => {
  const ds = dates(60)
  const bars = ds.map((d, i) => {
    const p = 100 + Math.sin(i / 4) * 2
    return bar(d, p, p, p - 0.5, p + 0.5)
  })
  const r = analyzeKline(bars)
  const keys = r.hints.map(h => h.key)
  assert.ok(!keys.includes('rsi_hot'))
  assert.ok(!keys.includes('rsi_cold'))
})

test('数据不足时安全返回', () => {
  const r = analyzeKline([bar('2026-01-01', 10, 10, 9, 11)])
  assert.deepEqual(r.levels, { supports: [], resistances: [] })
  assert.equal(r.trend, null)
  assert.deepEqual(r.patterns, [])
  assert.deepEqual(r.hints, [])
})
