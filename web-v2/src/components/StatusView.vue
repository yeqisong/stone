<template>
<div>
  <n-spin v-if="loading" style="padding:60px" />
  <template v-else>
  <!-- Overview Strip (same style as portfolio page) -->
  <div style="display:flex;gap:10px;justify-content:center;padding:8px 0 12px;flex-wrap:wrap">
    <div style="text-align:center;min-width:70px"><div style="font-size:11px;color:rgba(255,255,255,.45)">行情总条数</div><div style="font-size:20px;font-weight:700;color:#fff">{{fmt(overview.total_rows)}}</div></div>
    <div style="text-align:center;min-width:70px"><div style="font-size:11px;color:rgba(255,255,255,.45)">股票数</div><div style="font-size:20px;font-weight:700;color:#fff">{{overview.total_stocks}}</div></div>
    <div style="text-align:center;min-width:70px"><div style="font-size:11px;color:rgba(255,255,255,.45)">最新数据</div><div style="font-size:20px;font-weight:700;color:#fff">{{overview.latest_date||'-'}}</div></div>
    <div style="text-align:center;min-width:70px"><div style="font-size:11px;color:rgba(255,255,255,.45)">漏数据日期</div><div style="font-size:20px;font-weight:700" :style="{color:missingDates.length>0?'#f59e0b':'#888'}">{{missingDates.length}}</div></div>
  </div>

  <!-- Two-column layout: Data Detail + Calendar (same height) -->
  <div style="display:flex;gap:10px;flex-wrap:wrap;align-items:stretch">
    <!-- Left: Data Tables Detail -->
    <div style="flex:1;min-width:280px;display:flex;flex-direction:column">
      <div style="font-size:12px;font-weight:600;color:#ddd;margin-bottom:6px">📋 数据明细
        <span v-if="statsTime" style="font-size:9px;color:rgba(255,255,255,.3);margin-left:6px">统计于 {{statsTime}}</span>
        <n-button size="tiny" text style="margin-left:4px" @click="refreshStats" :loading="statsLoading">↻</n-button>
      </div>
      <div style="display:flex;flex-direction:column;gap:4px;flex:1">
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
    </div>

    <!-- Right: Calendar + Log -->
    <div style="flex:1;min-width:300px;display:flex;flex-direction:column">
      <div style="font-size:12px;font-weight:600;color:#ddd;margin-bottom:6px">📅 交易日历</div>
      <div style="display:flex;align-items:center;gap:6px;margin-bottom:6px;flex-wrap:wrap">
        <n-button size="tiny" @click="prevMonth">◀</n-button>
        <span style="font-weight:600;font-size:13px;color:#fff">{{monthLabel}}</span>
        <n-button size="tiny" @click="nextMonth">▶</n-button>
        <n-button size="tiny" @click="goToday">今天</n-button>
      </div>

      <!-- Calendar grid + legend side by side -->
      <div style="display:flex;gap:6px">
        <div style="flex:1">
          <div style="display:grid;grid-template-columns:repeat(7,1fr);gap:2px;font-size:9px;color:rgba(255,255,255,.35);margin-bottom:2px;text-align:center">
            <span>一</span><span>二</span><span>三</span><span>四</span><span>五</span><span style="color:#ef4444">六</span><span style="color:#ef4444">日</span>
          </div>
          <div style="display:grid;grid-template-columns:repeat(7,1fr);gap:2px">
            <div v-for="d in cal" :key="d.date" :style="{'padding':'4px 0','borderRadius':'4px','fontSize':'10px','textAlign':'center','background':calBg(d),'color':d.td?'#fff':'#475569','cursor':d.td?'pointer':'default','border':d.td?'1px solid '+calBd(d):'1px solid transparent','opacity':d.future?0.35:1}" :title="calTitle(d)" @click="doSyncClick(d)" @contextmenu.prevent="confirmSync(d)">
              <span v-if="d.day!==null" style="font-size:10px">{{d.day}}</span>
              <div v-if="d.td&&d.cp" :style="{fontSize:'7px',marginTop:'1px',color:d.cp.pct>=80?'#10b981':d.cp.pct>=50?'#f59e0b':'#ef4444'}">{{d.cp.pct}}%</div>
            </div>
          </div>
        </div>
        <!-- Vertical legend -->
        <div style="display:flex;flex-direction:column;justify-content:center;gap:4px;font-size:9px;color:rgba(255,255,255,.35);white-space:nowrap;padding-left:4px">
          <span><span style="color:#10b981;font-size:10px">●</span> ≥80%</span>
          <span><span style="color:#f59e0b;font-size:10px">●</span> 50-80%</span>
          <span><span style="color:#ef4444;font-size:10px">●</span> &lt;50%</span>
          <span><span style="color:#475569;font-size:10px">●</span> 非/未来</span>
        </div>
      </div>

      <!-- Latest 3 log entries -->
      <div style="margin-top:8px;flex:1">
        <div style="font-size:11px;font-weight:600;color:#ddd;margin-bottom:4px;display:flex;align-items:center;justify-content:space-between">
          <span>📥 最近记录</span>
          <n-button v-if="dlog.length>3" size="tiny" text style="font-size:10px" @click="showLogModal=true">更多 →</n-button>
        </div>
        <div v-if="dlog.length" style="display:flex;flex-direction:column;gap:2px;font-size:10px">
          <div v-for="r in dlog.slice(0,3)" :key="r.date+r.node" style="display:flex;align-items:center;gap:6px;padding:3px 6px;background:rgba(255,255,255,.03);border-radius:4px">
            <span style="color:rgba(255,255,255,.4);min-width:55px">{{(r.date||'').slice(5)}}</span>
            <span :style="{color:r.status==='success'?'#10b981':r.status==='running'?'#2080f0':'#ef4444'}">{{r.status==='success'?'✓':r.status==='running'?'⟳':'✗'}}</span>
            <span style="color:rgba(255,255,255,.6);min-width:60px">{{nodeName(r.node)}}</span>
            <span style="font-size:8px;color:rgba(255,255,255,.25);min-width:50px">{{r.run_id||''}}</span>
            <span style="color:rgba(255,255,255,.35);margin-left:auto">{{r.node==='daily_update'||r.node==='cron'?'—':(r.rows||0)+'条'}}</span>
          </div>
        </div>
        <div v-else style="font-size:10px;color:rgba(255,255,255,.25);padding:4px 6px">暂无记录</div>
      </div>
    </div>
  </div>

  <!-- Data Status DAG Flow -->
  <div style="margin-top:14px">
    <DagView />
  </div>

  <!-- Log History Modal -->
  <n-modal v-model:show="showLogModal" preset="card" title="📥 运行日志" style="width:900px;max-width:92vw" :mask-closable="false" :segmented="{content:true}">
    <n-space vertical>
      <n-data-table :columns="logColumns" :data="logPageData" size="small" :row-props="()=>({style:{fontSize:'12px'}})" />
      <n-pagination v-if="logTotalPages>1" v-model:page="logPage" :page-count="logTotalPages" size="small" />
    </n-space>
  </n-modal>
  </template>
