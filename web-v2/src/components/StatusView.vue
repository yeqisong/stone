<template>
<n-space vertical size="medium">
  <!-- Overview Cards -->
  <n-grid :cols="4" :x-gap="10">
    <n-gi><n-card size="small"><div style="text-align:center">
      <div style="font-size:22px;font-weight:700">{{fmt(overview.total_rows)}}</div>
      <div style="font-size:11px;color:rgba(255,255,255,.45)">行情总条数</div>
    </div></n-card></n-gi>
    <n-gi><n-card size="small"><div style="text-align:center">
      <div style="font-size:22px;font-weight:700">{{overview.total_stocks}}</div>
      <div style="font-size:11px;color:rgba(255,255,255,.45)">股票</div>
    </div></n-card></n-gi>
    <n-gi><n-card size="small"><div style="text-align:center">
      <div style="font-size:22px;font-weight:700">{{overview.latest_date||'-'}}</div>
      <div style="font-size:11px;color:rgba(255,255,255,.45)">最新数据</div>
    </div></n-card></n-gi>
    <n-gi><n-card size="small" :style="missingDates.length>0?'border-color:#f59e0b':''"><div style="text-align:center">
      <div style="font-size:22px;font-weight:700" :style="{color:missingDates.length>0?'#f59e0b':'inherit'}">{{missingDates.length}}</div>
      <div style="font-size:11px;color:rgba(255,255,255,.45)">漏数据日期</div>
    </div></n-card></n-gi>
  </n-grid>

  <!-- Today Strategy -->
  <n-card v-if="todayStrategy" size="small" :style="{borderLeft:todayStrategy.status==='ok'?'3px solid #10b981':'3px solid #f59e0b'}">
    <template #header><span style="color:#fff;font-size:14px">🎯 今日策略运行 ({{todayStrategy.strategy_date||'-'}})</span></template>
    <n-space size="large">
      <div><span style="color:rgba(255,255,255,.45)">扫描:</span> <b style="color:#fff">{{todayStrategy.scanned||0}} 只</b></div>
      <div><span style="color:rgba(255,255,255,.45)">信号:</span> <b style="color:#fff">{{todayStrategy.total_signals||0}}</b></div>
      <div><span style="color:rgba(255,255,255,.45)">买入:</span> <b style="color:#ef4444">{{todayStrategy.buy_signals||0}}</b></div>
      <div><span style="color:rgba(255,255,255,.45)">偏好:</span> <b style="color:#fff">{{todayStrategy.preference||'-'}}</b></div>
      <div><span style="color:rgba(255,255,255,.45)">耗时:</span> <b style="color:#fff">{{todayStrategy.elapsed_seconds||0}}s</b></div>
    </n-space>
  </n-card>

  <!-- Exchanges -->
  <n-card size="small">
    <template #header><span style="color:#fff;font-size:14px">📊 交易所</span></template>
    <n-data-table :columns="exColumns" :data="exchangeRows" size="small" />
  </n-card>

  <!-- Calendar -->
  <n-card size="small">
    <template #header><span style="color:#fff;font-size:14px">📅 交易日历 & 数据完整度</span></template>
    <div style="display:flex;align-items:center;gap:6px;margin-bottom:10px">
      <n-button size="small" @click="prevMonth">◀</n-button>
      <span style="font-weight:600;font-size:14px;color:#fff;min-width:80px;text-align:center">{{monthLabel}}</span>
      <n-button size="small" @click="nextMonth">▶</n-button>
      <n-button size="small" @click="goToday">今天</n-button>
      <span style="font-size:10px;color:rgba(255,255,255,.45)">🟢≥80% 🟡50-80% 🔴&lt;50% ⚫非交易日</span>
    </div>
    <div style="display:grid;grid-template-columns:repeat(7,1fr);gap:3px;font-size:10px;color:rgba(255,255,255,.45);margin-bottom:4px;text-align:center">
      <span>一</span><span>二</span><span>三</span><span>四</span><span>五</span><span style="color:#ef4444">六</span><span style="color:#ef4444">日</span>
    </div>
    <div style="display:grid;grid-template-columns:repeat(7,1fr);gap:3px">
      <div v-for="d in cal" :key="d.date" :style="calStyle(d)" :title="calTitle(d)" @click="doSyncClick(d)" @contextmenu.prevent="confirmSync(d)" style="transition:all .12s" :class="{calSync:d.syncing}">
        <span v-if="d.day!==null">{{d.day}}</span>
        <div v-if="d.td&&d.cp" :style="{fontSize:'7px',marginTop:'1px',color:d.cp.pct>=80?'#10b981':d.cp.pct>=50?'#f59e0b':'#ef4444'}">{{d.cp.pct}}%</div>
        <div v-if="d.syncing" style="font-size:7px;color:#2080f0;margin-top:1px">⟳ 采集中</div>
      </div>
    </div>
  </n-card>

  <!-- Missing Dates -->
  <n-card v-if="missingDates.length" size="small" :style="{borderColor:'#ef444466',borderLeft:'3px solid #ef4444'}">
    <template #header><span style="color:#ef4444;font-size:14px">⚠️ 近期数据异常</span></template>
    <div style="font-size:12px;color:rgba(255,255,255,.55);margin-bottom:8px">以下交易日数据不完整或完全缺失（点击日历日期可触发重新采集）:</div>
    <n-space>
      <n-tag v-for="d in missingDates" :key="d" type="error" size="small">{{d}}</n-tag>
    </n-space>
  </n-card>

  <!-- Download Log -->
  <n-card v-if="dlog.length" size="small">
    <template #header><span style="color:#fff;font-size:14px">📥 最近下载记录</span></template>
    <n-data-table :columns="logColumns" :data="dlog.slice(0,8)" size="small" />
  </n-card>
