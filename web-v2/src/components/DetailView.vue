<template>
<div>
  <n-space align="center" style="margin-bottom:8px">
    <n-button size="small" @click="$emit('back')">◀ 返回</n-button>
    <n-input v-model:value="code" placeholder="6位代码" style="width:150px" size="small" clearable @keyup.enter="load" />
    <n-button type="primary" size="small" @click="load">查询</n-button>
    <n-select v-model:value="adj" @update:value="reloadChart" size="small" style="width:105px" :options="adjOptions" />
  </n-space>

  <n-spin v-if="loading" />
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
          <table v-if="detail.fundamentals&&detail.fundamentals.industry" style="width:100%;border-collapse:collapse;font-size:12px">
            <tr><td style="width:85px;white-space:nowrap;padding:5px 8px;border:1px solid var(--c-border);color:var(--c-text-dim);font-size:11px">行业</td><td style="padding:5px 8px;border:1px solid var(--c-border);color:var(--c-text)">{{detail.fundamentals.industry||'-'}}</td></tr>
            <tr><td style="width:85px;white-space:nowrap;padding:5px 8px;border:1px solid var(--c-border);color:var(--c-text-dim);font-size:11px">PE(TTM)</td><td style="padding:5px 8px;border:1px solid var(--c-border)"><n-tag :type="detail.fundamentals.pe_ttm>0?(detail.fundamentals.pe_ttm<30?'success':'warning'):'error'" size="small" :bordered="false">{{detail.fundamentals.pe_ttm?detail.fundamentals.pe_ttm.toFixed(1):'-'}}</n-tag></td></tr>
            <tr><td style="width:85px;white-space:nowrap;padding:5px 8px;border:1px solid var(--c-border);color:var(--c-text-dim);font-size:11px">PB</td><td style="padding:5px 8px;border:1px solid var(--c-border);color:var(--c-text)">{{detail.fundamentals.pb_mrq?detail.fundamentals.pb_mrq.toFixed(2):'-'}}</td></tr>
            <tr><td style="width:85px;white-space:nowrap;padding:5px 8px;border:1px solid var(--c-border);color:var(--c-text-dim);font-size:11px">ROE</td><td style="padding:5px 8px;border:1px solid var(--c-border)"><n-tag :type="detail.fundamentals.roe>15?'success':detail.fundamentals.roe>5?'warning':'error'" size="small" :bordered="false">{{detail.fundamentals.roe?detail.fundamentals.roe.toFixed(1)+'%':'-'}}</n-tag></td></tr>
            <tr><td style="width:85px;white-space:nowrap;padding:5px 8px;border:1px solid var(--c-border);color:var(--c-text-dim);font-size:11px">营收同比</td><td style="padding:5px 8px;border:1px solid var(--c-border)"><n-tag :type="detail.fundamentals.revenue_yoy>0?'success':'error'" size="small" :bordered="false">{{detail.fundamentals.revenue_yoy!=null?detail.fundamentals.revenue_yoy.toFixed(1)+'%':'-'}}</n-tag></td></tr>
            <tr><td style="width:85px;white-space:nowrap;padding:5px 8px;border:1px solid var(--c-border);color:var(--c-text-dim);font-size:11px">净利同比</td><td style="padding:5px 8px;border:1px solid var(--c-border)"><n-tag :type="detail.fundamentals.profit_yoy>0?'success':'error'" size="small" :bordered="false">{{detail.fundamentals.profit_yoy!=null?detail.fundamentals.profit_yoy.toFixed(1)+'%':'-'}}</n-tag></td></tr>
          </table>
          <n-empty v-else-if="!loading" description="暂无基本面数据" style="padding:10px" />
        </div>
        <div>
          <h4 style="margin-bottom:6px;font-size:14px;color:var(--c-text)">📊 行情概览</h4>
          <table style="width:100%;border-collapse:collapse;font-size:12px">
            <tr><td style="width:85px;white-space:nowrap;padding:5px 8px;border:1px solid var(--c-border);color:var(--c-text-dim);font-size:11px">最新价</td><td style="padding:5px 8px;border:1px solid var(--c-border);color:var(--c-text)"><span style="font-weight:600">¥{{(detail.close||0).toFixed(2)}}</span></td></tr>
            <tr><td style="width:85px;white-space:nowrap;padding:5px 8px;border:1px solid var(--c-border);color:var(--c-text-dim);font-size:11px">后复权价</td><td style="padding:5px 8px;border:1px solid var(--c-border);color:var(--c-text)">¥{{detail.close_hfq?detail.close_hfq.toFixed(2):'-'}}</td></tr>
            <tr><td style="width:85px;white-space:nowrap;padding:5px 8px;border:1px solid var(--c-border);color:var(--c-text-dim);font-size:11px">成交量</td><td style="padding:5px 8px;border:1px solid var(--c-border);color:var(--c-text)">{{fmt(detail.volume)}}股</td></tr>
            <tr><td style="width:85px;white-space:nowrap;padding:5px 8px;border:1px solid var(--c-border);color:var(--c-text-dim);font-size:11px">换手率</td><td style="padding:5px 8px;border:1px solid var(--c-border);color:var(--c-text)">{{detail.turnover?detail.turnover.toFixed(2):'-'}}%</td></tr>
            <tr><td style="width:85px;white-space:nowrap;padding:5px 8px;border:1px solid var(--c-border);color:var(--c-text-dim);font-size:11px">数据日期</td><td style="padding:5px 8px;border:1px solid var(--c-border);color:var(--c-text)">{{detail.latest_trade_date}}</td></tr>
          </table>
        </div>
      </div>
    </div>
  </template>
