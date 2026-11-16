<template>
<n-modal v-model:show="show" :mask-closable="!uploading">
  <n-card class="ti-card" :bordered="false">
    <template #header>
      <div class="ti-head">
        <b><AppIcon name="upload" :size="15" />  导入交割单</b>
        <span class="ti-sub">支持券商导出的 .xls / .xlsx / .csv / .txt（自动识别 GBK）</span>
      </div>
    </template>

    <!-- 上传区 -->
    <div v-if="!result" class="ti-drop" :class="{ 'ti-drop-active': dragOver, 'ti-drop-busy': uploading }"
      @click="pickFile" @dragover.prevent="dragOver=true" @dragleave.prevent="dragOver=false"
      @drop.prevent="onDrop">
      <input ref="fileInput" type="file" accept=".xls,.xlsx,.csv,.txt,.tsv" style="display:none" @change="onPick" />
      <template v-if="!uploading">
        <AppIcon name="file-text" :size="28" />
        <div class="ti-drop-title">点击选择，或把交割单拖到这里</div>
        <div class="ti-drop-hint">导入后自动按「成交编号 + 内容指纹」去重，重复导入不会产生重复记录</div>
      </template>
      <template v-else>
        <n-spin size="medium" />
        <div class="ti-drop-title">正在解析并去重…</div>
        <div class="ti-drop-hint">{{ fileName }}</div>
      </template>
    </div>

    <div v-if="error" class="ti-error">{{ error }}</div>

    <!-- 结果 -->
    <template v-if="result">
      <div class="ti-summary">
        <div class="ti-sum-item"><span>文件</span><b>{{ result.filename }}</b></div>
        <div class="ti-sum-item"><span>解析</span><b>{{ result.format }}</b></div>
        <div class="ti-sum-item"><span>总记录</span><b>{{ result.rows_total }}</b></div>
        <div class="ti-sum-item ti-new"><span>新增</span><b>{{ result.rows_inserted }}</b></div>
        <div class="ti-sum-item"><span>重复跳过</span><b>{{ result.rows_duplicate }}</b></div>
        <div class="ti-sum-item" :class="{ 'ti-bad': result.rows_invalid }"><span>无效</span><b>{{ result.rows_invalid }}</b></div>
        <div class="ti-sum-item"><span>区间</span><b>{{ result.date_min }} ~ {{ result.date_max }}</b></div>
      </div>

      <div v-if="result.rows_inserted===0 && result.rows_duplicate>0" class="ti-note">
        这个文件之前已经导入过（{{ result.rows_duplicate }} 条全部命中），没有新增记录。可以继续核对持仓。
      </div>
      <div v-else-if="result.rows_inserted>0" class="ti-note ti-ok">
        本次新增 {{ result.rows_inserted }} 条成交；{{ result.rows_duplicate ? `另有 ${result.rows_duplicate} 条重复已跳过。` : '' }}
      </div>

      <div v-if="result.preview?.warnings?.length" class="ti-warn">
        <div v-for="w in result.preview.warnings" :key="w">· {{ w }}</div>
      </div>
      <div v-if="result.invalid_samples?.length" class="ti-warn">
        <div v-for="x in result.invalid_samples.slice(0,5)" :key="x.line">第 {{ x.line }} 行：{{ x.reason }}</div>
      </div>

      <!-- 持仓差异预览 -->
      <div class="ti-section-title">
        <b>持仓核对</b>
        <span>交割单数量优先于手工维护；确认后才会改写持仓</span>
      </div>
      <div class="ti-diff-wrap">
        <n-data-table :columns="diffCols" :data="diffRows" size="small" :bordered="false"
          :max-height="300" :row-key="r=>r.stock_code" />
      </div>
      <div v-if="!diffRows.length" class="ti-empty">没有可同步的持仓差异</div>
      <div class="ti-cash" v-if="result.preview?.summary">
        交割单推算现金余额 <b>¥{{ fmt(result.preview.summary.cash) }}</b>
        · 持仓市值 <b>¥{{ fmt(result.preview.summary.market_value) }}</b>
      </div>

      <div class="ti-actions">
        <n-button size="small" @click="reset">重新选择</n-button>
        <n-button size="small" @click="finish(false)">仅入库，不改持仓</n-button>
        <n-button size="small" type="primary" :loading="applying" @click="applyNow">
          确认同步持仓
        </n-button>
      </div>
    </template>

    <template #footer>
      <div v-if="!result" class="ti-foot">
        <span>交割单只读解析；不会上传到任何第三方</span>
        <n-button size="small" @click="show=false">取消</n-button>
      </div>
    </template>
  </n-card>