</n-space>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { NCard, NSpace, NGrid, NGi, NDataTable, NButton, NTag, useDialog } from 'naive-ui'
import axios from 'axios'

const API = window.location.origin
const overview = ref({total_rows:0,total_stocks:0,latest_date:'',exchanges:{}})
const missingDates = ref([])
const dlog = ref([])
const todayStrategy = ref(null)
const cal = ref([])
const smonth = ref(new Date().toISOString().slice(0,7))

const monthLabel = computed(() => {
  const y = parseInt(smonth.value.slice(0,4)), m = parseInt(smonth.value.slice(5,7))
  return y+'年'+m+'月'
})

const fmt = v => v!=null?Number(v).toLocaleString():'0'
const exFull = e => ({SSE:'上交所',SZSE:'深交所',BSE:'北交所'}[e]||e)

const exColumns = [
  { title:'交易所', key:'exchange', width:90, render(r){return exFull(r.key)} },
  { title:'最新日期', key:'latest_date', width:120 },
  { title:'行情', width:120, render(r){return fmt(r.rows)} },
  { title:'股票', key:'stocks', width:80 },
]
const logColumns = [
  { title:'时间', key:'time', width:165 },
  { title:'状态', width:60 },
  { title:'行数', width:70, render(r){return '+'+r.rows} },
  { title:'详情', minWidth:300, ellipsis:{tooltip:true}, render(r){return r.detail||''} },
]

const dialog = useDialog()

function confirmSync(d) {
  dialog.warning({
    title: '数据采集',
    content: '确定重新采集 [' + d.date + '] 的数据？（股票+指数+ETF）',
    positiveText: '确 认',
    negativeText: '取 消',
    onPositiveClick: () => { doSyncDate(d) }
  })
}

const exchangeRows = computed(() => {
  return Object.entries(overview.value.exchanges||{}).filter(e=>e[1].stocks>0).map(([k,v])=>({key:k,...v}))
})

function calStyle(d) {
  if(d.day===null) return {padding:'5px 3px'}
  const cp = d.cp
  let bg='transparent', bd='transparent'
  if(d.td && cp) {
    const p=cp.pct
    if(p>=80){bg='rgba(16,185,129,0.12)';bd='rgba(16,185,129,0.25)'}
    else if(p>=50){bg='rgba(251,191,36,0.12)';bd='rgba(251,191,36,0.2)'}
    else{bg='rgba(239,68,68,0.12)';bd='rgba(239,68,68,0.25)'}
  } else if(d.td && !cp) {
    bg='rgba(239,68,68,0.08)';bd='rgba(239,68,68,0.2)'
  }
  return {
    padding:'5px 3px', borderRadius:'4px', fontSize:'10px', textAlign:'center',
    background:bg, color:d.td?'#fff':'#475569', cursor:d.td?'pointer':'default',
    border:d.td?'1px solid '+bd:'1px solid transparent',
    opacity:d.future?0.35:1
  }
}

