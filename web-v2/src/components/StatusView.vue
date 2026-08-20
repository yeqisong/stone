<template>
<div>
  <n-spin v-if="loading" style="padding:60px" />
  <div v-if="!loading">
  <!-- Overview Strip (same style as portfolio page) -->
  <StatStrip :items="overviewItems" />

  <!-- Data Source Health -->
  <div v-if="dataSources.length" style="display:flex;align-items:center;gap:12px;padding:0 0 10px;flex-wrap:wrap">
    <span style="font-size:11px;color:var(--c-text-dim)">数据源</span>
    <div v-for="src in dataSources" :key="src.name" style="display:flex;align-items:center;gap:4px;padding:3px 10px;border-radius:12px;font-size:11px" :style="{background: src.name===activeSource ? 'rgba(32,128,240,0.1)' : 'var(--c-card-bg)', border: src.name===activeSource ? '1px solid rgba(32,128,240,0.3)' : '1px solid var(--c-border)'}">
      <span :style="{color: src.healthy ? '#10b981' : '#ef4444', fontSize:'14px'}">●</span>
      <span style="color:var(--c-text)">{{src.name}}</span>
      <span v-if="src.name===activeSource" style="font-size:9px;color:#2080f0;font-weight:600">活跃</span>
      <span v-if="src.name==='baostock'" style="font-size:9px;color:var(--c-text-faint)">补充</span>
    </div>
    <!-- tushare 当日配额 -->
    <div v-if="quota.calls_limit" style="display:flex;align-items:center;gap:8px;padding:3px 12px;border-radius:12px;font-size:11px;background:var(--c-card-bg);border:1px solid var(--c-border)">
      <span style="color:var(--c-text-dim)">tushare 配额</span>
      <span :style="{color: quotaColor, fontSize:'12px', fontWeight:700}">{{quota.remaining}}</span>
      <span style="color:var(--c-text-faint)">/ {{quota.calls_limit}} 次</span>
      <div style="width:80px;height:5px;border-radius:3px;background:var(--c-border-light);overflow:hidden">
        <div :style="{width: Math.min(quota.used_pct,100)+'%', height:'100%', background: quotaColor}"></div>
      </div>
      <span v-if="quota.exhausted" style="color:#ef4444;font-weight:600">⚠ 已用尽</span>
      <span v-else-if="quota.risk_level==='high'" style="color:#ef4444;font-weight:600">⚠ 高风险</span>
      <span v-else-if="quota.risk_level==='medium'" style="color:#f59e0b">注意</span>
      <span style="color:var(--c-text-faint)">分钟 {{quota.minute_calls}}/{{quota.minute_limit}}</span>
    </div>
  </div>

  <!-- Two-column layout: Data Detail + Calendar (same height) -->
  <div style="display:flex;gap:10px;flex-wrap:wrap;align-items:stretch">
    <!-- Left: Data Tables Detail -->
    <div style="flex:1;min-width:280px;display:flex;flex-direction:column">
      <div style="font-size:14px;font-weight:600;color:var(--c-text);margin-bottom:6px">📋 数据明细
        <span v-if="statsTime" style="font-size:10px;color:var(--c-text-faint);margin-left:6px">统计于 {{statsTime}}</span>
        <n-button size="tiny" text style="margin-left:4px" @click="refreshStats" :loading="statsLoading">{{statsLoading?'':'↻'}}</n-button>
      </div>
      <div style="display:flex;flex-direction:column;gap:4px;flex:1">
        <div v-for="dt in dataTables" :key="dt.label" style="display:flex;align-items:center;justify-content:space-between;padding:5px 10px;background:var(--c-card-bg);border-radius:6px;border:1px solid var(--c-card-bg-hover);cursor:pointer" :title="dt.detail ? '点击查看详情' : ''" @click="goStockFundList(dt)">
          <div style="display:flex;align-items:baseline;gap:6px;min-width:0">
            <span style="font-size:12px;font-weight:600;color:var(--c-text);white-space:nowrap">{{dt.label}}</span>
            <span v-if="dt.items!=null" style="font-size:10px;color:var(--c-text-faint);white-space:nowrap">{{dt.items}} 只</span>
          </div>
          <div style="text-align:right;flex-shrink:0">
            <div style="font-size:14px;font-weight:700;color:var(--c-text)"><span v-if="dt.detail" style="font-size:10px;font-weight:400;color:var(--c-text-dimmer)">{{dt.detail}} · </span>{{dt.rows>0?fmt(dt.rows)+' 条':dt.rows===0?'0 条':'-'}}</div>
            <div v-if="dt.start" style="font-size:10px;color:var(--c-text-faint);white-space:nowrap">{{dt.start}} ~ {{dt.end}}</div>
            <div v-else style="font-size:10px;color:var(--c-text-faint)">暂无数据</div>
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
            <div v-for="d in cal" :key="d.date" :style="{'padding':'3px 1px','borderRadius':'4px','fontSize':'9px','textAlign':'center','background':calBg(d),'color':d.td?'var(--c-text)':'#94a3b8','cursor':d.td&&d.detail?'pointer':'default','border':d.td?'1px solid '+calBd(d):'1px solid transparent','opacity':d.future?0.35:1,'position':'relative'}" :title="calTitle(d)" @click="d.td&&d.detail?showCalDetail(d):null">
              <span v-if="d.day!==null" style="font-size:10px;font-weight:500">{{d.day}}</span>
              <div v-if="d.syncing" style="position:absolute;top:0;right:2px;font-size:8px;color:#2080f0">⟳</div>
              <div v-if="d.td&&d.cp&&!d.syncing" style="font-size:8px;line-height:1.1">
                <div v-if="d.cp.rows>0" style="color:var(--c-text-dim)">{{d.cp.rows}}只</div>
                <div :style="{color:d.cp.pct>=80?'#10b981':d.cp.pct>=50?'#f59e0b':'#ef4444'}">{{d.cp.pct}}%</div>
              </div>
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

    </div>
  </div>

  <!-- ══════════════════════════════════════════ -->
  <!--  服务器监控                                            -->
  <!-- ══════════════════════════════════════════ -->
  <div style="margin-top:14px">
    <div style="font-size:14px;font-weight:600;color:var(--c-text);margin-bottom:6px">🖥 服务器监控</div>
    <div style="display:flex;gap:10px;flex-wrap:wrap">
      <div v-for="m in sysMetrics" :key="m.label" style="flex:1;min-width:100px;padding:8px 12px;background:var(--c-card-bg);border-radius:6px;border:1px solid var(--c-card-bg-hover)" :style="{cursor: m.clickable?'pointer':'default'}" @click="m.clickable && openDbDetail()">
        <div style="font-size:10px;color:var(--c-text-dim)">{{ m.label }}<span v-if="m.clickable" style="margin-left:2px;font-size:9px">↗</span></div>
        <div style="font-size:16px;font-weight:700;color:var(--c-text);margin:2px 0">{{ m.value }}<span style="font-size:11px;font-weight:400;color:var(--c-text-dim)"> {{ m.unit }}</span></div>
        <div v-if="m.sub" style="font-size:10px;color:var(--c-text-dim)">{{ m.sub }}</div>
        <div v-if="m.pct!=null" style="margin-top:4px;height:3px;background:rgba(255,255,255,0.08);border-radius:2px;overflow:hidden">
          <div :style="{width:m.pct+'%',height:'100%',background:m.pct>80?'#ef4444':m.pct>50?'#f59e0b':'#10b981',borderRadius:'2px'}"></div>
        </div>
      </div>
    </div>
  </div>

  <!-- ══════════════════════════════════════════ -->
  <!--  历史补数                                              -->
  <!-- ══════════════════════════════════════════ -->
  <!--  全局交易偏好                                            -->
  <!-- ══════════════════════════════════════════ -->
  <div style="margin-top:14px">
    <div style="font-size:14px;font-weight:600;color:var(--c-text);margin-bottom:6px">🎯 全局交易偏好</div>
    <n-radio-group v-model:value="prefMode" @update:value="setPref">
      <n-radio-button value="left" label="左侧" />
      <n-radio-button value="balanced" label="均衡" />
      <n-radio-button value="right" label="右侧" />
    </n-radio-group>
  </div>

  <!-- ══════════════════════════════════════════ -->
  <!--  历史补数                                            -->
  <div style="margin-top:14px">
    <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:6px">
      <span style="font-size:14px;font-weight:600;color:var(--c-text)">📥 历史补数</span>
      <n-button size="tiny" @click="showBfLogModal = true; bfLogPage = 1; loadBfLogs()">📋 日志</n-button>
    </div>
    <div style="display:flex;gap:6px;flex-wrap:wrap;margin-bottom:8px">
      <n-button v-for="btn in backfillBtns" :key="btn.type" size="small" :data-bf="btn.type" @click="openBackfill(btn.type)">
        {{ btn.icon }} {{ btn.label }}
      </n-button>
    </div>

    <!-- Running / recent task progress -->
    <div v-if="bfTask" style="margin-top:4px">
      <div style="display:flex;align-items:center;gap:8px;padding:6px 10px;background:var(--c-card-bg);border-radius:6px;border:1px solid var(--c-card-bg-hover)">
        <span style="font-size:16px">{{ bfTask.status==='running'?'●':bfTask.status==='completed'?'✅':bfTask.status==='failed'?'❌':'⏹'}}</span>
        <div style="flex:1;min-width:0">
          <div style="font-size:12px;font-weight:600;color:var(--c-text)">
            {{ bfTask.task_label }}
            <span :style="{fontSize:'10px',color:bfTask.status==='running'?'#f59e0b':bfTask.status==='cancelling'?'#f59e0b':bfTask.status==='completed'?'#10b981':'#ef4444'}">{{ STATUS_LABELS[bfTask.status] || bfTask.status }}</span>
          </div>
          <div v-if="bfTask.progress" style="margin-top:4px">
            <div style="height:4px;background:rgba(255,255,255,0.08);border-radius:2px;overflow:hidden">
              <div :style="{width:bfPct+'%',height:'100%',background:bfTask.status==='running'?'#2080f0':bfTask.status==='cancelling'?'#f59e0b':'#10b981',borderRadius:'2px',transition:'width .3s'}"></div>
            </div>
            <div style="display:flex;gap:12px;font-size:10px;color:var(--c-text-dim);margin-top:3px;flex-wrap:wrap">
              <span v-if="bfTask.progress.total_batches">批次 {{ bfTask.progress.current_batch }}/{{ bfTask.progress.total_batches }}</span>
              <span v-if="bfTask.progress.stocks_total">{{ bfTask.progress.stocks_done }}/{{ bfTask.progress.stocks_total }} 只</span>
              <span v-if="bfTask.progress.rows">{{ bfTask.progress.rows.toLocaleString() }} 行</span>
              <span v-if="bfTask.progress.errors" style="color:#ef4444">失败 {{ bfTask.progress.errors }} 只</span>
              <span>耗时 {{fmtDuration(bfTask.elapsed_seconds)}}</span>
              <span v-if="bfTask.status==='running' && bfTask.eta_seconds">预计剩余 {{fmtDuration(bfTask.eta_seconds)}}</span>
            </div>
          </div>
          <div v-if="bfTask.error_message" style="font-size:10px;color:#ef4444;margin-top:2px">{{ bfTask.error_message }}</div>
        </div>
        <n-button v-if="bfTask.status==='running'||bfTask.status==='cancelling'" size="tiny" type="warning" :disabled="bfTask.status==='cancelling'" @click="cancelBackfill">{{ bfTask.status==='cancelling'?'取消中…':'取消' }}</n-button>
      </div>
    </div>

    <!-- Last completed task (if different from current) -->
    <div v-if="bfLastTask && bfLastTask.task_id !== (bfTask?.task_id)" style="margin-top:6px;font-size:10px;color:var(--c-text-dim)">
      <span>{{ bfLastTask.task_label }}</span>
      <span :style="{color:bfLastTask.status==='completed'?'#10b981':'#ef4444',marginLeft:'6px'}">{{ bfLastTask.status==='completed'?'已完成':'失败'}}</span>
      <span style="margin-left:8px">{{ bfLastTask.progress?.rows?.toLocaleString() || 0 }} 行</span>
      <span style="margin-left:8px">耗时 {{fmtDuration(bfLastTask.elapsed_seconds)}}</span>
    </div>
  </div>

  <!-- Backfill Log Modal -->
  <n-modal v-model:show="showBfLogModal" preset="card" title="📋 补数日志" style="width:900px;max-width:92vw" :mask-closable="false" :segmented="{content:true}" @after-show="loadBfLogs">
    <n-space vertical>
      <div v-if="bfLogLoading" style="text-align:center;padding:20px;color:var(--c-text-faint)">加载中...</div>
      <div v-else style="overflow-x:auto;-webkit-overflow-scrolling:touch">
        <n-data-table :columns="bfLogColumns" :data="bfLogItems" size="small" :row-props="bfLogRowProps" scroll-x="700" />
      </div>
      <n-pagination v-if="bfLogTotalPages>1" v-model:page="bfLogPage" :page-count="bfLogTotalPages" size="small" @update:page="(p) => { bfLogPage = p || 1; nav.bfLogPage = bfLogPage; nav.syncHash(); loadBfLogs() }" />
    </n-space>
  </n-modal>

  <!-- DB 表大小弹窗 -->
  <n-modal v-model:show="showDbDetail" preset="card" title="🗄️ 数据库表大小" style="width:500px;max-width:92vw" :mask-closable="false">
    <n-data-table v-if="dbTables.length" :columns="dbTableCols" :data="dbTables" size="small" />
    <n-empty v-else description="加载中..." />
  </n-modal>

  <!-- Backfill Modal -->
  <BackfillModal :show="bfModalShow" :type="bfModalType" @close="bfModalShow=false" @started="onBackfillStarted" />

  <n-modal v-model:show="showCalDtl" preset="card" :title="'📊 ' + calDtlDate + ' 数据明细'" style="width:380px;max-width:92vw">
    <n-data-table v-if="calDtlData" :columns="calDtlCols" :data="calDtlRows" size="small" :bordered="false" :single-line="false" />
    <div v-else style="padding:20px;text-align:center;color:var(--c-text-dim);font-size:12px">无明细数据</div>
  </n-modal>

