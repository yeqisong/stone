// 带 .js 后缀：本模块要能被 node --test 直接加载（无扩展名导入只有 Vite 能解析）
import { chartColors as T } from './chart.js'
import { pctText, signed, sentiLabel, yi, yiNum } from './realtime.js'

/**
 * 实时页 ECharts option 构造器（原 MarketRealtimeView 的 8 个 draw* 函数迁出）。
 * 约定不变：animation 关（15s 轮询重画不做过渡）、颜色取 chartColors（canvas 读不到 CSS 变量）、
 * 红涨绿跌。入参缺数据时返回 null——RtChart 收到 null 即清空，不画空坐标系。
 */

/** 分时类目轴：x 轴刻度按真实时间对齐（整点/半点）。 */
const minutesAxis = ts => ({
  type: 'category', data: ts, boundaryGap: false,
  axisLabel: timeAxisLabel(ts), axisLine: { lineStyle: { color: T.line } }, axisTick: { show: false },
})
const VALUE_Y = extra => ({ type: 'value', scale: true, axisLabel: { color: T.dim, fontSize: 10 }, splitLine: { lineStyle: { color: T.line } }, ...extra })

// y 轴紧凑刻度：深证成指上万后 ECharts 默认的 "10,900.00" 在 120px 高的迷你图里会叠成一团
export const axisCompact = v => (Math.abs(v) >= 10000 ? (v / 10000).toFixed(2) + '万' : String(Math.round(v)))
// 类目轴用固定 interval（每 N 个点一个）会在午休后漂成 13:29 / 13:59——午休那 90 分钟在类目轴上不占位
export function timeAxisLabel(ts) {
  const show = new Set(ts.filter(t => /:(00|30)$/.test(t)))
  return { color: T.dim, fontSize: 10, hideOverlap: true, interval: (i, v) => i === 0 || show.has(v) }
}

/** 指数小图（sparkline）：价格 + 昨收虚线。 */
export function miniTrendOption(ix) {
  if (!ix || !ix.trend || !ix.trend.length) return null
  const prices = ix.trend.map(x => x.price)
  const ts = ix.trend.map(x => x.t)
  const last = prices[prices.length - 1]
  return {
    animation: false,
    grid: { left: 50, right: 6, top: 6, bottom: 16 },
    xAxis: { type: 'category', data: ts, axisLabel: { show: false }, axisLine: { lineStyle: { color: T.line } }, axisTick: { show: false } },
    yAxis: { type: 'value', scale: true, splitNumber: 3, axisLabel: { color: T.dim, fontSize: 10, hideOverlap: true, formatter: axisCompact }, splitLine: { lineStyle: { color: T.line } }, axisLine: { show: false } },
    tooltip: { trigger: 'axis', formatter: p => `${ts[p[0]?.dataIndex]} ${p[0]?.value}` },
    series: [
      { type: 'line', data: prices, symbol: 'none', lineStyle: { width: 1.2, color: last >= (ix.preClose || prices[0]) ? T.up : T.down },
        markLine: { silent: true, symbol: 'none', data: [{ yAxis: ix.preClose, lineStyle: { color: T.faint, type: 'dashed', width: 1 } }], label: { show: false } } },
    ],
  }
}

/** 指数大图：价格 + 昨收标注 + 较昨收 tooltip。 */
export function bigTrendOption(ix) {
  if (!ix || !ix.trend || !ix.trend.length) return null
  const ts = ix.trend.map(x => x.t)
  const prices = ix.trend.map(x => x.price)
  const color = ix.preClose && prices[prices.length - 1] >= ix.preClose ? T.up : T.down
  return {
    animation: false,
    grid: { left: 60, right: 46, top: 24, bottom: 28 },
    tooltip: { trigger: 'axis', axisPointer: { type: 'cross' }, formatter: p => {
      const i = p[0]?.dataIndex
      return `<b>${ix.name}</b> ${ts[i]}<br/>价 ${prices[i]}<br/>较昨收 ${signed(ix.preClose ? (prices[i] / ix.preClose - 1) * 100 : null)}%`
    } },
    xAxis: minutesAxis(ts),
    yAxis: VALUE_Y({ axisLabel: { color: T.dim, fontSize: 10, formatter: axisCompact } }),
    series: [
      { name: '价格', type: 'line', data: prices, symbol: 'none', lineStyle: { width: 1.6, color },
        areaStyle: { opacity: 0.06, color },
        markLine: { silent: true, symbol: 'none', data: [{ yAxis: ix.preClose, label: { formatter: '昨收 ' + ix.preClose, fontSize: 10, color: T.faint, position: 'insideEndTop' }, lineStyle: { color: T.faint, type: 'dashed', width: 1 } }] } },
    ],
  }
}

