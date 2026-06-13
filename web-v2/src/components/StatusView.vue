<template>
<div>
  <n-spin v-if="loading" style="padding:60px" />
  <template v-else>
  <!-- Overview Strip (same style as portfolio page) -->
  <div style="display:flex;gap:10px;justify-content:center;padding:8px 0 12px;flex-wrap:wrap">
    <div style="text-align:center;min-width:70px"><div style="font-size:11px;color:var(--c-text-dim)">行情总条数</div><div style="font-size:20px;font-weight:700;color:var(--c-text)">{{fmt(overview.total_rows)}}</div></div>
    <div style="text-align:center;min-width:70px"><div style="font-size:11px;color:var(--c-text-dim)">股票数</div><div style="font-size:20px;font-weight:700;color:var(--c-text)">{{overview.total_stocks}}</div></div>
    <div style="text-align:center;min-width:70px"><div style="font-size:11px;color:var(--c-text-dim)">最新数据</div><div style="font-size:20px;font-weight:700;color:var(--c-text)">{{overview.latest_date||'-'}}</div></div>
    <div style="text-align:center;min-width:70px"><div style="font-size:11px;color:var(--c-text-dim)">漏数据日期</div><div style="font-size:20px;font-weight:700" :style="{color:missingDates.length>0?'#f59e0b':'#888'}">{{missingDates.length}}</div></div>
  </div>

  <!-- Two-column layout: Data Detail + Calendar (same height) -->
  <div style="display:flex;gap:10px;flex-wrap:wrap;align-items:stretch">
    <!-- Left: Data Tables Detail -->
    <div style="flex:1;min-width:280px;display:flex;flex-direction:column">
      <div style="font-size:14px;font-weight:600;color:var(--c-text);margin-bottom:6px">📋 数据明细
        <span v-if="statsTime" style="font-size:9px;color:var(--c-text-faint);margin-left:6px">统计于 {{statsTime}}</span>
        <n-button size="tiny" text style="margin-left:4px" @click="refreshStats" :loading="statsLoading">{{statsLoading?'':'↻'}}</n-button>
      </div>
      <div style="display:flex;flex-direction:column;gap:4px;flex:1">
        <div v-for="dt in dataTables" :key="dt.label" style="display:flex;align-items:center;justify-content:space-between;padding:5px 10px;background:var(--c-card-bg);border-radius:6px;border:1px solid var(--c-card-bg-hover)">
          <div style="display:flex;align-items:baseline;gap:6px;min-width:0">
            <span style="font-size:12px;font-weight:600;color:var(--c-text);white-space:nowrap">{{dt.label}}</span>
            <span v-if="dt.items!=null" style="font-size:9px;color:var(--c-text-faint);white-space:nowrap">{{dt.items}} 只</span>
          </div>
          <div style="text-align:right;flex-shrink:0">
            <div style="font-size:14px;font-weight:700;color:var(--c-text)">{{dt.rows>0?fmt(dt.rows)+' 条':dt.rows===0?'0 条':'-'}} <span v-if="dt.detail" style="font-size:10px;color:var(--c-text-dimmer)">{{dt.detail}}</span></div>
            <div v-if="dt.start" style="font-size:9px;color:var(--c-text-faint);white-space:nowrap">{{dt.start}} ~ {{dt.end}}</div>
            <div v-else style="font-size:9px;color:var(--c-text-faint)">暂无数据</div>
          </div>
        </div>
      </div>
    </div>

    <!-- Right: Calendar + Log -->
    <div style="flex:1;min-width:300px;display:flex;flex-direction:column">
      <div style="font-size:14px;font-weight:600;color:var(--c-text);margin-bottom:6px">📅 交易日历</div>
      <div style="display:flex;align-items:center;gap:6px;margin-bottom:6px;flex-wrap:wrap">
        <n-button size="tiny" @click="prevMonth">◀</n-button>
        <span style="font-weight:600;font-size:13px;color:var(--c-text)">{{monthLabel}}</span>
        <n-button size="tiny" @click="nextMonth">▶</n-button>
        <n-button size="tiny" @click="goToday">今天</n-button>
      </div>

      <!-- Calendar grid + legend side by side -->
      <div style="display:flex;gap:6px">
        <div style="flex:1">
          <div style="display:grid;grid-template-columns:repeat(7,1fr);gap:2px;font-size:9px;color:var(--c-text-faint);margin-bottom:2px;text-align:center">
            <span>一</span><span>二</span><span>三</span><span>四</span><span>五</span><span style="color:#ef4444">六</span><span style="color:#ef4444">日</span>
          </div>
          <div style="display:grid;grid-template-columns:repeat(7,1fr);gap:2px">
            <div v-for="d in cal" :key="d.date" :style="{'padding':'4px 0','borderRadius':'4px','fontSize':'10px','textAlign':'center','background':calBg(d),'color':d.td?'var(--c-text)':'#94a3b8','cursor':d.td?'pointer':'default','border':d.td?'1px solid '+calBd(d):'1px solid transparent','opacity':d.future?0.35:1,'position':'relative'}" :title="calTitle(d)" @click="doSyncClick(d)" @contextmenu.prevent="confirmSync(d)">
              <span v-if="d.day!==null" style="font-size:10px">{{d.day}}</span>
              <div v-if="d.syncing" style="position:absolute;top:0;right:2px;font-size:8px;color:#2080f0">⟳</div>
              <div v-if="d.td&&d.cp&&!d.syncing" :style="{fontSize:'7px',marginTop:'1px',color:d.cp.pct>=80?'#10b981':d.cp.pct>=50?'#f59e0b':'#ef4444'}">{{d.cp.pct}}%</div>
            </div>
          </div>
        </div>
        <!-- Vertical legend -->
        <div style="display:flex;flex-direction:column;justify-content:center;gap:4px;font-size:9px;color:var(--c-text-faint);white-space:nowrap;padding-left:4px">
          <span><span style="color:#10b981;font-size:10px">●</span> ≥80%</span>
          <span><span style="color:#f59e0b;font-size:10px">●</span> 50-80%</span>
          <span><span style="color:#ef4444;font-size:10px">●</span> &lt;50%</span>
          <span><span style="color:#475569;font-size:10px">●</span> 非/未来</span>
        </div>
      </div>

      <!-- Latest 3 log entries -->
      <div style="margin-top:8px;flex:1">
        <div style="font-size:14px;font-weight:600;color:var(--c-text);margin-bottom:4px;display:flex;align-items:center;justify-content:space-between">
          <span>📥 最近记录</span>
          <n-button v-if="dlog.length>3" size="tiny" text style="font-size:10px" @click="showLogModal=true">更多 →</n-button>
        </div>
        <div v-if="dlog.length" style="display:flex;flex-direction:column;gap:2px;font-size:10px">
          <div v-for="r in dlog.slice(0,3)" :key="r.date+r.node" style="display:flex;align-items:center;gap:6px;padding:3px 6px;background:var(--c-card-bg);border-radius:4px">
            <span style="color:var(--c-text-dimmer);min-width:55px">{{(r.date||'').slice(5)}}</span>
            <span :style="{color:r.status==='success'?'#10b981':r.status==='running'?'#2080f0':r.status==='pending'?'#f59e0b':'#ef4444'}">{{r.status==='success'?'✓':r.status==='running'?'⟳':r.status==='pending'?'◻':'✗'}}</span>
            <span style="color:var(--c-text-dim);min-width:60px">{{nodeName(r.node)}}</span>
            <span style="font-size:8px;color:var(--c-text-faint);min-width:50px">{{r.run_id||''}}</span>
            <span style="color:var(--c-text-faint);margin-left:auto">{{r.node==='daily_update'||r.node==='cron'?'—':(r.rows||0)+'条'}}</span>
          </div>
        </div>
        <div v-else style="font-size:10px;color:var(--c-text-faint);padding:4px 6px">暂无记录</div>
      </div>
    </div>
  </div>

  <!-- Data Status DAG Flow -->
  <div style="margin-top:14px">
    <DagView />
  </div>

  <!-- Log History Modal -->
  <!-- Sync Mode Modal -->
  <n-modal v-model:show="showSyncModal" preset="card" title="📥 数据采集" style="width:360px;max-width:85vw" :mask-closable="false">
    <div style="text-align:center;padding:10px 0">
      <div style="font-size:14px;color:var(--c-text);margin-bottom:16px">重新采集 [{{syncDate}}] 的数据？</div>
      <div style="display:flex;gap:10px;justify-content:center">
        <n-button @click="showSyncModal=false">取 消</n-button>
        <n-button type="warning" @click="doSyncForce">强制更新</n-button>
        <n-button type="primary" @click="doSyncQuick">快速更新</n-button>
      </div>
    </div>
  </n-modal>

  <n-modal v-model:show="showLogModal" preset="card" title="📥 运行日志" style="width:900px;max-width:92vw" :mask-closable="false" :segmented="{content:true}" @after-show="loadLogModal">
    <n-space vertical>
      <div v-if="logLoading" style="text-align:center;padding:20px;color:var(--c-text-faint)">加载中...</div>
      <n-data-table v-else :columns="logColumns" :data="logPageData" size="small" :row-props="()=>({style:{fontSize:'12px'}})" />
      <n-pagination v-if="logTotalPages>1" v-model:page="logPage" :page-count="logTotalPages" size="small" />
    </n-space>
  </n-modal>
  </template>