</div>
</div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted, h } from 'vue'
import { NDataTable, NButton, NSpace, NSpin, NPagination, NModal, NEmpty, NTag, NRadioGroup, NRadioButton, useDialog, useMessage } from 'naive-ui'
import axios from 'axios'
import BackfillModal from './BackfillModal.vue'
import StatStrip from './StatStrip.vue'
import { addWsListener } from '../utils/ws'
import { useNavStore } from '../stores/nav'
import { bjDateStr } from '../utils/date.js'
const nav = useNavStore()
const dialog = useDialog()
const message = useMessage()

const API = window.location.origin
const prefMode = ref('balanced')
// 北京时间 YYYY-MM-DD（共享工具，见 utils/date.js）

async function loadPref() {
  try {
    const r = await axios.get(API + '/api/settings')
    prefMode.value = r.data.preference?.mode || 'balanced'
  } catch(e) { prefMode.value = 'balanced' }
}
async function setPref(m) {
  try { await axios.post(API + '/api/settings/preference', { mode: m }) } catch(e) {}
}
const loading = ref(true)
const overview = ref({total_rows:0,total_stocks:0,latest_date:'',exchanges:{}})
const missingDates = ref([])
const todayStrategy = ref(null)
const dataTables = ref([])
const statsTime = ref('')
const statsLoading = ref(false)
const cal = ref([])
const smonth = ref(bjDateStr().slice(0,7))
const dataSources = ref([])
const activeSource = ref(null)
const quota = ref({})
const quotaColor = computed(() => {
  if (quota.value.exhausted || quota.value.risk_level === 'high') return '#ef4444'
  if (quota.value.risk_level === 'medium') return '#f59e0b'
  return '#10b981'
})
const calDtlDate = ref('')
const calDtlData = ref(null)
const showCalDtl = ref(false)