function calTitle(d) {
  if(!d.td) return d.date+' 非交易日'
  if(!d.cp) return d.date+' 无数据，点击采集'
  return d.date+' | '+d.cp.rows+'条 ('+d.cp.pct+'%)'
}

function doSyncClick(d) {
  if(!d.td||d.syncing) return
  confirmSync(d)
}

const SYNC_STORAGE_KEY = '_stock_sync_tasks'

function loadSyncTasks() {
  try {
    const tasks = JSON.parse(localStorage.getItem(SYNC_STORAGE_KEY)||'{}')
    const now = Date.now()
    Object.keys(tasks).forEach(date => {
      const task = tasks[date]
      if(now - task.started > 7200000) { delete tasks[date]; return } // 2h timeout
      const day = cal.value.find(d => d.date === date)
      if(day) day.syncing = true
      // 轮询进度
      const iv = setInterval(() => {
        axios.get(API+'/api/data_status/sync_status', {params:{task_id:task.tid}}).then(sr => {
          const st = sr.data.task
          if(!st||st.status==='running') return
          clearInterval(iv)
          const d2 = cal.value.find(d2 => d2.date === date)
          if(d2) d2.syncing = false
          delete tasks[date]; localStorage.setItem(SYNC_STORAGE_KEY, JSON.stringify(tasks))
          loadDataStatus()
        })
      }, 5000)
    })
  } catch(e) {}
}

function doSyncDate(d) {
  if(!d.td||d.syncing) return
  d.syncing = true
  axios.post(API+'/api/data_status/sync_date', {date:d.date}).then(r => {
    const tid = r.data.task_id
    if(!tid){d.syncing=false;return}
    // 持久化到 localStorage
    const tasks = JSON.parse(localStorage.getItem(SYNC_STORAGE_KEY)||'{}')
    tasks[d.date] = {tid, started:Date.now()}
    localStorage.setItem(SYNC_STORAGE_KEY, JSON.stringify(tasks))
    const iv = setInterval(() => {
      axios.get(API+'/api/data_status/sync_status', {params:{task_id:tid}}).then(sr => {
        const st = sr.data.task
        if(!st||st.status==='running') return
        d.syncing = false
        clearInterval(iv)
        const tasks2 = JSON.parse(localStorage.getItem(SYNC_STORAGE_KEY)||'{}')
        delete tasks2[d.date]
        localStorage.setItem(SYNC_STORAGE_KEY, JSON.stringify(tasks2))
        loadDataStatus()
      })
    }, 3000)
  }).catch(() => {d.syncing=false})
}

function prevMonth() {
  const d = new Date(smonth.value+'-01')
  d.setMonth(d.getMonth()-1)
  smonth.value = d.toISOString().slice(0,7)
  loadDataStatus()
}
function nextMonth() {
  const d = new Date(smonth.value+'-01')
  d.setMonth(d.getMonth()+1)
  smonth.value = d.toISOString().slice(0,7)
  loadDataStatus()
}
function goToday() {
  smonth.value = new Date().toISOString().slice(0,7)
  loadDataStatus()
}

async function loadDataStatus() {
  try {
    const r = await axios.get(API+'/api/data_status?month='+smonth.value)
    const data = r.data
    if(data.overview) overview.value = data.overview
    missingDates.value = data.missing_dates||[]
    dlog.value = data.download_log||[]
    todayStrategy.value = data.today_strategy||null
    const arr = (data.calendar||[]).map(x => ({
      day: parseInt(x.date.slice(8)), td:x.is_trade_day, cp:x.completeness,
      future: new Date(x.date)>new Date(), date:x.date, syncing:false
    }))
    const y=parseInt(smonth.value.slice(0,4)), m=parseInt(smonth.value.slice(5,7))
    const fd=new Date(y,m-1,1).getDay()
    const offset=fd===0?6:fd-1
    const pad=[]
    for(let i=0;i<offset;i++) pad.push({day:null,td:false,cp:null,future:false,date:''})
    cal.value=pad.concat(arr)
    loadSyncTasks()
  } catch(e) {}
}

onMounted(loadDataStatus)
</script>
