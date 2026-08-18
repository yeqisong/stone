<template>
<div>
  <n-space align="center" style="margin-bottom:8px">
    <n-button size="small" @click="$emit('back')">◀ 返回</n-button>
    <n-input v-model:value="code" placeholder="6位代码" style="width:150px" size="small" clearable @keyup.enter="load" />
    <n-button type="primary" size="small" @click="load">查询</n-button>
    <n-select v-model:value="adj" @update:value="reloadChart" size="small" style="width:105px" :options="adjOptions" />
    <n-button size="small" :disabled="!prevCode" @click="jump(prevCode)">◀ 上一只</n-button>
    <n-button size="small" :disabled="!nextCode" @click="jump(nextCode)">下一只 ▶</n-button>
  </n-space>

  <n-spin v-if="loading" />
  <n-empty v-else-if="notFound" description="未找到该证券，请检查代码" style="padding:40px" />
  <template v-else-if="detail">
    <!-- Summary Cards - unified stat-row style -->
    <div style="display:flex;gap:8px;justify-content:center;padding:6px 0 10px;flex-wrap:wrap">
      <div style="text-align:center;min-width:70px"><div style="font-size:10px;color:var(--c-text-dim)">{{detail.stock_code}}</div><div style="font-size:20px;font-weight:700;color:var(--c-text)">{{detail.stock_name}}</div></div>
      <div style="text-align:center;min-width:70px"><div style="font-size:10px;color:var(--c-text-dim)">最新价</div><div style="font-size:20px;font-weight:700" :style="{color:priceColor}">¥{{(detail.close||0).toFixed(2)}} <span v-if="priceChg!=null" style="font-size:11px;font-weight:400">{{priceChg>=0?'+':''}}{{priceChg.toFixed(2)}}%</span></div></div>
      <div style="text-align:center;min-width:70px"><div style="font-size:10px;color:var(--c-text-dim)">数据日期</div><div style="font-size:20px;font-weight:700;color:var(--c-text)">{{detail.latest_trade_date}}</div></div>
      <div style="text-align:center;min-width:70px"><div style="font-size:10px;color:var(--c-text-dim)">历史信号</div><div style="font-size:20px;font-weight:700;color:var(--c-text)">{{hcnt}}</div></div>
    </div>

    <!-- Strategy Signals -->
    <div style="margin-bottom:12px;min-height:50px">
      <h4 style="margin-bottom:6px;font-size:14px;color:var(--c-text)">🎯 策略信号 ({{detail.latest_signal_date||detail.latest_trade_date}})</h4>
      <div v-if="detail.latest_signals&&detail.latest_signals.length" style="display:flex;gap:8px;flex-wrap:wrap">
        <div v-for="s in detail.latest_signals" :key="s.strategy_name" style="flex:1;min-width:200px;border-radius:8px;padding:10px 12px;background:var(--c-card-bg-hover);border:1px solid var(--c-border)">
          <div style="display:flex;align-items:center;gap:6px;margin-bottom:4px">
            <n-tag :type="s.direction==='buy'?'error':s.direction==='sell'?'success':'default'" size="small" :bordered="false">{{dirName(s.direction)}}</n-tag>
            <span v-if="s.combined_signal" style="color:var(--n-color-target);font-size:13px;font-weight:600">★{{s.strength}}</span>
            <span v-else style="font-size:12px">{{'★'.repeat(s.strength)}}</span>
          </div>
          <div style="font-size:12px;line-height:1.5;color:var(--c-text)">{{s.reason}}</div>
          <div v-if="s.model_version" style="font-size:10px;color:var(--c-text-faint);margin-top:2px">📦 {{s.model_version}}</div>
        </div>
      </div>
      <n-empty v-else description="暂无信号" style="padding:10px" />
    </div>

    <!-- Left-Right Layout -->
    <div style="display:flex;gap:12px;flex-wrap:wrap">
      <div style="flex:1;min-width:320px">
        <div style="margin-bottom:12px">
          <h4 style="margin-bottom:4px;font-size:14px;color:var(--c-text)">📈 K线图 <span style="font-size:11px;color:var(--c-text-dim)">{{dateRange}}</span></h4>
          <div :id="'c1'" style="width:100%;height:340px"></div>
        </div>
        <div style="margin-bottom:12px">
          <h4 style="margin-bottom:4px;font-size:14px;color:var(--c-text)">📈 均线 (MA5/10/20/30/60/120/180)</h4>
          <div :id="'c6'" style="width:100%;height:180px"></div>
        </div>
        <div style="margin-bottom:12px">
          <h4 style="margin-bottom:4px;font-size:14px;color:var(--c-text)">📊 成交量</h4>
          <div :id="'c2'" style="width:100%;height:160px"></div>
        </div>
        <div style="margin-bottom:12px">
          <h4 style="margin-bottom:4px;font-size:14px;color:var(--c-text)">📉 MACD</h4>
          <div :id="'c3'" style="width:100%;height:160px"></div>
        </div>
        <div style="margin-bottom:12px">
          <h4 style="margin-bottom:4px;font-size:14px;color:var(--c-text)">📐 RSI</h4>
          <div :id="'c4'" style="width:100%;height:160px"></div>
        </div>
        <div style="margin-bottom:12px">
          <h4 style="margin-bottom:4px;font-size:14px;color:var(--c-text)">📊 PE历史分位 <span style="font-size:11px;color:var(--c-text-dim)">{{peRange}}</span></h4>
          <div :id="'c5'" style="width:100%;height:160px"></div>
        </div>
      </div>

      <!-- Right: Fundamentals & Overview -->
      <div style="width:100%;max-width:320px;display:flex;flex-direction:column;gap:12px" class="detail-sidebar">
        <div>
          <h4 style="margin-bottom:6px;font-size:14px;color:var(--c-text)">🏢 基本面</h4>
          <!-- v-if 判断对象存在即可：industry 可能为空，不能因此隐藏整块 -->
          <table v-if="detail.fundamentals!=null&&Object.keys(detail.fundamentals).length" style="width:100%;border-collapse:collapse;font-size:12px">
            <tr v-for="row in fundRows" :key="row.lbl" style="background:var(--c-card-bg)">
              <td style="width:85px;white-space:nowrap;padding:4px 6px;border:1px solid var(--c-border);color:var(--c-text-dim);font-size:11px;text-align:left">{{row.lbl}}</td>
              <td style="padding:4px 6px;border:1px solid var(--c-border);color:var(--c-text);text-align:left">{{row.val}}</td>
            </tr>
          </table>
          <n-empty v-else-if="!loading" description="暂无基本面数据" style="padding:10px" />
        </div>
        <div>
          <h4 style="margin-bottom:6px;font-size:14px;color:var(--c-text)">📊 行情概览</h4>
          <table style="width:100%;border-collapse:collapse;font-size:12px">
            <tr><td style="width:85px;white-space:nowrap;padding:5px 8px;border:1px solid var(--c-border);color:var(--c-text-dim);font-size:11px">日期</td><td style="padding:5px 8px;border:1px solid var(--c-border);color:var(--c-text)" :style="{background:hoverInfo?'rgba(32,128,240,0.06)':'transparent'}">{{hoverInfo?hoverInfo.date:detail.latest_trade_date}}</td></tr>
            <tr><td style="width:85px;white-space:nowrap;padding:5px 8px;border:1px solid var(--c-border);color:var(--c-text-dim);font-size:11px">开盘</td><td style="padding:5px 8px;border:1px solid var(--c-border);color:var(--c-text)">¥{{hoverInfo?hoverInfo.open.toFixed(2):(detail.open||0).toFixed(2)}}</td></tr>
            <tr><td style="width:85px;white-space:nowrap;padding:5px 8px;border:1px solid var(--c-border);color:var(--c-text-dim);font-size:11px">最高</td><td style="padding:5px 8px;border:1px solid var(--c-border)" :style="{color:hoverInfo?'#ef4444':'var(--c-text)'}">¥{{hoverInfo?hoverInfo.high.toFixed(2):(detail.high||0).toFixed(2)}}</td></tr>
            <tr><td style="width:85px;white-space:nowrap;padding:5px 8px;border:1px solid var(--c-border);color:var(--c-text-dim);font-size:11px">最低</td><td style="padding:5px 8px;border:1px solid var(--c-border)" :style="{color:hoverInfo?'#10b981':'var(--c-text)'}">¥{{hoverInfo?hoverInfo.low.toFixed(2):(detail.low||0).toFixed(2)}}</td></tr>
            <tr><td style="width:85px;white-space:nowrap;padding:5px 8px;border:1px solid var(--c-border);color:var(--c-text-dim);font-size:11px">收盘</td><td style="padding:5px 8px;border:1px solid var(--c-border)"><span style="font-weight:600" :style="{color:hoverInfo?(hoverInfo.close>=hoverInfo.prevClose?'#ef4444':'#10b981'):priceColor}">¥{{hoverInfo?hoverInfo.close.toFixed(2):(detail.close||0).toFixed(2)}}</span><span v-if="hoverInfo&&hoverInfo.prevClose" style="font-size:10px;margin-left:4px" :style="{color:hoverInfo.close>=hoverInfo.prevClose?'#ef4444':'#10b981'}">{{((hoverInfo.close-hoverInfo.prevClose)/hoverInfo.prevClose*100).toFixed(2)}}%</span><span v-else-if="priceChg!=null" style="font-size:10px;margin-left:4px" :style="{color:priceColor}">{{priceChg>=0?'+':''}}{{priceChg.toFixed(2)}}%</span></td></tr>
            <tr><td style="width:85px;white-space:nowrap;padding:5px 8px;border:1px solid var(--c-border);color:var(--c-text-dim);font-size:11px">成交量</td><td style="padding:5px 8px;border:1px solid var(--c-border);color:var(--c-text)">{{hoverInfo?fmt(hoverInfo.volume)+'股':fmt(detail.volume)+'股'}}</td></tr>
            <tr><td style="width:85px;white-space:nowrap;padding:5px 8px;border:1px solid var(--c-border);color:var(--c-text-dim);font-size:11px">成交额</td><td style="padding:5px 8px;border:1px solid var(--c-border);color:var(--c-text)">{{hoverInfo&&hoverInfo.amount?fmt(hoverInfo.amount)+'元':(detail.amount?fmt(detail.amount)+'元':'-')}}</td></tr>
            <tr><td style="width:85px;white-space:nowrap;padding:5px 8px;border:1px solid var(--c-border);color:var(--c-text-dim);font-size:11px">换手率</td><td style="padding:5px 8px;border:1px solid var(--c-border);color:var(--c-text)">{{(hoverInfo&&hoverInfo.turnover!=null?hoverInfo.turnover:(detail.turnover!=null?detail.turnover:null))!=null ? (hoverInfo&&hoverInfo.turnover!=null?hoverInfo.turnover:detail.turnover).toFixed(2)+'%':'—'}}</td></tr>
          </table>
        </div>
      </div>
    </div>
  </template>