const monthLabel = computed(() => {
  const y = parseInt(smonth.value.slice(0,4)), m = parseInt(smonth.value.slice(5,7))
  return y+'年'+m+'月'
})

const fmt = v => v!=null?Number(v).toLocaleString():'0'

const overviewItems = computed(() => [
  { label: '行情总条数', value: fmt(overview.total_rows) },
  { label: '股票数', value: overview.total_stocks },
  { label: '最新数据', value: overview.latest_date || '-' },
  { label: '漏数据日期', value: missingDates.length, color: missingDates.length > 0 ? '#f59e0b' : undefined },
])

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


function calTitle(d) {
  if(!d.td) return d.date+' 非交易日'
  if(!d.cp) return d.date+' 无数据，点击采集'
  return d.date+' | '+d.cp.rows+'条 ('+d.cp.pct+'%)'
}

function goStockFundList(dt) {
  const typeMap = {'上交所A股':'stock','深交所A股':'stock','北交所':'stock','指数日K线':'index','ETF日K线':'etf'}
  const t = typeMap[dt.label]
  if (t) { nav.stockFundType = t; nav.tab = 'u' }
}

const calDtlCols = [
  { title:'类别', key:'label', width:50 },
  { title:'实际', key:'actual', width:65, align:'right' },
  { title:'应有', key:'baseline', width:65, align:'right' },
  { title:'完整度', key:'pct', align:'right', render(r){ return (r.pct||0)+'%' } },
]
const calDtlRows = computed(() => {
  if(!calDtlData.value) return []
  return [
    { label:'个股', ...calDtlData.value.stock },
    { label:'指数', ...calDtlData.value.index },
    { label:'ETF', ...calDtlData.value.etf },
    { label:'基本面', ...calDtlData.value.fund },
  ]
})
function showCalDetail(d) {
  calDtlDate.value = d.date
  calDtlData.value = d.detail
  showCalDtl.value = true
}

