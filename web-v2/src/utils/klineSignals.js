/**
 * K 线自动分析（纯函数，无 Vue / ECharts 依赖）。
 *
 * 输出四类结果，详情页 K 线图直接消费：
 * - levels：枢轴点聚类得到最近支撑/压力位（下穿/上破提醒）
 * - trend：近 60 根收盘线性回归趋势线（斜率 + R²，弱趋势不画线）
 * - patterns：常见 K 线形态（锤子/吞没/星线/三兵/缺口/突破等）
 * - hints：基于最新一根 K 线的规则化操作提示（接近压力、跌破支撑、均线排列、超买超卖等）
 *
 * 注意：这是规则提醒，不是预测；所有提示都必须配合仓位/止损使用。
 */

function fin(v) {
  if (v == null || v === '') return null
  const n = +v
  return Number.isFinite(n) ? n : null
}

function avg(arr) {
  const xs = arr.filter(v => Number.isFinite(v))
  return xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : null
}

export function smaAt(values, endIdx, period) {
  if (endIdx < period - 1 || period <= 0) return null
  let sum = 0
  for (let i = endIdx - period + 1; i <= endIdx; i++) {
    const v = fin(values[i])
    if (v == null) return null
    sum += v
  }
  return sum / period
}

function atrAt(bars, idx, period = 14) {
  if (idx < 1) return null
  let sum = 0
  let n = 0
  for (let i = Math.max(1, idx - period + 1); i <= idx; i++) {
    const b = bars[i]
    const prev = bars[i - 1]
    if (!b || !prev) continue
    const tr = Math.max(b.high - b.low, Math.abs(b.high - prev.close), Math.abs(b.low - prev.close))
    if (Number.isFinite(tr)) {
      sum += tr
      n++
    }
  }
  return n ? sum / n : null
}

function candle(b) {
  const body = Math.abs(b.close - b.open)
  const range = Math.max(b.high - b.low, 1e-9)
  return {
    body,
    range,
    upper: b.high - Math.max(b.open, b.close),
    lower: Math.min(b.open, b.close) - b.low,
    bull: b.close >= b.open,
    bear: b.close < b.open,
    mid: (b.open + b.close) / 2,
  }
}

function isLocalHigh(bars, i, k = 5) {
  // 左侧必须有 k 根；右侧不足时按已有数据判断（最新一根也要能识别形态）
  for (let j = 1; j <= k; j++) {
    if (i - j < 0) return false
    if (bars[i].high < bars[i - j].high) return false
  }
  for (let j = 1; j <= k && i + j < bars.length; j++) {
    if (bars[i].high < bars[i + j].high) return false
  }
  return true
}

function isLocalLow(bars, i, k = 5) {
  for (let j = 1; j <= k; j++) {
    if (i - j < 0) return false
    if (bars[i].low > bars[i - j].low) return false
  }
  for (let j = 1; j <= k && i + j < bars.length; j++) {
    if (bars[i].low > bars[i + j].low) return false
  }
  return true
}

function findPivots(bars, start, end, k = 3) {
  const highs = []
  const lows = []
  for (let i = start + k; i <= end - k; i++) {
    if (isLocalHigh(bars, i, k)) highs.push({ idx: i, price: bars[i].high })
    if (isLocalLow(bars, i, k)) lows.push({ idx: i, price: bars[i].low })
  }
  return { highs, lows }
}

function clusterPoints(points, tol, start, end) {
  const clusters = []
  for (const p of points) {
    let c = clusters.find(x => Math.abs(x.price - p.price) <= tol)
    if (!c) {
      c = { price: p.price, sum: p.price, weight: 1, touches: 1, lastIdx: p.idx }
      clusters.push(c)
    } else {
      const w = 1 + 0.5 * (p.idx - start) / Math.max(1, end - start)
      c.sum += p.price * w
      c.weight += w
      c.touches++
      c.lastIdx = Math.max(c.lastIdx, p.idx)
      c.price = c.sum / c.weight
    }
  }
  for (const c of clusters) {
    const recency = (c.lastIdx - start) / Math.max(1, end - start)
    c.score = c.touches * 2 + recency * 2
  }
  return clusters.sort((a, b) => b.score - a.score)
}

function dedupeLevels(levels, tol) {
  const out = []
  for (const l of levels) {
    if (!out.some(x => Math.abs(x.price - l.price) <= tol)) out.push(l)
  }
  return out
}