</div>
</template>

<script setup>
import { ref, computed, watch, onMounted, onUnmounted, nextTick } from 'vue'
import { NCard, NButton, NInput, NSpace, NSpin, NTag, NEmpty, NDescriptions, NDescriptionsItem, NSelect } from 'naive-ui'
import axios from 'axios'
import * as echarts from 'echarts'
import { useNavStore } from '../stores/nav'

const props = defineProps({ code: String })
const emit = defineEmits(['back'])
const API = window.location.origin
const _nav = useNavStore()
const code = ref(props.code||'')
const loading = ref(false)
const detail = ref(null)
const notFound = ref(false)
const hcnt = ref(0)
// 复权记忆：记住用户上次选择
const adj = ref(localStorage.getItem('detail_adj') || 'none')
let lastKline = null
const dateRange = ref('')
const peData = ref([])
const peRange = ref('')
// 上下只：从列表页跳转时写入 localStorage 的最近列表导航上下文
const prevCode = ref(null)
const nextCode = ref(null)
let navList = []
function loadNavList() {
  try { navList = JSON.parse(localStorage.getItem('detail_nav_list') || '[]') || [] } catch (e) { navList = [] }
}
function updateNav() {
  loadNavList()
  prevCode.value = null; nextCode.value = null
  if (!navList.length) return
  const i = navList.indexOf(code.value)
  if (i > 0) prevCode.value = navList[i - 1]
  if (i >= 0 && i < navList.length - 1) nextCode.value = navList[i + 1]
}
function jump(c) { if (c) { _nav.dcode = c; _nav.syncHash(); code.value = c; load() } }
// 供列表页调用：记录当前列表代码序列
window.__setDetailNavList = (codes) => { localStorage.setItem('detail_nav_list', JSON.stringify(codes || [])) }