function prevMonth() {
  const d = new Date(smonth.value + '-01T00:00:00')
  d.setMonth(d.getMonth() - 1)
  smonth.value = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`
  nav.statusMonth = smonth.value; nav.syncHash(); loadDataStatus()
}
function nextMonth() {
  const d = new Date(smonth.value + '-01T00:00:00')
  d.setMonth(d.getMonth() + 1)
  smonth.value = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`
  nav.statusMonth = smonth.value; nav.syncHash(); loadDataStatus()
}
function goToday() {
  smonth.value = bjDateStr().slice(0,7); nav.statusMonth = smonth.value; nav.syncHash(); loadDataStatus()
}

async function refreshStats() {
  statsLoading.value = true
  try {
    const r = await axios.post(API + '/api/refresh_stats')
    if (r.data.busy) { dialog.warning({ title: '任务进行中', content: '已有统计任务在进行中，请稍候再试' }); statsLoading.value = false }
  } catch(e) { statsLoading.value = false }
}

async function loadDataStatus() {
  loading.value = true
  try {
    const r = await axios.get(API+'/api/data_status?month='+smonth.value)
    const data = r.data
    if(data.overview) overview.value = data.overview
    missingDates.value = data.missing_dates||[]
    todayStrategy.value = data.today_strategy||null
    dataTables.value = data.data_tables||[]
    statsTime.value = data.stats_computed_at||''
    const arr = (data.calendar||[]).map(x => ({
      day: parseInt(x.date.slice(8)), td:x.is_trade_day, cp:x.completeness,
      future: new Date(x.date)>new Date(), date:x.date, syncing:false,
      detail: x.detail
    }))
    const y=parseInt(smonth.value.slice(0,4)), m=parseInt(smonth.value.slice(5,7))
    const fd=new Date(y,m-1,1).getDay()
    const offset=fd===0?6:fd-1
    const pad=[]
    for(let i=0;i<offset;i++) pad.push({day:null,td:false,cp:null,future:false,date:''})
    cal.value=pad.concat(arr)
  } catch(e) {} finally { loading.value = false }
}