</n-modal>
</template>

<script setup>
import AppIcon from './AppIcon.vue'
import { ref, computed, watch, h } from 'vue'
import { NModal, NCard, NButton, NDataTable, NSpin, NTag, useMessage } from 'naive-ui'
import axios from 'axios'

const props = defineProps({ show: { type: Boolean, default: false } })
const emit = defineEmits(['update:show', 'imported'])
const message = useMessage()
const API = window.location.origin

const show = computed({ get: () => props.show, set: v => emit('update:show', v) })
const fileInput = ref(null)
const uploading = ref(false), applying = ref(false)
const dragOver = ref(false)
const error = ref('')
const fileName = ref('')
const result = ref(null)

function pickFile(){ if(!uploading.value) fileInput.value?.click() }
function onPick(e){
  const f = e.target.files?.[0]
  e.target.value = ''
  if (f) upload(f)
}
function onDrop(e){
  dragOver.value = false
  const f = e.dataTransfer?.files?.[0]
  if (f) upload(f)
}
async function upload(file){
  if (uploading.value) return
  if (file.size > 20 * 1024 * 1024) { error.value = '文件超过 20MB，请拆分后再导入'; return }
  uploading.value = true; error.value = ''; result.value = null; fileName.value = file.name
  try{
    const buf = await file.arrayBuffer()
    const r = await axios.post(API + '/api/portfolio/trades/import', buf, {
      headers: { 'Content-Type': 'application/octet-stream' },
      params: { filename: file.name },
      timeout: 120000,
    })
    result.value = r.data
    message.success(r.data.rows_inserted
      ? `已导入 ${r.data.rows_inserted} 条成交`
      : `已全部去重（${r.data.rows_duplicate} 条）`)
  }catch(e){
    error.value = e.response?.data?.detail || e.message || '导入失败'
  }finally{
    uploading.value = false
  }
}

const statusMeta = {
  new:     { label:'新增',   type:'success' },
  changed: { label:'变更',   type:'warning' },
  same:    { label:'一致',   type:'default' },
  closed:  { label:'清仓',   type:'error' },
  untouched:{ label:'未覆盖', type:'default' },
}
const diffRows = computed(() => {
  const rows = result.value?.preview?.diff || []
  const order = { new:0, changed:1, closed:2, untouched:3, same:4 }
  return [...rows].sort((a,b) => (order[a.status]??9) - (order[b.status]??9))
})
const diffCols = [
  { title:'代码', key:'stock_code', width:78, render(r){ return h('span',{style:{color:'var(--c-text-dim)'}},r.stock_code) } },
  { title:'名称', key:'stock_name', width:100 },
  { title:'交割单数量', key:'quantity', width:96, align:'right', render(r){ return r.status==='closed'?'0':String(r.quantity) } },
  { title:'交割单成本', key:'cost_price', width:96, align:'right', render(r){
      if(r.status==='closed') return '—'
      return h('span',{}, '¥'+Number(r.cost_price||0).toFixed(2)+(r.cost_estimated?' *':''))
  }},
  { title:'当前持仓', key:'cur_qty', width:84, align:'right' },
  { title:'当前成本', key:'cur_cost', width:88, align:'right', render(r){ return r.cur_cost?('¥'+Number(r.cur_cost).toFixed(2)):'—' } },
  { title:'差异', key:'delta_qty', width:80, align:'right', render(r){
      if(r.status==='same'||r.status==='untouched') return '—'
      const d=Number(r.delta_qty||0)
      return h('span',{style:{color:d>0?'var(--c-up)':d<0?'var(--c-down)':'var(--c-text)'}}, (d>0?'+':'')+d)
  }},
  { title:'状态', key:'status', width:80, align:'center', render(r){
      const m=statusMeta[r.status]||{label:r.status,type:'default'}
      return h(NTag,{size:'small',type:m.type,bordered:false},{default:()=>m.label})
  }},
]