const fundRows = computed(() => {
  const f = detail.value?.fundamentals
  if (!f || !Object.keys(f).length) return []
  const Y = v => v != null ? v.toLocaleString() : '—'
  const P = v => v != null ? Number(v).toFixed(2) : '—'
  const B = (v, d = 0) => v != null ? (v / 1e8).toFixed(d) + '亿' : '—'
  return [
    { lbl:'行业', val: f.industry || '—' },
    { lbl:'PE(TTM)', val: f.pe_ttm != null ? P(f.pe_ttm) : '—' },
    { lbl:'PE', val: f.pe != null ? P(f.pe) : '—' },
    { lbl:'PB', val: f.pb_mrq != null ? P(f.pb_mrq) : '—' },
    { lbl:'PS(TTM)', val: f.ps_ttm != null ? P(f.ps_ttm) : '—' },
    { lbl:'PS', val: f.ps != null ? P(f.ps) : '—' },
    { lbl:'ROE', val: f.roe != null ? f.roe.toFixed(2) + '%' : '—' },
    { lbl:'营收同比', val: f.revenue_yoy != null ? f.revenue_yoy.toFixed(1) + '%' : '—' },
    { lbl:'净利同比', val: f.profit_yoy != null ? f.profit_yoy.toFixed(1) + '%' : '—' },
    { lbl:'股息率', val: f.dv_ratio != null ? f.dv_ratio.toFixed(2) + '%' : '—' },
    { lbl:'股息TTM', val: f.dv_ttm != null ? f.dv_ttm.toFixed(2) + '%' : '—' },
    { lbl:'换手率', val: f.turnover_rate != null ? f.turnover_rate.toFixed(2) + '%' : '—' },
    { lbl:'量比', val: f.volume_ratio != null ? P(f.volume_ratio) : '—' },
    { lbl:'总市值', val: B(f.market_cap, 2) },
    { lbl:'流通市值', val: B(f.circ_mv, 2) },
    { lbl:'总股本', val: B(f.total_shares, 2) },
    { lbl:'流通股本', val: B(f.float_share, 2) },
    { lbl:'自由流通', val: B(f.free_share, 2) },
    { lbl:'注册资本', val: f.reg_capital != null ? Number(f.reg_capital).toFixed(1) + '万元' : '—' },
    { lbl:'员工', val: f.employees != null ? Y(f.employees) : '—' },
    { lbl:'主营', val: f.main_business || '—' },
  ]
})