</div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { NDataTable, NButton, NSpace, NSpin, NPagination, NModal } from 'naive-ui'
import axios from 'axios'
import DagView from './DagView.vue'
import { addWsListener } from '../utils/ws'

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
const logLoading = ref(false)

async function loadLogModal() {
  logLoading.value = true
  logPage.value = 1
  try {
    const r = await axios.get(window.location.origin + '/api/dag_logs')
    if (r.data && r.data.nodes) {
      dlog.value = r.data.nodes
    }
  } catch(e) {}
  logLoading.value = false
}

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

const showSyncModal = ref(false)
const syncDate = ref('')
function confirmSync(d) {
  syncDate.value = d.date
  showSyncModal.value = true
}
function doSyncForce() {
  showSyncModal.value = false
  const dateStr = syncDate.value
  if(!dateStr) return
  // 标记日历为同步中
  const day = cal.value.find(d => d.date === dateStr)
  if(day) day.syncing = true
  axios.post(API+'/api/data_status/sync_date', {date:dateStr, mode:'force'}).then(r => {
    if(r.data.busy){ 
      alert(r.data.error||'任务进行中，请等待')
      if(day) day.syncing = false
      return 
    }
  }).catch(() => {})
}
function doSyncQuick() {
  showSyncModal.value = false
  const dateStr = syncDate.value
  if(!dateStr) return
  const day = cal.value.find(d => d.date === dateStr)
  if(day) day.syncing = true
  axios.post(API+'/api/data_status/sync_date', {date:dateStr, mode:'quick'}).then(r => {
    if(r.data.busy){ 
      alert(r.data.error||'任务进行中，请等待')
      if(day) day.syncing = false
      return 
    }
  }).catch(() => {})
}