/** 量能对比：今日 / 昨日 / 近4日均值累计成交额（亿）。 */
export function turnoverOption(tv) {
  if (!tv || !tv.series || !tv.series.length) return null
  const ts = tv.series.map(x => x.t)
  const y2v = v => (v == null ? null : Number(v) / 1e8)
  return {
    animation: false,
    grid: { left: 52, right: 10, top: 26, bottom: 20 },
    legend: { top: 2, textStyle: { color: T.dim, fontSize: 10 }, itemWidth: 12, data: ['今日', '昨日', '近4日均'] },
    tooltip: { trigger: 'axis', formatter: p => `${ts[p[0]?.dataIndex]}<br/>` + p.map(x => `${x.marker}${x.seriesName} ${x.value == null ? '—' : Number(x.value).toFixed(1) + '亿'}`).join('<br/>') },
    xAxis: minutesAxis(ts),
    yAxis: VALUE_Y({ axisLabel: { color: T.dim, fontSize: 10, formatter: axisCompact }, name: '亿', nameTextStyle: { color: T.faint } }),
    series: [
      { name: '今日', type: 'line', data: tv.series.map(x => y2v(x.today)), symbol: 'none', lineStyle: { width: 1.5, color: T.up } },
      { name: '昨日', type: 'line', data: tv.series.map(x => y2v(x.yday)), symbol: 'none', lineStyle: { width: 1, color: T.dim, type: 'dashed' } },
      { name: '近4日均', type: 'line', data: tv.series.map(x => y2v(x.avg_prev)), symbol: 'none', lineStyle: { width: 1, color: T.series[3] } },
    ],
  }
}

/**
 * 当日情绪轨迹：三成分口径逐 slot 分值，visualMap 按档位着色；实时四成分分作虚线参考。
 * 后端把缺失的 slot 补成空点（score=null）：没有空点时类目轴会把 09:50 直接连到 11:10，
 * 所以空点保留在数据里（折线默认 connectNulls=false，缺口处断线）。
 */
export function trackOption(track, senti) {
  if (!track || !track.length || !track.some(x => x && x.score != null)) return null
  const slots = track.map(x => x.slot)
  const refLine = (senti && senti.score != null)
    ? [{ yAxis: senti.score, lineStyle: { color: T.faint, type: 'dotted', width: 1 }, label: { formatter: `实时 ${senti.score}`, fontSize: 10, color: T.faint, position: 'insideEndTop' } }]
    : []
  return {
    animation: false,
    grid: { left: 40, right: 14, top: 24, bottom: 22 },
    tooltip: { trigger: 'axis', formatter: p => {
      const x = track[p[0]?.dataIndex]
      if (!x) return ''
      if (x.score == null) return `<b>${x.slot}</b><br/>该时点无快照（采集缺口）`
      return `<b>${x.slot}</b> ${sentiLabel(x.level)} <b>${x.score}</b> 分<br/>涨占比 ${x.up_ratio == null ? '—' : (x.up_ratio * 100).toFixed(1) + '%'} · 中位 ${x.median == null ? '—' : x.median + '%'}<br/>涨停 ${x.limit_up ?? '—'} / 跌停 ${x.limit_down ?? '—'}`
    } },
    xAxis: { type: 'category', data: slots, axisLabel: timeAxisLabel(slots), axisLine: { lineStyle: { color: T.line } }, axisTick: { show: false } },
    yAxis: { min: 0, max: 100, axisLabel: { color: T.dim, fontSize: 10 }, splitLine: { lineStyle: { color: T.line } } },
    // 按分值分档着色。dimension 不能写 0——那是 x 轴序号（会沿横轴切段而不是跟数值走）；
    // 不写即取最后一个维度（数值本身）
    visualMap: { show: false, pieces: [
      { lt: 25, color: T.down }, { gte: 25, lt: 45, color: T.warn },
      { gte: 45, lt: 70, color: T.series[3] }, { gte: 70, color: T.up }] },
    series: [{ type: 'line', data: track.map(x => x.score), symbol: 'circle', symbolSize: 4, lineStyle: { width: 1.6 },
      markLine: { silent: true, symbol: 'none', data: [{ yAxis: 45, lineStyle: { color: T.line, type: 'dashed', width: 1 }, label: { show: false } }, ...refLine] } }],
  }
}