async function loadDataSources() {
  try {
    const r = await axios.get(API + '/api/data-sources/health')
    dataSources.value = r.data?.sources || []
    activeSource.value = r.data?.active_source || null
  } catch(e) { /* API 不可用时静默 */ }
}

async function loadQuota() {
  try {
    const r = await axios.get(API + '/api/tushare_quota')
    quota.value = r.data || {}
  } catch(e) { /* 未配置 tushare 时静默 */ }
}


onMounted(() => {
  loadDataStatus()
  loadDataSources()
  loadQuota()
  loadSysMetrics()
  loadPref()
  // 配额定期刷新（30s，与补数/采集共用配额时保持最新）
  setInterval(loadQuota, 30000)
  wsUnwatch.value = addWsListener((data) => {
    // 补数进度
    if (data.type === 'sys_metrics') {
      const d = data.data
      const dbSizeGB = (d.db_size_mb / 1024).toFixed(1)
      sysMetrics.value = [
        { label: '内存总量', value: (d.memory_total_mb/1024).toFixed(1), unit: 'GB', sub: null, pct: null },
        { label: '内存剩余', value: (d.memory_avail_mb/1024).toFixed(1), unit: 'GB', sub: `已用 ${d.memory_used_pct}%`, pct: d.memory_used_pct },
        { label: 'DB 大小', value: dbSizeGB, unit: 'GB', sub: null, pct: null, clickable: true },
        { label: '数据盘剩余', value: d.disk_avail_gb, unit: 'GB', sub: `已用 ${d.disk_used_pct}%`, pct: d.disk_used_pct },
        { label: 'CPU 使用率', value: d.cpu_pct, unit: '%', sub: null, pct: d.cpu_pct },
      ]
    }
    if (data.type === 'backfill_progress') {
      bfTask.value = data
      // 保留最后一次完成的任务
      if (data.status === 'completed' || data.status === 'failed') {
        bfLastTask.value = data
        // 30 秒后自动清空当前任务显示
        setTimeout(() => {
          if (bfTask.value?.task_id === data.task_id) {
            bfTask.value = null
          }
        }, 30000)
      }
    }
    if (data.type === 'task_progress') {
      if ((data.status === 'completed' || data.status === 'failed') && statsLoading.value) {
        statsLoading.value = false
      }
    }
  })
})