const adjOptions = [
  {value:'none',label:'不复权'},
  {value:'qfq',label:'前复权'},
  {value:'hfq',label:'后复权'},
]
const dirName = d => ({buy:'买入',sell:'卖出',neutral:'中性'}[d]||d)
const fmt = v => v!=null?Number(v).toLocaleString():'0'
const priceChg = ref(null)
const priceColor = ref('#fff')
const hoverInfo = ref(null)  // crosshair hover 时动态更新的行情数据

function calcPriceChange(kd){
  if(!kd||!kd.kline||kd.kline.length<2) { priceChg.value=null; priceColor.value='#fff'; return }
  const kl = kd.kline
  const last = kl[kl.length-1], prev = kl[kl.length-2]
  if(last.close && prev.close){
    priceChg.value = (last.close - prev.close) / prev.close * 100
    priceColor.value = priceChg.value >= 0 ? '#ef4444' : '#10b981'
  }
}

async function load(){
  if(!code.value) return
  loading.value = true
  notFound.value = false
  detail.value = null
  hoverInfo.value = null
  updateNav()
  try{
    const [r1, r2] = await Promise.all([
      axios.get(API+'/api/stock/'+code.value+'/detail'),
      axios.get(API+'/api/stock/'+code.value+'/kline?days=500&adjust='+adj.value),
    ])
    detail.value = r1.data; hcnt.value = r1.data.history_count
    if(r2.data.kline) lastKline = r2.data
  }catch(e){
    if(e.response?.status===404) notFound.value = true
  } finally { loading.value = false }
  // PE data (best-effort, 失败不影响 main charts)
  await nextTick()
  drawCharts(lastKline)
  try{
    const r=await axios.get(API+'/api/stock/'+code.value+'/pe_history')
    if(r.data.data&&r.data.data.length){peData.value=r.data.data;nextTick(()=>drawPeChart())}
  }catch(e){} 
}