function buildLevels(bars, endIdx) {
  const close = bars[endIdx].close
  const atr = atrAt(bars, endIdx, 14) || close * 0.02
  const tol = Math.max(atr * 0.6, close * 0.012)
  const start = Math.max(0, endIdx - 240)
  const { highs, lows } = findPivots(bars, start, endIdx, 3)

  let supports = clusterPoints(lows, tol, start, endIdx)
    .filter(c => c.price < close * 0.995)
  let resistances = clusterPoints(highs, tol, start, endIdx)
    .filter(c => c.price > close * 1.005)

  const addFallback = (arr, price, source) => {
    if (!(price > 0)) return
    if (arr.some(c => Math.abs(c.price - price) <= tol)) return
    arr.push({ price, touches: 1, score: 0, lastIdx: endIdx, fallback: true, source })
  }
  const last20 = bars.slice(Math.max(0, endIdx - 19), endIdx + 1)
  const last60 = bars.slice(Math.max(0, endIdx - 59), endIdx + 1)
  if (supports.length < 2) {
    addFallback(supports, Math.min(...last20.map(b => b.low)), '20日低')
    addFallback(supports, Math.min(...last60.map(b => b.low)), '60日低')
  }
  if (resistances.length < 2) {
    addFallback(resistances, Math.max(...last20.map(b => b.high)), '20日高')
    addFallback(resistances, Math.max(...last60.map(b => b.high)), '60日高')
  }

  const fmt = (l) => ({
    price: +l.price.toFixed(2),
    touches: l.touches,
    distancePct: +((l.price / close - 1) * 100).toFixed(2),
    source: l.fallback ? l.source : '枢轴',
  })
  supports = dedupeLevels(supports, tol).filter(c => c.price < close * 0.995)
    .sort((a, b) => b.price - a.price).slice(0, 2).map(fmt)
  resistances = dedupeLevels(resistances, tol).filter(c => c.price > close * 1.005)
    .sort((a, b) => a.price - b.price).slice(0, 2).map(fmt)
  return { supports, resistances }
}

function buildTrend(bars, endIdx) {
  const len = Math.min(60, endIdx + 1)
  if (len < 20) return null
  const start = endIdx - len + 1
  const ys = []
  for (let i = start; i <= endIdx; i++) {
    const v = fin(bars[i].close)
    if (v == null) return null
    ys.push(v)
  }
  const meanX = (len - 1) / 2
  const meanY = avg(ys)
  let cov = 0
  let varX = 0
  let ssTot = 0
  for (let i = 0; i < len; i++) {
    cov += (i - meanX) * (ys[i] - meanY)
    varX += (i - meanX) ** 2
    ssTot += (ys[i] - meanY) ** 2
  }
  if (varX <= 0) return null
  const slope = cov / varX
  const intercept = meanY - slope * meanX
  let ssRes = 0
  for (let i = 0; i < len; i++) ssRes += (ys[i] - (intercept + slope * i)) ** 2
  const r2 = ssTot > 0 ? Math.max(0, 1 - ssRes / ssTot) : 0
  return {
    startIdx: start,
    endIdx,
    startPrice: +intercept.toFixed(2),
    endPrice: +(intercept + slope * (len - 1)).toFixed(2),
    slopePct: +((slope / meanY) * 100).toFixed(3),
    r2: +r2.toFixed(3),
  }
}

