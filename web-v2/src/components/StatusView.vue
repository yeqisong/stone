<template>
<div>
  <n-spin v-if="loading" style="padding:60px" />
  <template v-else>
  <!-- Overview Strip -->
  <div style="display:flex;gap:6px;justify-content:center;padding:4px 0 8px;flex-wrap:wrap">
    <div style="text-align:center;min-width:60px"><div style="font-size:9px;color:rgba(255,255,255,.35)">行情总条数</div><div style="font-size:16px;font-weight:600;color:#fff">{{fmt(overview.total_rows)}}</div></div>
    <div style="text-align:center;min-width:60px"><div style="font-size:9px;color:rgba(255,255,255,.35)">股票数</div><div style="font-size:16px;font-weight:600;color:#fff">{{overview.total_stocks}}</div></div>
    <div style="text-align:center;min-width:60px"><div style="font-size:9px;color:rgba(255,255,255,.35)">最新数据</div><div style="font-size:16px;font-weight:600;color:#fff">{{overview.latest_date||'-'}}</div></div>
    <div style="text-align:center;min-width:60px"><div style="font-size:9px;color:rgba(255,255,255,.35)">漏数据日期</div><div style="font-size:16px;font-weight:600" :style="{color:missingDates.length>0?'#f59e0b':'#888'}">{{missingDates.length}}</div></div>
  </div>



  <!-- Two-column layout: Data Detail + Calendar -->
  <div style="display:flex;gap:10px;flex-wrap:wrap">
    <!-- Left: Data Tables Detail -->
    <div style="flex:1;min-width:280px">
      <div style="font-size:12px;font-weight:600;color:#ddd;margin-bottom:6px">📋 数据明细
        <span v-if="statsTime" style="font-size:9px;color:rgba(255,255,255,.3);margin-left:6px">统计于 {{statsTime}}</span>
        <n-button size="tiny" text style="margin-left:4px" @click="refreshStats" :loading="statsLoading">↻</n-button>
      </div>
      <div style="display:flex;flex-direction:column;gap:4px">
        <div v-for="dt in dataTables" :key="dt.label" style="display:flex;align-items:center;justify-content:space-between;padding:5px 10px;background:rgba(255,255,255,.03);border-radius:6px;border:1px solid rgba(255,255,255,.05)">
          <div style="display:flex;align-items:baseline;gap:6px;min-width:0">
            <span style="font-size:12px;font-weight:600;color:#fff;white-space:nowrap">{{dt.label}}</span>
            <span v-if="dt.items!=null" style="font-size:9px;color:rgba(255,255,255,.3);white-space:nowrap">{{dt.items}} 只</span>
          </div>
          <div style="text-align:right;flex-shrink:0">
            <div style="font-size:14px;font-weight:700;color:#fff">{{dt.rows>0?fmt(dt.rows)+' 条':dt.rows===0?'0 条':'-'}} <span v-if="dt.detail" style="font-size:10px;color:rgba(255,255,255,.4)">{{dt.detail}}</span></div>
            <div v-if="dt.start" style="font-size:9px;color:rgba(255,255,255,.35);white-space:nowrap">{{dt.start}} ~ {{dt.end}}</div>
            <div v-else style="font-size:9px;color:rgba(255,255,255,.35)">暂无数据</div>
          </div>
        </div>
      </div>
      <!-- Download Log -->
      <div v-if="dlog.length" style="margin-top:10px">
        <div style="font-size:12px;font-weight:600;color:#ddd;margin-bottom:4px">📥 最近下载记录</div>
        <div style="font-size:10px;color:rgba(255,255,255,.35);margin-bottom:4px">下载记录由每日自动采集脚本写入，仅在有采集操作时更新</div>
        <n-data-table :columns="logColumns" :data="dlog.slice(0,8)" size="small" />
      </div>
    </div>

    <!-- Right: Calendar -->
    <div style="flex:1;min-width:280px">
      <div style="font-size:12px;font-weight:600;color:#ddd;margin-bottom:6px">📅 交易日历</div>
      <div style="display:flex;align-items:center;gap:6px;margin-bottom:8px;flex-wrap:wrap">
        <n-button size="tiny" @click="prevMonth">◀</n-button>
        <span style="font-weight:600;font-size:13px;color:#fff">{{monthLabel}}</span>
        <n-button size="tiny" @click="nextMonth">▶</n-button>
        <n-button size="tiny" @click="goToday">今天</n-button>
      </div>
      <div style="display:grid;grid-template-columns:repeat(7,1fr);gap:2px;font-size:9px;color:rgba(255,255,255,.35);margin-bottom:3px;text-align:center">
        <span>一</span><span>二</span><span>三</span><span>四</span><span>五</span><span style="color:#ef4444">六</span><span style="color:#ef4444">日</span>
      </div>
      <div style="display:grid;grid-template-columns:repeat(7,1fr);gap:2px">
        <div v-for="d in cal" :key="d.date" :style="calStyle(d)" :title="calTitle(d)" @click="doSyncClick(d)" @contextmenu.prevent="confirmSync(d)" style="transition:all .12s;cursor:pointer" :class="{calSync:d.syncing}">
          <span v-if="d.day!==null" style="font-size:10px">{{d.day}}</span>
          <div v-if="d.td&&d.cp" :style="{fontSize:'7px',marginTop:'1px',color:d.cp.pct>=80?'#10b981':d.cp.pct>=50?'#f59e0b':'#ef4444'}">{{d.cp.pct}}%</div>
          <div v-if="d.syncing" style="font-size:7px;color:#2080f0;margin-top:1px">⟳</div>
        </div>
      </div>
      <div style="font-size:8px;color:rgba(255,255,255,.25);margin-top:4px;text-align:center">🟢≥80% 🟡50-80% 🔴&lt;50% ⚫非/未来</div>
    </div>
  </div>
  </template>