function drawCharts(kd){
  const dates = kd.kline.map(d=>d.trade_date)
  const closes = kd.kline.map(d=>d.close)
  const ohlc = kd.kline.map(d=>[d.open,d.close,d.low,d.high])
  const vols = kd.kline.map(d=>d.volume)
  const amts = kd.kline.map(d=>d.amount)
  const trns = kd.kline.map(d=>d.turnover)
  const bmid = kd.kline.map(d=>d.boll_mid), bup = kd.kline.map(d=>d.boll_upper), blo = kd.kline.map(d=>d.boll_lower)
  const rs = kd.kline.map(d=>d.rsi), di = kd.kline.map(d=>d.dif), de = kd.kline.map(d=>d.dea), ba = kd.kline.map(d=>d.macd_bar)
  // 均线 MA5/10/20/30/60/120/180（前端按 close 计算，前 N-1 点为 null）
  const MA = (n) => closes.map((_, i) => { if (i < n - 1) return null; let s = 0; for (let j = 0; j < n; j++) s += closes[i - j]; return +(s / n).toFixed(2); })
  const ma5 = MA(5), ma10 = MA(10), ma20 = MA(20), ma30 = MA(30), ma60 = MA(60), ma120 = MA(120), ma180 = MA(180)
  const vc = ohlc.map(d=>d[1]>=d[0]?'rgba(239,68,68,0.85)':'rgba(16,185,129,0.85)')
  const bc = ba.map(v=>v>=0?'rgba(239,68,68,0.85)':'rgba(16,185,129,0.85)')
  // 浅灰色网格线（比默认的 --c-border-light 更浅）
  const gl = {lineStyle:{color:'rgba(128,128,128,0.1)'}}

  dateRange.value = dates[0]+' ~ '+dates[dates.length-1]
  // 默认显示最近 1 年（约 250 交易日；不足则全显示）
  const totalDays = dates.length
  const SHOW = totalDays <= 250 ? 0 : ((totalDays - 250) / totalDays * 100).toFixed(1)
  const dz = [{type:'slider',xAxisIndex:0,start:SHOW,end:100,height:22,bottom:4,handleSize:8,
    borderColor:'var(--c-input-bg)',
    backgroundColor:'var(--c-card-bg)',
    fillerColor:'rgba(96,165,250,0.15)',
    handleStyle:{borderColor:'var(--c-text-faint)',color:'var(--c-input-bg)'},
    textStyle:{color:'var(--c-text-faint)',fontSize:9},
    labelStyle:{color:'transparent'},
    moveHandleStyle:{color:'var(--c-card-bg-hover)'}
  }]
  const xA = {type:'category',data:dates,axisLabel:{show:false},
    axisLine:{lineStyle:{color:'rgba(128,128,128,0.15)'}},
    axisTick:{show:false}}
  const tt = {trigger:'axis',axisPointer:{type:'cross'}}

  // 实例复用：已存在则 setOption(notMerge) 更新，避免每次 dispose+init 卡顿
  function make(id, opt){
    const el = document.getElementById(id)
    if(!el) return null
    let c = el._echart
    if(!c){ c = echarts.init(el); el._echart = c }
    c.setOption(opt, { notMerge: true })
    return c
  }

  const tooltipFmt = p => {
    if(!p||!p.length) return ''
    const d = p[0]; const idx = d.dataIndex; const o = ohlc[idx]
    if(!o) return ''
    const chg = o[1] && ohlc[idx-1] ? ((o[1]-ohlc[idx-1][1])/ohlc[idx-1][1]*100).toFixed(2) : '—'
    const color = chg>=0?'#ef4444':'#10b981'
    const mid = bmid[idx], up = bup[idx], lo = blo[idx]
    return `<div style="font-size:12px"><b>${dates[idx]}</b><br/>
      开: ${o[0].toFixed(2)}  收: <span style="color:${color}">${o[1].toFixed(2)}</span> (${chg}%)<br/>
      高: ${o[3].toFixed(2)}  低: ${o[2].toFixed(2)}  量: ${(vols[idx]/1e6).toFixed(1)}M<br/>
      <span style="color:#f59e0b">BOLL上轨: ${up!=null?up.toFixed(2):'—'}</span>
      <span style="color:#60a5fa"> 中轨: ${mid!=null?mid.toFixed(2):'—'}</span>
      <span style="color:#f59e0b"> 下轨: ${lo!=null?lo.toFixed(2):'—'}</span></div>`
  }

  const c1 = make('c1', {
    tooltip:{trigger:'axis',axisPointer:{type:'cross'},formatter:tooltipFmt},
    grid:{left:'8%',right:'3%',top:18,bottom:30},
    xAxis:xA, yAxis:{scale:true,splitLine:gl},
    dataZoom:dz,
    series:[
      {name:'K线',type:'candlestick',data:ohlc,itemStyle:{color:'#ef4444',color0:'#10b981',borderColor:'#ef4444',borderColor0:'#10b981'}},
      {name:'上轨',type:'line',data:bup,lineStyle:{color:'#f59e0b',width:2},symbol:'none',smooth:true},
      {name:'中轨',type:'line',data:bmid,lineStyle:{color:'#60a5fa',width:2},symbol:'none',smooth:true},
      {name:'下轨',type:'line',data:blo,lineStyle:{color:'#f59e0b',width:2},symbol:'none',smooth:true}
    ]
  })
  const c2 = make('c2', {
    tooltip:tt, grid:{left:'8%',right:'3%',top:8,bottom:30},
    xAxis:xA, yAxis:{axisLabel:{fontSize:9,formatter:v=>(v/1e6).toFixed(0)+'M'},splitLine:gl},
    dataZoom:dz,
    series:[{name:'量',type:'bar',data:vols,itemStyle:{color:p=>vc[p.dataIndex]}}]
  })
  const c3 = make('c3', {
    tooltip:tt, grid:{left:'8%',right:'3%',top:8,bottom:30},
    xAxis:xA, yAxis:{splitLine:gl},
    dataZoom:dz,
    series:[
      {name:'柱',type:'bar',data:ba,itemStyle:{color:p=>bc[p.dataIndex]}},
      {name:'DIF',type:'line',data:di,lineStyle:{color:'#f59e0b',width:1},symbol:'none',smooth:true},
      {name:'DEA',type:'line',data:de,lineStyle:{color:'#06b6d4',width:1},symbol:'none',smooth:true}
    ]
  })
  const c4 = make('c4', {
    tooltip:tt, grid:{left:'8%',right:'3%',top:8,bottom:30},
    xAxis:xA, yAxis:{min:0,max:100,splitLine:gl},
    dataZoom:dz,
    series:[{name:'RSI',type:'line',data:rs,lineStyle:{color:'#8b5cf6',width:1.5},symbol:'none',smooth:true,areaStyle:{color:'rgba(139,92,246,0.1)'},
      markLine:{silent:true,symbol:'none',data:[{yAxis:70,label:{formatter:'超买'},lineStyle:{color:'#ef4444',type:'dashed'}},{yAxis:30,label:{formatter:'超卖'},lineStyle:{color:'#10b981',type:'dashed'}}]}}
    ]
  })
  calcPriceChange(kd)
  // crosshair 交互：hover K 线时更新动态行情数据（含成交额/换手）
  if(c1){
    c1.on('mousemove', p=>{
      if(p.dataIndex!=null){
        const o = ohlc[p.dataIndex]
        hoverInfo.value = { date: dates[p.dataIndex], open: o[0], close: o[1], low: o[2], high: o[3], volume: vols[p.dataIndex], amount: amts[p.dataIndex] ?? null, turnover: trns[p.dataIndex] ?? null, prevClose: p.dataIndex>0 ? ohlc[p.dataIndex-1][1] : null }
      }
    })
    c1.on('mouseout', ()=>{ hoverInfo.value = null })
  }
  // 均线图（MA5/10/20/60，与 K 线联动 zoom/十字线）
  const c6 = make('c6', {
    tooltip: tt,
    grid:{left:'8%',right:'3%',top:18,bottom:30},
    xAxis: { ...xA, axisLabel: { show: true, fontSize: 9, interval: 'auto' } },
    yAxis:{scale:true,splitLine:gl},
    dataZoom:dz,
    series:[
      {name:'MA5',type:'line',data:ma5,lineStyle:{color:'#ef4444',width:1},symbol:'none',smooth:true},
      {name:'MA10',type:'line',data:ma10,lineStyle:{color:'#f59e0b',width:1},symbol:'none',smooth:true},
      {name:'MA20',type:'line',data:ma20,lineStyle:{color:'#06b6d4',width:1},symbol:'none',smooth:true},
      {name:'MA30',type:'line',data:ma30,lineStyle:{color:'#10b981',width:1},symbol:'none',smooth:true},
      {name:'MA60',type:'line',data:ma60,lineStyle:{color:'#8b5cf6',width:1},symbol:'none',smooth:true},
      {name:'MA120',type:'line',data:ma120,lineStyle:{color:'#ec4899',width:1},symbol:'none',smooth:true},
      {name:'MA180',type:'line',data:ma180,lineStyle:{color:'#64748b',width:1},symbol:'none',smooth:true},
    ]
  })
  const charts = [c1,c2,c3,c4,c6].filter(Boolean)
  if(charts.length){charts.forEach(c=>c.group='s');echarts.connect('s')}
}