function detectPatterns(bars, atr, from, to) {
  const out = []
  const closes = bars.map(x => x.close)
  const add = (i, code, short, name, direction, action, score) => {
    const b = bars[i]
    out.push({
      idx: i,
      date: b.trade_date || b.date || '',
      code,
      short,
      name,
      direction,
      action,
      score,
    })
  }

  for (let i = from; i <= to; i++) {
    const b = bars[i]
    if (!b || !(b.close > 0) || !(b.high >= b.low)) continue
    const c = candle(b)
    if (c.range <= 1e-9) continue
    const prev = i > 0 ? bars[i - 1] : null
    const pc = prev ? candle(prev) : null
    const a = atr[i] || c.range
    const ma20 = smaAt(closes, i, 20)
    let hit = false

    // 1) 吞没：低位阳包阴 / 高位阴包阳
    if (pc && c.bull && pc.bear && b.open <= prev.close && b.close >= prev.open &&
        c.body >= pc.body * 0.8 && (isLocalLow(bars, i, 8) || (ma20 != null && b.close < ma20))) {
      add(i, 'bull_engulf', '吞', '看涨吞没', 'bull', '低位阳包阴，等次日不破低点再确认', 6)
      hit = true
    } else if (pc && c.bear && pc.bull && b.open >= prev.close && b.close <= prev.open &&
               c.body >= pc.body * 0.8 && (isLocalHigh(bars, i, 8) || (ma20 != null && b.close > ma20))) {
      add(i, 'bear_engulf', '吞', '看跌吞没', 'bear', '高位阴包阳，反弹减仓/止盈', 6)
      hit = true
    }

    // 2) 早晨之星 / 黄昏之星
    if (!hit && i >= from + 2) {
      const b1 = bars[i - 2]
      const b2 = bars[i - 1]
      const c1 = candle(b1)
      const c2 = candle(b2)
      const mid1 = (b1.open + b1.close) / 2
      if (c1.bear && c2.body <= Math.max(c1.body * 0.45, a * 0.35) &&
          c.bull && b.close > mid1 && b.close > b2.close && isLocalLow(bars, i, 8)) {
        add(i, 'morning_star', '晨', '早晨之星', 'bull', '止跌信号，等次日确认后再动', 7)
        hit = true
      } else if (c1.bull && c2.body <= Math.max(c1.body * 0.45, a * 0.35) &&
                 c.bear && b.close < mid1 && b.close < b2.close && isLocalHigh(bars, i, 8)) {
        add(i, 'evening_star', '昏', '黄昏之星', 'bear', '见顶信号，减仓/止盈', 7)
        hit = true
      }
    }

    // 3) 锤子线 / 射击之星
    if (!hit && c.body <= c.range * 0.4) {
      if (c.lower >= Math.max(c.body * 2, c.range * 0.5) && isLocalLow(bars, i, 6)) {
        add(i, 'hammer', '锤', '锤子线', 'bull', '下影长，若次日不破低点，可能止跌；等确认', 4)
        hit = true
      } else if (c.upper >= Math.max(c.body * 2, c.range * 0.5) && isLocalHigh(bars, i, 6)) {
        add(i, 'shooting_star', '星', '射击之星', 'bear', '上影长，冲高回落；持仓注意减仓', 4)
        hit = true
      }
    }

    // 4) 红三兵 / 三只乌鸦
    if (!hit && i >= from + 2) {
      const b1 = bars[i - 2]
      const b2 = bars[i - 1]
      const c1 = candle(b1)
      const c2 = candle(b2)
      if (c1.bull && c2.bull && c.bull &&
          b1.close < b2.close && b2.close < b.close &&
          c1.body > c1.range * 0.45 && c2.body > c2.range * 0.45 && c.body > c.range * 0.45) {
        add(i, 'three_white', '三红', '红三兵', 'bull', '趋势延续，回踩不破可持有', 3)
        hit = true
      } else if (c1.bear && c2.bear && c.bear &&
                 b1.close > b2.close && b2.close > b.close &&
                 c1.body > c1.range * 0.45 && c2.body > c2.range * 0.45 && c.body > c.range * 0.45) {
        add(i, 'three_black', '三黑', '三只乌鸦', 'bear', '趋势转弱，反弹减仓', 3)
        hit = true
      }
    }

    // 5) 跳空缺口
    if (!hit && prev) {
      const gapUp = b.low > prev.high * 1.003 && (b.low - prev.high) > a * 0.25
      const gapDown = b.high < prev.low * 0.997 && (prev.low - b.high) > a * 0.25
      if (gapUp) {
        add(i, 'gap_up', '缺', '向上跳空', 'bull', '缺口不补偏强；追高谨慎，回踩缺口看承接', 5)
        hit = true
      } else if (gapDown) {
        add(i, 'gap_down', '缺', '向下跳空', 'bear', '反抽缺口不过偏弱；不抄底', 5)
        hit = true
      }
    }

    // 6) 放量突破 20 日高 / 跌破 20 日低
    if (!hit && i >= from + 20) {
      const prev20 = bars.slice(i - 20, i)
      const hi20 = Math.max(...prev20.map(x => x.high))
      const lo20 = Math.min(...prev20.map(x => x.low))
      const vols = prev20.map(x => x.volume).filter(v => v > 0)
      const vAvg = avg(vols)
      const vr = vAvg ? b.volume / vAvg : null
      if (vr != null && vr >= 1.5 && b.close > hi20 * 1.005) {
        add(i, 'breakout', '破', '放量突破20日高', 'bull', '回踩突破位不破可看多；跌回则止损', 6)
        hit = true
      } else if (vr != null && vr >= 1.5 && b.close < lo20 * 0.995) {
        add(i, 'breakdown', '破', '跌破20日低', 'bear', '持仓先减/止损，不补仓', 6)
        hit = true
      }
    }

    // 7) 十字星 / 长影线（较弱，只在高位/低位）
    if (!hit && c.body <= c.range * 0.1 && c.range >= a * 0.5 &&
        (isLocalHigh(bars, i, 5) || isLocalLow(bars, i, 5))) {
      add(i, 'doji', '十', '十字星', 'neutral', '方向待选择，等确认', 2)
      hit = true
    }
    if (!hit && c.body > 0) {
      if (c.upper >= Math.max(c.body * 2, c.range * 0.6)) {
        add(i, 'long_upper', '上影', '长上影', 'bear', '冲高回落，注意次日确认；持仓可减', 2)
      } else if (c.lower >= Math.max(c.body * 2, c.range * 0.6)) {
        add(i, 'long_lower', '下影', '长下影', 'bull', '下方有承接，等次日不破低点', 2)
      }
    }
  }
  return out.sort((a, b) => b.idx - a.idx)
}