/** 连板天梯（高板在上）。 */
export function ladderOption(lad) {
  if (!lad || !lad.length) return null
  const rows = [...lad].reverse()
  return {
    animation: false,
    grid: { left: 60, right: 60, top: 8, bottom: 20 },
    tooltip: { formatter: p => {
      const x = rows[p[0]?.dataIndex]
      return `<b>${x.lbc} 连板</b> ${x.n} 家<br/>${(x.names || []).join('、')}`
    } },
    xAxis: { type: 'value', axisLabel: { color: T.dim, fontSize: 10 }, splitLine: { lineStyle: { color: T.line } } },
    yAxis: { type: 'category', data: rows.map(x => x.lbc + '板'), axisLabel: { color: T.dim, fontSize: 11 }, axisLine: { lineStyle: { color: T.line } }, axisTick: { show: false } },
    series: [{ type: 'bar', data: rows.map(x => x.n), barWidth: 16, itemStyle: { color: T.up },
      label: { show: true, position: 'right', color: T.dim, fontSize: 10, formatter: p => {
        const x = rows[p.dataIndex]
        return (x.names || []).slice(0, 3).join('、') + (x.n > 3 ? ' …' : '')
      } } }],
  }
}

/** 连板高度 / 涨停家数 / 炸板率历史（zt_pool_daily）。 */
export function ztHistOption(rows) {
  if (!rows || !rows.length) return null
  const dates = rows.map(x => x.date.slice(5))
  return {
    animation: false,
    grid: { left: 44, right: 44, top: 26, bottom: 20 },
    legend: { top: 2, textStyle: { color: T.dim, fontSize: 10 }, itemWidth: 12, data: ['连板高度', '涨停家数', '炸板率'] },
    tooltip: { trigger: 'axis', formatter: p => {
      const x = rows[p[0]?.dataIndex]
      return `<b>${x.date}</b><br/>` + p.map(s => `${s.marker}${s.seriesName} ${s.seriesName === '炸板率' ? pctText(x.broken_rate) : s.seriesName === '连板高度' ? (x.max_lbc || 0) + ' 板' : x.limit_up + ' 家'}`).join('<br/>')
    } },
    xAxis: { type: 'category', data: dates, axisLabel: { color: T.dim, fontSize: 10, interval: Math.max(0, Math.floor(dates.length / 8)) }, axisLine: { lineStyle: { color: T.line } }, axisTick: { show: false } },
    yAxis: [
      { type: 'value', min: 0, axisLabel: { color: T.dim, fontSize: 10 }, splitLine: { lineStyle: { color: T.line } } },
      { type: 'value', min: 0, max: 100, axisLabel: { color: T.dim, fontSize: 10, formatter: '{value}%' }, splitLine: { show: false } },
    ],
    series: [
      { name: '涨停家数', type: 'bar', data: rows.map(x => x.limit_up), itemStyle: { color: T.up, opacity: 0.55 }, barWidth: '55%' },
      { name: '连板高度', type: 'line', data: rows.map(x => x.max_lbc), symbol: 'circle', symbolSize: 3, lineStyle: { width: 1.4, color: T.warn }, yAxisIndex: 0 },
      { name: '炸板率', type: 'line', data: rows.map(x => (x.broken_rate == null ? null : Number(x.broken_rate) * 100)), symbol: 'none', lineStyle: { width: 1, color: T.series[3], type: 'dashed' }, yAxisIndex: 1 },
    ],
  }
}