</div>
</template>

<script setup>
import { ref, watch, onMounted, nextTick } from 'vue'
import { NCard, NButton, NInput, NSpace, NSpin, NTag, NEmpty, NDescriptions, NDescriptionsItem, NSelect } from 'naive-ui'
import axios from 'axios'
import * as echarts from 'echarts'

const props = defineProps({ code: String })
const emit = defineEmits(['back'])
const API = window.location.origin
const code = ref(props.code||'')
const loading = ref(false)
const detail = ref(null)
const hcnt = ref(0)
const adj = ref('none')
let lastKline = null
const dateRange = ref('')
const peData = ref([])
const peRange = ref('')

const adjOptions = [
  {value:'none',label:'不复权'},
  {value:'qfq',label:'前复权'},
  {value:'hfq',label:'后复权'},
]
const dirName = d => ({buy:'买入',sell:'卖出',neutral:'中性'}[d]||d)
const fmt = v => v!=null?Number(v).toLocaleString():'0'
const priceChg = ref(null)
const priceColor = ref('#fff')

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
  detail.value = null
  adj.value = 'none'
  try{
    const [r1, r2] = await Promise.all([
      axios.get(API+'/api/stock/'+code.value+'/detail'),
      axios.get(API+'/api/stock/'+code.value+'/kline?days=500&adjust='+adj.value),
    ])
    detail.value = r1.data; hcnt.value = r1.data.history_count
    if(r2.data.kline) lastKline = r2.data
  }catch(e){} finally { loading.value = false }
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
  const ohlc = kd.kline.map(d=>[d.open,d.close,d.low,d.high])
  const vols = kd.kline.map(d=>d.volume)
  const bmid = kd.kline.map(d=>d.boll_mid), bup = kd.kline.map(d=>d.boll_upper), blo = kd.kline.map(d=>d.boll_lower)
  const rs = kd.kline.map(d=>d.rsi), di = kd.kline.map(d=>d.dif), de = kd.kline.map(d=>d.dea), ba = kd.kline.map(d=>d.macd_bar)
  const vc = ohlc.map(d=>d[1]>=d[0]?'rgba(239,68,68,0.5)':'rgba(16,185,129,0.5)')
  const bc = ba.map(v=>v>=0?'rgba(239,68,68,0.6)':'rgba(16,185,129,0.6)')

  dateRange.value = dates[0]+' ~ '+dates[dates.length-1]
  const dz = [{type:'slider',xAxisIndex:0,start:82,end:100,height:22,bottom:4,handleSize:8,
    borderColor:'var(--c-input-bg)',
    backgroundColor:'var(--c-card-bg)',
    fillerColor:'rgba(96,165,250,0.15)',
    handleStyle:{borderColor:'var(--c-text-faint)',color:'var(--c-input-bg)'},
    textStyle:{color:'var(--c-text-faint)',fontSize:9},
    labelStyle:{color:'transparent'},
    moveHandleStyle:{color:'var(--c-card-bg-hover)'}
  }]
  const xA = {type:'category',data:dates,axisLabel:{show:false}}
  const tt = {trigger:'axis',axisPointer:{type:'cross'}}

  function make(id, opt){
    const el = document.getElementById(id)
    if(!el) return null
    if(el._echart) el._echart.dispose()
    const c = echarts.init(el)
    el._echart = c
    c.setOption(opt)
    return c
  }

  const c1 = make('c1', {
    tooltip:tt, grid:{left:'8%',right:'3%',top:18,bottom:50},
    xAxis:xA, yAxis:{scale:true,splitLine:{lineStyle:{color:'var(--c-border-light)'}}},
    dataZoom:dz,
    series:[
      {name:'K线',type:'candlestick',data:ohlc,itemStyle:{color:'#ef4444',color0:'#10b981',borderColor:'#ef4444',borderColor0:'#10b981'}},
      {name:'上轨',type:'line',data:bup,lineStyle:{color:'#f59e0b',width:1,type:'dashed'},symbol:'none'},
      {name:'中轨',type:'line',data:bmid,lineStyle:{color:'#60a5fa',width:1.5},symbol:'none'},
      {name:'下轨',type:'line',data:blo,lineStyle:{color:'#f59e0b',width:1,type:'dashed'},symbol:'none'}
    ]
  })
  const c2 = make('c2', {
    tooltip:tt, grid:{left:'8%',right:'3%',top:8,bottom:20},
    xAxis:xA, yAxis:{axisLabel:{fontSize:9,formatter:v=>(v/1e6).toFixed(0)+'M'},splitLine:{lineStyle:{color:'var(--c-border-light)'}}},
    dataZoom:dz,
    series:[{name:'量',type:'bar',data:vols,itemStyle:{color:p=>vc[p.dataIndex]}}]
  })
  const c3 = make('c3', {
    tooltip:tt, grid:{left:'8%',right:'3%',top:8,bottom:20},
    xAxis:xA, yAxis:{splitLine:{lineStyle:{color:'var(--c-border-light)'}}},
    dataZoom:dz,
    series:[
      {name:'柱',type:'bar',data:ba,itemStyle:{color:p=>bc[p.dataIndex]}},
      {name:'DIF',type:'line',data:di,lineStyle:{color:'#f59e0b',width:1},symbol:'none'},
      {name:'DEA',type:'line',data:de,lineStyle:{color:'#06b6d4',width:1},symbol:'none'}
    ]
  })
  const c4 = make('c4', {
    tooltip:tt, grid:{left:'8%',right:'3%',top:8,bottom:20},
    xAxis:xA, yAxis:{min:0,max:100,splitLine:{lineStyle:{color:'var(--c-border-light)'}}},
    dataZoom:dz,
    series:[{name:'RSI',type:'line',data:rs,lineStyle:{color:'#8b5cf6',width:1.5},symbol:'none',areaStyle:{color:'rgba(139,92,246,0.1)'},
      markLine:{silent:true,symbol:'none',data:[{yAxis:70,label:{formatter:'超买'},lineStyle:{color:'#ef4444',type:'dashed'}},{yAxis:30,label:{formatter:'超卖'},lineStyle:{color:'#10b981',type:'dashed'}}]}}
    ]
  })
  calcPriceChange(kd)
  const charts = [c1,c2,c3,c4].filter(Boolean)
  if(charts.length){charts.forEach(c=>c.group='s');echarts.connect('s')}
}