</div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { NDataTable, NButton, NSpace, NTag, NSpin, useDialog } from 'naive-ui'
import axios from 'axios'

const API = window.location.origin
const loading = ref(true)
const overview = ref({total_rows:0,total_stocks:0,latest_date:'',exchanges:{}})
const missingDates = ref([])
const dlog = ref([])
const todayStrategy = ref(null)
const dataTables = ref([])
const statsTime = ref('')
const statsLoading = ref(false)
const cal = ref([])
const smonth = ref(new Date().toISOString().slice(0,7))

const monthLabel = computed(() => {
  const y = parseInt(smonth.value.slice(0,4)), m = parseInt(smonth.value.slice(5,7))
  return y+'年'+m+'月'
})

const fmt = v => v!=null?Number(v).toLocaleString():'0'

const logColumns = [
  { title:'时间', key:'time', width:150 },
  { title:'状态', width:50 },
  { title:'行数', width:60, render(r){return '+'+r.rows} },
  { title:'详情', minWidth:200, ellipsis:{tooltip:true}, render(r){return r.detail||''} },
]

const dialog = useDialog()

function confirmSync(d) {
  dialog.warning({
    title: '数据采集',
    content: '确定重新采集 [' + d.date + '] 的数据？（股票+指数+ETF）',
    positiveText: '确 认', negativeText: '取 消',
    onPositiveClick: () => { doSyncDate(d) }
  })
}

const SYNC_STORAGE_KEY = '_stock_sync_tasks'

function loadSyncTasks() {
  try {
    const tasks = JSON.parse(localStorage.getItem(SYNC_STORAGE_KEY)||'{}')
    const now = Date.now()
    Object.keys(tasks).forEach(date => {
      const task = tasks[date]
      if(now - task.started > 7200000) { delete tasks[date]; return }
      const day = cal.value.find(d => d.date === date)
      if(day) day.syncing = true
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
    const tasks = JSON.parse(localStorage.getItem(SYNC_STORAGE_KEY)||'{}')
    tasks[d.date] = {tid, started:Date.now()}
    localStorage.setItem(SYNC_STORAGE_KEY, JSON.stringify(tasks))
    const iv = setInterval(() => {
      axios.get(API+'/api/data_status/sync_status', {params:{task_id:tid}}).then(sr => {
        const st = sr.data.task
        if(!st||st.status==='running') return
        d.syncing = false; clearInterval(iv)
        const tasks2 = JSON.parse(localStorage.getItem(SYNC_STORAGE_KEY)||'{}')
        delete tasks2[d.date]
        localStorage.setItem(SYNC_STORAGE_KEY, JSON.stringify(tasks2))
        loadDataStatus()
      })
    }, 3000)
  }).catch(() => {d.syncing=false})
}

function doSyncClick(d) {
  if(!d.td||d.syncing) return
  confirmSync(d)
}

function calStyle(d) {
  if(d.day===null) return {padding:'5px 2px'}
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
    padding:'4px 2px', borderRadius:'4px', fontSize:'10px', textAlign:'center',
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

function prevMonth() {
  const d = new Date(smonth.value+'-01')
  d.setMonth(d.getMonth()-1)
  smonth.value = d.toISOString().slice(0,7); loadDataStatus()
}
function nextMonth() {
  const d = new Date(smonth.value+'-01')
  d.setMonth(d.getMonth()+1)
  smonth.value = d.toISOString().slice(0,7); loadDataStatus()
}
function goToday() {
  smonth.value = new Date().toISOString().slice(0,7); loadDataStatus()
}

async function refreshStats() {
  statsLoading.value = true
  try {
    await axios.post(API+'/api/refresh_stats')
    await loadDataStatus()
  } catch(e) {}
  statsLoading.value = false
}

async function loadDataStatus() {
  loading.value = true
  try {
    const r = await axios.get(API+'/api/data_status?month='+smonth.value)
    const data = r.data
    if(data.overview) overview.value = data.overview
    missingDates.value = data.missing_dates||[]
    dlog.value = data.download_log||[]
    todayStrategy.value = data.today_strategy||null
    dataTables.value = data.data_tables||[]
    statsTime.value = data.stats_computed_at||''
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
  } catch(e) {} finally { loading.value = false }
}

onMounted(loadDataStatus)
</script>