function buildHints(bars, levels, trend, patterns) {
  const i = bars.length - 1
  const b = bars[i]
  const close = b.close
  const atr = atrAt(bars, i, 14) || close * 0.02
  const ma5 = smaAt(bars.map(x => x.close), i, 5)
  const ma10 = smaAt(bars.map(x => x.close), i, 10)
  const ma20 = smaAt(bars.map(x => x.close), i, 20)
  const ma60 = smaAt(bars.map(x => x.close), i, 60)
  const prevMa5 = smaAt(bars.map(x => x.close), i - 1, 5)
  const prevMa20 = smaAt(bars.map(x => x.close), i - 1, 20)
  const prev20 = bars.slice(Math.max(0, i - 20), i)
  const vols = prev20.map(x => x.volume).filter(v => v > 0)
  const vAvg = avg(vols)
  const volRatio = vAvg ? b.volume / vAvg : null
  const rsi = fin(b.rsi)
  const bollUp = fin(b.boll_upper)
  const bollLo = fin(b.boll_lower)
  const c = candle(b)
  const hints = []

  const add = (key, text, detail, tone, score) => hints.push({ key, text, detail, tone, score })

  if (ma20 != null && ma60 != null) {
    if (close > ma20 && ma20 > ma60) {
      add('trend_bull', '价 > MA20 > MA60（多头排列）',
        '回踩 MA20 不破可持有；收盘跌破 MA20 先减仓', 'positive', 7)
    } else if (close < ma20 && ma20 < ma60) {
      add('trend_bear', '价 < MA20 < MA60（空头排列）',
        '反弹到 MA20/MA60 附近是减仓机会，不抄底', 'danger', 8)
    }
  }
  if (ma5 != null && ma20 != null && prevMa5 != null && prevMa20 != null) {
    if (prevMa5 <= prevMa20 && ma5 > ma20) {
      add('ma_cross_up', 'MA5 上穿 MA20', '短期转强，等回踩确认再动', 'positive', 6)
    } else if (prevMa5 >= prevMa20 && ma5 < ma20) {
      add('ma_cross_down', 'MA5 下穿 MA20', '短期转弱，反弹减仓，不补仓', 'warn', 6)
    }
  }

  const nearRes = levels.resistances.find(l => l.distancePct <= 1.5)
  const nearSup = levels.supports.find(l => l.distancePct >= -1.5)
  if (nearRes) {
    add('near_resistance', `接近压力位 ${nearRes.price.toFixed(2)}（+${nearRes.distancePct.toFixed(1)}%）`,
      '不追高；持仓可先减 1/3，放量突破再看', 'warn', 8)
  }
  if (nearSup) {
    add('near_support', `接近支撑位 ${nearSup.price.toFixed(2)}（${nearSup.distancePct.toFixed(1)}%）`,
      '观察能否企稳；收盘跌破支撑则止损', 'info', 6)
  }
  if (!levels.supports.length) {
    add('no_support', '下方无近端支撑（阶段新低附近）',
      '跌破前低先止损，不抄底；等止跌结构/放量反包', 'danger', 7)
  }
  if (!levels.resistances.length) {
    add('no_resistance', '上方无近端压力（阶段新高附近）',
      '持仓可持，追高需设止损；放量滞涨先减仓', 'info', 3)
  }

  // 20 日平台突破/跌破（与形态检测互补，最新一根即可提示）
  if (prev20.length >= 20) {
    const hi20 = Math.max(...prev20.map(x => x.high))
    const lo20 = Math.min(...prev20.map(x => x.low))
    if (volRatio != null && volRatio >= 1.3 && close > hi20 * 1.005) {
      add('breakout20', `放量突破20日高 ${hi20.toFixed(2)}（量比 ${volRatio.toFixed(1)}）`,
        '回踩突破位不破可看多；跌回则止损', 'positive', 9)
    } else if (volRatio != null && volRatio >= 1.3 && close < lo20 * 0.995) {
      add('breakdown20', `跌破20日低 ${lo20.toFixed(2)}（量比 ${volRatio.toFixed(1)}）`,
        '持仓先减/止损，不补仓', 'danger', 10)
    }
  }

  if (rsi != null) {
    if (rsi >= 75) add('rsi_hot', `RSI ${rsi.toFixed(1)}（超买）`, '注意冲高回落，不追高', 'warn', 5)
    else if (rsi <= 25) add('rsi_cold', `RSI ${rsi.toFixed(1)}（超卖）`, '超跌不抄底，等止跌/放量反包', 'info', 4)
  }
  if (bollUp != null && close > bollUp) {
    add('boll_upper', '收盘站上 BOLL 上轨', '短期过热，持仓可部分止盈；追高需谨慎', 'warn', 4)
  } else if (bollLo != null && close < bollLo) {
    add('boll_lower', '收盘跌破 BOLL 下轨', '超跌区，等企稳信号，不急着接', 'info', 4)
  }

  if (volRatio != null && volRatio >= 2 && c.body >= c.range * 0.6) {
    if (c.bull) add('vol_up', `放量长阳（量比 ${volRatio.toFixed(1)}）`, '有资金参与，等回踩不破再动；追高设止损', 'positive', 5)
    else add('vol_down', `放量长阴（量比 ${volRatio.toFixed(1)}）`, '抛压重，反弹减仓，不补仓', 'danger', 7)
  }

  if (trend) {
    if (trend.r2 < 0.15) {
      add('range', '近60日区间震荡', '趋势线参考性弱，按支撑/压力做区间，不追单边', 'info', 3)
    } else if (trend.slopePct >= 0.03) {
      add('trend_up', `近60日上升趋势（+${trend.slopePct.toFixed(2)}%/日）`,
        '回踩趋势线/MA20 不破可持；跌破趋势线减仓', 'positive', 4)
    } else if (trend.slopePct <= -0.03) {
      add('trend_down', `近60日下降趋势（${trend.slopePct.toFixed(2)}%/日）`,
        '反弹到趋势线附近减仓，不抄底', 'warn', 5)
    }
  }

  // 近 3 根内的形态作为提示补充（昨天的形态今天仍可能有效）
  const recent = patterns.filter(p => p.idx >= i - 2).slice(0, 2)
  for (const p of recent) {
    const key = `pattern_${p.code}_${p.idx}`
    if (hints.some(h => h.key === key)) continue
    const tone = p.direction === 'bull' ? 'positive' : (p.direction === 'bear' ? 'warn' : 'info')
    add(key, `${p.date.slice(5)} ${p.name}`, p.action, tone, Math.max(1, p.score - 1))
  }

  const seen = new Set()
  return hints
    .sort((a, b2) => b2.score - a.score)
    .filter(h => {
      if (seen.has(h.key)) return false
      seen.add(h.key)
      return true
    })
    .slice(0, 6)
}

export function analyzeKline(bars, opts = {}) {
  // 不在这里 filter：形态/画线的 idx 必须与图表 bars 下标严格一致，
  // 过滤会让标记错位。调用方画 K 线时已保证 OHLC 有效。
  const list = Array.isArray(bars) ? bars : []
  const n = list.length
  if (n < 10) {
    return { levels: { supports: [], resistances: [] }, trend: null, patterns: [], hints: [], meta: { bars: n } }
  }
  const last = n - 1
  const atr = []
  for (let i = 0; i < n; i++) atr.push(atrAt(list, i, 14))
  const levels = buildLevels(list, last)
  const trend = buildTrend(list, last)
  const from = Math.max(0, n - 120)
  const patterns = detectPatterns(list, atr, from, last)
  const hints = buildHints(list, levels, trend, patterns)
  return {
    levels,
    trend,
    patterns,
    hints,
    meta: {
      bars: n,
      close: +list[last].close.toFixed(2),
      atr: atr[last] != null ? +atr[last].toFixed(2) : null,
    },
  }
}