function drawPeChart(){
  const peEl = document.getElementById('c5')
  if(!peEl || !peData.value.length) return
  if(peEl._echart) peEl._echart.dispose()
  const c5 = echarts.init(peEl)
  peEl._echart = c5
  const peDates = peData.value.map(d=>d.date)
  const peVals = peData.value.map(d=>d.pe_ttm)
  const pctl = peData.value.map(d=>d.pe_percentile)
  peRange.value = peDates[0]+' ~ '+peDates[peDates.length-1]
  c5.setOption({
    tooltip:{trigger:'axis',axisPointer:{type:'cross'}},
    grid:{left:'8%',right:'3%',top:8,bottom:50},
    xAxis:{type:'category',data:peDates,axisLabel:{fontSize:9,rotate:30},splitLine:{lineStyle:{color:'var(--c-border-light)'}}},
    yAxis:[
      {type:'value',name:'PE',splitLine:{lineStyle:{color:'var(--c-border-light)'}}},
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
      {name:'PE(TTM)',type:'line',data:peVals,lineStyle:{color:'#60a5fa',width:1.5},symbol:'none',areaStyle:{color:'rgba(96,165,250,0.1)'}},
      {name:'分位%',type:'line',yAxisIndex:1,data:pctl,lineStyle:{color:'#f59e0b',width:1,type:'dashed'},symbol:'none'}
    ]
  })
}

async function reloadChart(){
  try{
    const r = await axios.get(API+'/api/stock/'+code.value+'/kline?days=500&adjust='+adj.value)
    if(r.data.kline) lastKline = r.data
  }catch(e){} finally { if(lastKline){ await nextTick(); drawCharts(lastKline) } }
}

watch(()=>props.code, v=>{if(v){code.value=v;load()}})
onMounted(()=>{if(code.value) load()})
</script>