/** 板块雷达：按涨幅或主力净流入排序，取前 12 + 后 6（最差在最下、最好在最上）。 */
export function boardsOption(boards, dim) {
  if (!boards || !boards.length) return null
  const isNet = dim === 'net'
  const byDim = isNet
    ? [...boards].sort((a, b) => (b.main_net || 0) - (a.main_net || 0))
    : [...boards].sort((a, b) => (b.chg_pct || -999) - (a.chg_pct || -999))
  // ECharts category y 轴 index 0 渲染在底部
  const rows = [...byDim.slice(-6), ...byDim.slice(0, 12)]
  const vals = rows.map(x => (isNet ? yiNum(x.main_net) : (x.chg_pct || 0)))
  return {
    animation: false,
    grid: { left: 76, right: 46, top: 8, bottom: 18 },
    tooltip: { formatter: p => {
      const x = rows[p[0]?.dataIndex]
      return `<b>${x.name}</b> ${isNet ? yi(x.main_net) : signed(x.chg_pct) + '%'}<br/>涨 ${x.up} / 跌 ${x.down}` + (x.leader ? `<br/>领涨 ${x.leader}` : '')
    } },
    xAxis: { type: 'value', axisLabel: { color: T.dim, fontSize: 10 }, splitLine: { lineStyle: { color: T.line } } },
    yAxis: { type: 'category', data: rows.map(x => x.name), axisLabel: { color: T.dim, fontSize: 11 }, axisLine: { lineStyle: { color: T.line } }, axisTick: { show: false } },
    series: [{ type: 'bar', data: vals.map(v => ({ value: v, itemStyle: { color: v >= 0 ? T.up : T.down } })), barWidth: 12,
      label: { show: true, position: 'right', fontSize: 10, color: T.dim, formatter: p => (isNet ? yi(rows[p.dataIndex].main_net) : signed(rows[p.dataIndex].chg_pct) + '%') } }],
  }
}

/** 大盘主力资金分时：沪深两市当日累计净流入（主力 / 超大单 / 大单）。 */
export function fflowOption(series) {
  if (!series || !series.length) return null
  const ts = series.map(x => x.t)
  return {
    animation: false,
    grid: { left: 60, right: 16, top: 30, bottom: 22 },
    legend: { top: 2, textStyle: { color: T.dim, fontSize: 10 }, itemWidth: 12, data: ['主力', '超大单', '大单'] },
    tooltip: { trigger: 'axis', formatter: p => `${ts[p[0]?.dataIndex]}<br/>` + p.map(x => `${x.marker}${x.seriesName} ${Number(x.value).toFixed(1)}亿`).join('<br/>') },
    xAxis: minutesAxis(ts),
    yAxis: VALUE_Y({ axisLabel: { color: T.dim, fontSize: 10, formatter: axisCompact }, name: '亿', nameTextStyle: { color: T.faint } }),
    series: [
      { name: '主力', type: 'line', data: series.map(x => yiNum(x.main)), symbol: 'none', lineStyle: { width: 1.6, color: T.up },
        areaStyle: { opacity: 0.07, color: T.up }, markLine: { silent: true, symbol: 'none', data: [{ yAxis: 0, lineStyle: { color: T.line, width: 1 } }], label: { show: false } } },
      { name: '超大单', type: 'line', data: series.map(x => yiNum(x.huge)), symbol: 'none', lineStyle: { width: 1, color: T.series[1] } },
      { name: '大单', type: 'line', data: series.map(x => yiNum(x.big)), symbol: 'none', lineStyle: { width: 1, color: T.series[3] } },
    ],
  }
}