async function applyNow(){
  applying.value = true
  try{
    const r = await axios.post(API + '/api/portfolio/trades/apply-positions', {}, { timeout: 60000 })
    const a = r.data.applied || {}
    message.success(`持仓已同步：新增 ${a.inserted||0} / 更新 ${a.updated||0} / 清仓 ${a.closed||0}`)
    result.value = { ...result.value, preview: r.data.preview, applied: a }
    finish(true)
  }catch(e){
    message.error(e.response?.data?.detail || '同步持仓失败')
  }finally{
    applying.value = false
  }
}
function finish(synced){
  emit('imported', { synced })
  show.value = false
}
function reset(){ result.value=null; error.value=''; fileName.value='' }
function fmt(v){ return v==null?'—':Number(v).toLocaleString('zh-CN',{maximumFractionDigits:2}) }

watch(() => props.show, v => { if(v) { reset() } })
</script>

<style scoped>
.ti-card { width: min(760px, 94vw); max-height: 92vh; display:flex; flex-direction:column; }
.ti-card :deep(.n-card__content) { overflow-y: auto; }
.ti-head { display:flex; align-items:baseline; gap:10px; flex-wrap:wrap; }
.ti-sub { font-size: var(--text-meta); color: var(--c-text-dim); font-weight:400; }
.ti-drop {
  border: 1.5px dashed var(--c-border); border-radius: var(--radius); padding: 30px 20px;
  display:flex; flex-direction:column; align-items:center; gap:8px; cursor:pointer;
  color: var(--c-text-dim); transition: border-color .15s, background .15s;
}
.ti-drop:hover, .ti-drop-active { border-color: var(--c-primary); background: var(--c-card-bg-hover); }
.ti-drop-busy { cursor: default; }
.ti-drop-title { font-size:14px; color: var(--c-text); }
.ti-drop-hint { font-size: var(--text-meta); color: var(--c-text-faint); text-align:center; }
.ti-error { margin-top:12px; padding:8px 10px; border-radius: var(--radius-sm); color: var(--c-error);
  border:1px solid color-mix(in srgb, var(--c-error) 35%, transparent); font-size:13px; }
.ti-summary { display:flex; gap:16px; flex-wrap:wrap; padding:10px 0 4px; border-bottom:1px solid var(--c-border); }
.ti-sum-item { display:flex; flex-direction:column; gap:2px; font-size: var(--text-meta); color: var(--c-text-dim); }
.ti-sum-item b { font-size:13px; color: var(--c-text); font-variant-numeric: tabular-nums; }
.ti-sum-item.ti-new b { color: var(--c-up); }
.ti-sum-item.ti-bad b { color: var(--c-error); }
.ti-note { margin-top:10px; font-size:13px; color: var(--c-text-dim); }
.ti-note.ti-ok { color: var(--c-text); }
.ti-warn { margin-top:8px; font-size: var(--text-meta); color: var(--c-warning); line-height:1.7; }
.ti-section-title { display:flex; align-items:baseline; gap:10px; margin:14px 0 8px; flex-wrap:wrap; }
.ti-section-title b { font-size:13px; }
.ti-section-title span { font-size: var(--text-meta); color: var(--c-text-faint); }
.ti-diff-wrap { border:1px solid var(--c-border); border-radius: var(--radius-sm); overflow:hidden; }
.ti-empty { padding:16px; text-align:center; color: var(--c-text-faint); font-size:13px; }
.ti-cash { margin-top:8px; font-size: var(--text-meta); color: var(--c-text-dim); }
.ti-cash b { color: var(--c-text); }
.ti-actions { display:flex; gap:8px; justify-content:flex-end; margin-top:16px; flex-wrap:wrap; }
.ti-foot { display:flex; align-items:center; justify-content:space-between; gap:10px; font-size: var(--text-meta); color: var(--c-text-faint); }
</style>