// WS 监听器清理（v-if 切 tab 时防止泄漏）
const wsUnwatch = ref(null)
onUnmounted(() => { if (wsUnwatch.value) wsUnwatch.value() })

// ── 历史补数 ──

const backfillBtns = [
  { type: 'kline', label: '个股日K线', icon: '📈' },
  { type: 'index', label: '指数日K线', icon: '📊' },
  { type: 'etf', label: 'ETF日K线', icon: '💹' },
  { type: 'fund', label: '基本面', icon: '📋' },
  { type: 'calendar', label: '日历统计', icon: '📅' },
  { type: 'stock_master', label: '更新股票列表', icon: '🔄' },
]

const STATUS_LABELS = {
  running: '运行中', completed: '已完成', failed: '失败',
  cancelled: '已取消', cancelling: '取消中…', pending: '等待中',
}

const bfModalShow = ref(false)
const bfModalType = ref('kline')
const bfTask = ref(null)
const bfLastTask = ref(null)

const bfPct = computed(() => {
  const t = bfTask.value
  if (!t || !t.progress || !t.progress.stocks_total) return 0
  return Math.round((t.progress.stocks_done || 0) / t.progress.stocks_total * 100)
})

function openBackfill(type) {
  if (type === 'stock_master') {
    dialog.warning({
      title: '刷新股票列表',
      content: '将刷新全量股票列表（IPO/退市/名称），确定继续？',
      positiveText: '继续',
      negativeText: '取消',
      onPositiveClick: async () => {
        try {
          await axios.post(window.location.origin + '/api/data_status/backfill', { type: 'stock_master' })
          message.success('已启动')
        } catch (e) {
          message.error(e.response?.data?.error || e.message)
        }
      },
    })
    return
  }
  bfModalType.value = type
  bfModalShow.value = true
}

function onBackfillStarted() {
  // WS 会自动推送进度
}

function cancelBackfill() {
  const taskId = bfTask.value?.task_id
  if (!taskId) return
  axios.post(API + '/api/data_status/backfill/' + taskId + '/cancel').then(r => {
    if (r.data?.ok) {
      message.success(r.data.message || '终止信号已发送')
      // 等待 WS 推送 cancelled 状态
    }
  }).catch(() => {})
}

function fmtDuration(sec) {
  if (!sec || sec < 0) return '0s'
  const h = Math.floor(sec / 3600)
  const m = Math.floor((sec % 3600) / 60)
  const s = sec % 60
  if (h) return h + 'h ' + m + 'm'
  if (m) return m + 'm ' + s + 's'
  return s + 's'
}

// ── 服务器监控 ──

const sysMetrics = ref([])
const _cpuPrev = ref(null)
const showDbDetail = ref(false)
const dbTables = ref([])
const dbTableCols = [
  { title: '表名', key: 'name', width: 180, ellipsis: { tooltip: true } },
  { title: '大小', key: 'size', width: 80, align: 'right' },
  { title: '行数', key: 'rows', width: 80, align: 'right', render(r) { return (r.rows || 0).toLocaleString() } },
]

async function openDbDetail() {
  showDbDetail.value = true
  try {
    const r = await axios.get(API + '/api/system/metrics')
    dbTables.value = r.data?.db?.tables || []
  } catch(e) {}
}