function doSyncClick(d) {
  if(!d.td||d.syncing) return
  confirmSync(d)
}
// 旧 confirmSync 已替换为 showSyncModal 弹窗

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
  } catch(e) { statsLoading.value = false }
}

async function loadDataStatus() {
  loading.value = true
  try {
    const r = await axios.get(API+'/api/data_status?month='+smonth.value)
    const data = r.data
    if(data.overview) overview.value = data.overview
    missingDates.value = data.missing_dates||[]
    dlog.value = []  // 日志由 WS 推送，初始清空
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
  } catch(e) {} finally { loading.value = false }
}

onMounted(() => {
  loadDataStatus()
  addWsListener((data) => {
    if (data.type === 'dag_log') {
      dlog.value = data.nodes || []
    }
    if (data.type === 'dag_status') {
      // 刷新统计按钮：仅 stats 节点 running/pending 时转动，不跟全局 has_running
      const rs = data.run_status || {}
      statsLoading.value = rs.stats?.status === 'running' || rs.stats?.status === 'pending'
      // 日历同步状态：任务结束后清除
      if (!data.has_running) {
        cal.value.forEach(d => { d.syncing = false })
        // 任务过期（current_run_id 变 null）→ 清空最近记录
        if (!data.current_run_id) {
          dlog.value = []
        }
      }
    }
  })
})
</script>

<style>
.nowrap-cell, .nowrap-cell .n-data-table-th { white-space:nowrap !important; }
.n-data-table-td__ellipsis { max-width:100%; overflow:hidden; text-overflow:ellipsis; white-space:nowrap !important; }
</style>