</div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { NDataTable, NButton, NButtonGroup, NSpace, NTag, NSpin, NPagination, NModal, useDialog } from 'naive-ui'
import axios from 'axios'
import DagView from './DagView.vue'

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
const showLogModal = ref(false)
const logPage = ref(1)
const logPageSize = 20

const monthLabel = computed(() => {
  const y = parseInt(smonth.value.slice(0,4)), m = parseInt(smonth.value.slice(5,7))
  return y+'年'+m+'月'
})

const fmt = v => v!=null?Number(v).toLocaleString():'0'

const logTotalPages = computed(() => Math.ceil(dlog.value.length / logPageSize))
const logPageData = computed(() => {
  const s = (logPage.value - 1) * logPageSize
  return dlog.value.slice(s, s + logPageSize)
})

const nodeNames = {
  daily_update:'更新汇总', kline:'A股日K线', index:'指数', etf:'ETF', fund:'基本面',
  treemap:'树图', strategy:'策略', stats:'统计', test_node:'测试'
}
const nodeName = n => nodeNames[n] || n

const logColumns = [
  { title:'日期', key:'date', width:80, ellipsis:{tooltip:true} },
  { title:'节点', key:'node', width:65, render(r){return nodeName(r.node)}, ellipsis:{tooltip:true} },
  { title:'状态', width:44, render(r){return r.status==='success'?'✅':r.status==='running'?'⏳':r.status==='pending'?'◻':'❌'}, className:'nowrap-cell' },
  { title:'行数', width:42, render(r){return r.node==='daily_update'||r.node==='cron'?'—':r.rows||0}, className:'nowrap-cell' },
  { title:'任务ID', key:'run_id', width:72, ellipsis:{tooltip:true} },
  { title:'创建', key:'created_at', width:130, ellipsis:{tooltip:true}, className:'nowrap-cell' },
  { title:'开始', key:'started_at', width:130, ellipsis:{tooltip:true}, className:'nowrap-cell' },
  { title:'完成', key:'finished_at', width:130, ellipsis:{tooltip:true}, className:'nowrap-cell' },
  { title:'详情', key:'detail', minWidth:120, ellipsis:{tooltip:true} },
]

const calBg = d => {
  if(d.day===null) return 'transparent'
  if(d.td && d.cp) { const p=d.cp.pct; return p>=80?'rgba(16,185,129,0.12)':p>=50?'rgba(251,191,36,0.12)':'rgba(239,68,68,0.12)' }
  if(d.td && !d.cp) return 'rgba(239,68,68,0.08)'
  return 'transparent'
}
const calBd = d => {
  if(d.day===null) return 'transparent'
  if(d.td && d.cp) { const p=d.cp.pct; return p>=80?'rgba(16,185,129,0.25)':p>=50?'rgba(251,191,36,0.2)':'rgba(239,68,68,0.25)' }
  if(d.td && !d.cp) return 'rgba(239,68,68,0.2)'
  return 'transparent'
}

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
  axios.post(API+'/api/data_status/sync_date', {date:d.date}).then(r => {
    if(r.data.busy){ alert(r.data.error||'任务进行中，请等待'); return }
    d.syncing = true
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
    const r = await axios.post(API+'/api/refresh_stats')
    if (r.data.busy) { alert(r.data.error||'任务进行中，请等待'); statsLoading.value=false; return }
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

onMounted(() => {
  // 清理 localStorage 中过期/不存在的同步任务
  const SYNC_KEY = '_stock_sync_tasks'
  const tasks = JSON.parse(localStorage.getItem(SYNC_KEY)||'{}')
  const now = Date.now()
  let changed = false
  for (const [date, task] of Object.entries(tasks)) {
    if (now - task.started > 3600000) { delete tasks[date]; changed = true }  // 1小时过期
  }
  if (changed) localStorage.setItem(SYNC_KEY, JSON.stringify(tasks))
  loadDataStatus()
})
</script>

<style>
.nowrap-cell, .nowrap-cell .n-data-table-th { white-space:nowrap !important; }
.n-data-table-td__ellipsis { max-width:100%; overflow:hidden; text-overflow:ellipsis; white-space:nowrap !important; }
</style>