function drawPeChart(){
  const peEl = document.getElementById('c5')
  if(!peEl || !peData.value.length) return
  // 实例复用
  let c5 = peEl._echart
  if(!c5){ c5 = echarts.init(peEl); peEl._echart = c5 }
  const peDates = peData.value.map(d=>d.date)
  const peVals = peData.value.map(d=>d.pe_ttm)
  const pctl = peData.value.map(d=>d.pe_percentile)
  peRange.value = peDates[0]+' ~ '+peDates[peDates.length-1]
  c5.setOption({
    tooltip:{trigger:'axis',axisPointer:{type:'cross'}},
    grid:{left:'8%',right:'3%',top:8,bottom:50},
    xAxis:{type:'category',data:peDates,axisLabel:{fontSize:9,rotate:30,interval:'auto'},splitLine:{lineStyle:{color:'rgba(128,128,128,0.1)'}}},
    yAxis:[
      {type:'value',name:'PE',splitLine:{lineStyle:{color:'rgba(128,128,128,0.1)'}}},
      {type:'value',name:'%',min:0,max:100,splitLine:{show:false}}
    ],
    dataZoom:[{type:'slider',start:0,end:100,height:22,bottom:4,handleSize:8,
      borderColor:'var(--c-input-bg)',backgroundColor:'var(--c-card-bg)',
      fillerColor:'rgba(96,165,250,0.15)',
      handleStyle:{borderColor:'var(--c-text-faint)',color:'var(--c-input-bg)'},
      textStyle:{color:'var(--c-text-faint)'},labelStyle:{color:'transparent'},
      moveHandleStyle:{color:'var(--c-card-bg-hover)'}
    }],
    series:[
      {name:'PE(TTM)',type:'line',data:peVals,lineStyle:{color:'#60a5fa',width:1.5},symbol:'none',smooth:true,areaStyle:{color:'rgba(96,165,250,0.1)'}},
      {name:'分位%',type:'line',yAxisIndex:1,data:pctl,lineStyle:{color:'#f59e0b',width:1,type:'dashed'},symbol:'none',smooth:true}
    ]
  })
}

async function reloadChart(){
  localStorage.setItem('detail_adj', adj.value)
  try{
    const r = await axios.get(API+'/api/stock/'+code.value+'/kline?days=500&adjust='+adj.value)
    if(r.data.kline) lastKline = r.data
  }catch(e){} finally { if(lastKline){ await nextTick(); drawCharts(lastKline) } }
}

// 窗口 resize 时自适应所有图表
function resizeAll(){ ['c1','c2','c3','c4','c5','c6'].forEach(id => { const el = document.getElementById(id); if (el && el._echart) el._echart.resize() }) }
let _resizeHandler = null
onMounted(() => {
  _resizeHandler = window.addEventListener ? window.addEventListener('resize', resizeAll) : null
  if(code.value) load()
})
onUnmounted(() => { if (_resizeHandler && window.removeEventListener) window.removeEventListener('resize', resizeAll) })

watch(()=>props.code, v=>{if(v && v!==code.value){code.value=v;load()}})
</script>