async function loadSysMetrics() {
  try {
    const r = await axios.get(API + '/api/system/metrics')
    const d = r.data

    // CPU 使用率：需两次采样求差值
    let cpuPct = 0
    if (d.cpu_idle && d.cpu_total) {
      if (_cpuPrev.value) {
        const idleDelta = d.cpu_idle - _cpuPrev.value.idle
        const totalDelta = d.cpu_total - _cpuPrev.value.total
        if (totalDelta > 0) cpuPct = Math.round((1 - idleDelta / totalDelta) * 100)
      }
      _cpuPrev.value = { idle: d.cpu_idle, total: d.cpu_total }
    }

    const dbSizeGB = ((d.db?.db_size_mb || 0) / 1024).toFixed(1)
    sysMetrics.value = [
      { label: '内存总量', value: (d.memory_total_mb / 1024).toFixed(1), unit: 'GB', sub: null, pct: null },
      { label: '内存剩余', value: (d.memory_avail_mb / 1024).toFixed(1), unit: 'GB', sub: `已用 ${d.memory_used_pct}%`, pct: d.memory_used_pct },
      { label: 'DB 大小', value: dbSizeGB, unit: 'GB', sub: null, pct: null, clickable: true },
      { label: '数据盘剩余', value: d.disk_avail_gb, unit: 'GB', sub: `已用 ${d.disk_used_pct}%`, pct: d.disk_used_pct },
      { label: 'CPU 使用率', value: cpuPct, unit: '%', sub: null, pct: cpuPct },
    ]
  } catch (e) {}
}

// ── 补数日志弹窗 ──

const showBfLogModal = ref(false)
const bfLogLoading = ref(false)
const bfLogItems = ref([])
const bfLogPage = ref(1)
const bfLogPageSize = 20
const bfLogTotal = ref(0)
const bfLogTotalPages = ref(1)

const bfLogColumns = [
  { title: '开始', key: 'started_at', width: 115, ellipsis: { tooltip: true }, className: 'nowrap-cell', render(r) { return (r.started_at || '').slice(0, 16) } },
  { title: '结束', key: 'completed_at', width: 115, ellipsis: { tooltip: true }, className: 'nowrap-cell', render(r) { return r.completed_at ? r.completed_at.slice(0, 16) : (r.status==='running'?'—':'') } },
  { title: '类型', key: 'task_label', width: 75, className: 'nowrap-cell' },
  { title: '日期范围', width: 150, className: 'nowrap-cell', ellipsis: { tooltip: true }, render(r) { return r.start_date ? `${r.start_date} ~ ${r.end_date || ''}` : '—' } },
  { title: '模式', width: 40, className: 'nowrap-cell', render(r) { return r.force ? '强制' : '续传' } },
  { title: '状态', width: 65, className: 'nowrap-cell', render(r) {
    const m = { running: '⏳运行中', completed: '✅完成', failed: '❌失败', cancelled: '⏹已取消' }
    return m[r.status] || r.status
  }},
  { title: '行数', width: 72, className: 'nowrap-cell', align: 'right', render(r) { return (r.progress?.rows || 0).toLocaleString() }},
  { title: '耗时', width: 70, className: 'nowrap-cell', render(r) { return fmtDuration(r.elapsed_seconds) }},
  { title: '错误', width: 150, ellipsis: { tooltip: true }, render(r) { return r.error_message || (r.progress?.errors || 0) }},
]

function bfLogRowProps(row) {
  return {
    style: {
      color: row.status === 'running' ? '#f59e0b' : row.status === 'failed' ? '#ef4444' : 'var(--c-text)',
    }
  }
}

async function loadBfLogs() {
  bfLogLoading.value = true
  try {
    const r = await axios.get(API + '/api/data_status/backfill/logs', {
      params: { page: bfLogPage.value, page_size: bfLogPageSize }
    })
    bfLogItems.value = r.data?.items || []
    bfLogTotal.value = r.data?.total || 0
    bfLogTotalPages.value = r.data?.total_pages || 1
  } catch (e) {
    bfLogItems.value = []
  } finally {
    bfLogLoading.value = false
  }
}
</script>

<style>
.nowrap-cell, .nowrap-cell .n-data-table-th { white-space:nowrap !important; }
.n-data-table-td__ellipsis { max-width:100%; overflow:hidden; text-overflow:ellipsis; white-space:nowrap !important; }
</style>
