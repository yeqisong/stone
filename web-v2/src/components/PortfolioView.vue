<template>
<div class="page-fill">
  <!-- Tab 行：持仓 / 自选 / 动态分组（右侧新增分组） -->
  <div style="display:flex;align-items:center;gap:8px;margin-bottom:8px;flex-wrap:wrap;flex-shrink:0">
    <n-button-group size="tiny">
      <n-button size="tiny" :type="activeTab==='pos'?'primary':'default'" @click="activeTab='pos'">持仓</n-button>
      <n-button v-for="g in groups" :key="g.id" size="tiny" :type="activeTab===g.id?'primary':'default'" @click="activeTab=g.id">
        {{ g.name }}<span v-if="g.count" style="font-size:11px;opacity:0.75"> {{ g.count }}</span>
      </n-button>
    </n-button-group>
    <n-button size="tiny" quaternary type="primary" style="margin-left:auto" @click="openNewGroup"><AppIcon name="plus" :size="13" />  新增分组</n-button>
  </div>

  <!-- 持仓 tab：统计区 + 持仓表（统计区仅持仓 tab 显示） -->
  <template v-if="activeTab==='pos'">
    <StatStrip v-if="data.count" :items="statItems" />

    <n-button type="primary" size="small" @click="startAdd" style="margin:6px 0" class="no-shrink self-start">+ 新增持仓</n-button>

    <n-spin v-if="loading" style="padding:40px" />
    <template v-else>
      <div v-if="data.positions&&data.positions.length" class="fill-table" style="display:flex;flex-direction:column">
        <div v-if="isNarrow" class="pos-cards">
          <div v-for="row in data.positions" :key="row.stock_code" class="pos-card">
            <div class="pos-card-head">
              <div class="pos-card-title">
                <span class="pos-name">{{ row.stock_name }}</span>
                <span class="pos-code">{{ row.stock_code }}</span>
              </div>
              <div class="pos-pnl" :class="(row.pnl||0)>=0?'up':'down'">
                {{ (row.pnl||0)>=0?'+':'' }}¥{{ fmt(row.pnl) }}
                <span>{{ (row.pnl||0)>=0?'+':'' }}{{ (row.pnl_pct||0).toFixed(1) }}%</span>
              </div>
            </div>
            <div class="pos-grid">
              <div><span>数量</span><b>{{ row.quantity }}</b></div>
              <div><span>成本</span><b>¥{{ (row.cost_price||0).toFixed(2) }}</b></div>
              <div><span>现价</span><b>¥{{ (row.current_price||0).toFixed(2) }}</b></div>
              <div><span>市值</span><b>¥{{ fmt(row.market_value) }}</b></div>
            </div>
            <div class="pos-card-foot">
              <span v-if="row.signal_direction" class="pos-signal">今日信号 {{ row.signal_direction==='buy'?'买入':'卖出' }}</span>
              <span v-else class="pos-signal muted">今日无信号</span>
              <div class="pos-actions">
                <n-button size="tiny" @click="showHistory(row.stock_code)">详情</n-button>
                <n-button size="tiny" @click="startEdit(row)">编辑</n-button>
                <n-button size="tiny" type="error" quaternary @click="askDelete(row.stock_code)">删除</n-button>
              </div>
            </div>
          </div>
        </div>
        <n-data-table v-else class="fill-table" flex-height :columns="columns" :data="data.positions" size="small" :row-props="rowProps" scroll-x="1060" />
      </div>
      <n-empty v-else description="暂无持仓" style="flex:1" />
    </template>
  </template>

  <!-- 自选 / 动态分组 tab：列字段同个股列表 -->
  <WatchTable v-else-if="activeTab!==null" :group-id="activeTab" @show-detail="c=>emit('show-detail',c)" />
</div>

<!-- 新增分组弹窗 -->
<n-modal v-model:show="showNewGroup">
  <n-card style="width:380px" title="新增分组" role="dialog" aria-modal="true">
    <n-input v-model:value="newGroupName" size="small" placeholder="分组名称（1~32 字符）" maxlength="32" @keyup.enter="doCreateGroup" />
    <template #footer>
      <n-space justify="flex-end">
        <n-button size="small" @click="showNewGroup=false">取消</n-button>
        <n-button size="small" type="primary" :loading="creatingGroup" @click="doCreateGroup">创建</n-button>
      </n-space>
    </template>
  </n-card>
</n-modal>

<n-modal v-model:show="showEdit">
  <n-card style="width:450px" :title="isAdding ? '新增持仓' : '编辑持仓'" role="dialog" aria-modal="true">
    <n-space vertical>
      <StockSuggestInput v-if="isAdding" v-model:value="editForm.code" placeholder="代码/名称/拼音首字母" @select="onSuggestFill" />
      <n-input-number v-model:value="editForm.qty" :min="100" :step="100" :precision="0" />
      <n-input-number v-model:value="editForm.cost" :min="0" :step="0.01" :precision="2" />
      <n-date-picker v-model:formatted-value="editForm.date" type="date" value-format="yyyy-MM-dd" placeholder="建仓日期" clearable />
      <n-input v-model:value="editForm.note" type="textarea" :rows="2" placeholder="备注" />
    </n-space>
    <template #footer>
      <n-space justify="flex-end">
        <n-button @click="showEdit=false">取消</n-button>
        <n-button type="primary" @click="isAdding?doAdd():doSaveEdit()">{{isAdding?'添加':'保存'}}</n-button>
      </n-space>
    </template>
  </n-card>
</n-modal>
<n-modal v-model:show="showDeleteConfirm">
  <n-card style="width:380px" title="确认删除" role="dialog" aria-modal="true">
    <p>确定删除该持仓？删除后数据不可恢复。</p>
    <template #footer>
      <n-space justify="center">
        <n-button @click="showDeleteConfirm=false">取消</n-button>
        <n-button type="error" @click="confirmDelete">确认删除</n-button>
      </n-space>
    </template>
  </n-card>
</n-modal>

<!-- History Modal -->
<n-modal v-model:show="showHistoryModal">
  <n-card style="width:600px" title="持仓明细" role="dialog" aria-modal="true">
    <div v-if="historyLoading" style="text-align:center;padding:20px">加载中...</div>
    <template v-else>
      <div v-if="historyPos" style="display:flex;gap:20px;justify-content:center;padding:6px 0;margin-bottom:10px;background:var(--c-card-bg-hover);border-radius:8px">
        <div style="text-align:center"><div style="font-size:11px;color:var(--c-text-dim)">代码</div><div style="font-size:16px;font-weight:600">{{historyPos.stock_code}}</div></div>
        <div style="text-align:center"><div style="font-size:11px;color:var(--c-text-dim)">名称</div><div style="font-size:16px;font-weight:600">{{historyPos.stock_name}}</div></div>
        <div style="text-align:center"><div style="font-size:11px;color:var(--c-text-dim)">持仓</div><div style="font-size:16px;font-weight:600">{{historyPos.quantity}}股</div></div>
        <div style="text-align:center"><div style="font-size:11px;color:var(--c-text-dim)">成本价</div><div style="font-size:16px;font-weight:600">¥{{historyPos.cost_price.toFixed(2)}}</div></div>
      </div>
      <h4 style="margin:8px 0"><AppIcon name="file-text" :size="13" />  加减仓记录</h4>
      <div v-if="historyRecords.length" style="overflow-x:auto;-webkit-overflow-scrolling:touch">
        <n-data-table :columns="historyColumns" :data="historyRecords" size="small" :max-height="360" />
      </div>
      <n-empty v-else description="暂无历史记录" />
    </template>
  </n-card>
</n-modal>
</template>

<script setup>
import AppIcon from './AppIcon.vue'
import StockSuggestInput from './StockSuggestInput.vue'
import WatchTable from './WatchTable.vue'
import { ref, reactive, h, computed, onMounted } from 'vue'
import { useMessage, NButton, NSpace, NCard, NDataTable, NModal, NForm, NFormItem, NInput, NInputNumber, NDatePicker, NEmpty, NSpin, NTag, NButtonGroup } from 'naive-ui'
import axios from 'axios'
import { useAuthStore } from '../stores/auth'
import { useViewport } from '../utils/viewport'
import StatStrip from './StatStrip.vue'
const emit = defineEmits(['show-detail'])
const API = window.location.origin
const message = useMessage()
const auth2 = useAuthStore()
const { isNarrow } = useViewport()
const data = reactive({positions:[], count:0, total_value:0, total_pnl:0})
const showEdit = ref(false), showDeleteConfirm = ref(false), loading = ref(true)

// ── Tab：'pos'=持仓，number=分组 id（自选 + 动态组） ──
const activeTab = ref('pos')
const groups = ref([])
const showNewGroup = ref(false), newGroupName = ref(''), creatingGroup = ref(false)
const _authHeaders = () => auth2.token ? {Authorization: 'Bearer '+auth2.token} : {}
async function loadGroups(){
  try{
    const r = await axios.get(API+'/api/watch/groups')
    groups.value = r.data.groups||[]
    // 当前 tab 的组被删（详情页/其它入口）时回退到持仓
    if (activeTab.value !== 'pos' && !groups.value.some(g => g.id === activeTab.value)) activeTab.value = 'pos'
  }catch(e){}
}
function openNewGroup(){ newGroupName.value=''; showNewGroup.value=true }
async function doCreateGroup(){
  const name = newGroupName.value.trim()
  if (!name) { message.warning('请输入分组名称'); return }
  creatingGroup.value = true
  try{
    const r = await axios.post(API+'/api/watch/groups', {name}, {headers:_authHeaders()})
    showNewGroup.value = false
    await loadGroups()
    activeTab.value = r.data.id
    message.success(`分组「${name}」已创建`)
  }catch(e){ message.error(e.response?.data?.detail || '创建失败') }
  creatingGroup.value = false
}
function onSuggestFill() {
  if (!editForm.qty) editForm.qty = 100   // 选中建议仅填充代码；数量为空给默认一手
}
const isAdding = ref(false)
const editForm = reactive({code:'', qty:100, cost:0, date:null, note:''})
const deleteCode = ref('')

function startEdit(row){
  isAdding.value = false
  editForm.code = row.stock_code; editForm.qty = row.quantity; editForm.cost = row.cost_price
  editForm.date = null; editForm.note = row.notes || ''
  showEdit.value = true
}
function askDelete(code){ deleteCode.value = code; showDeleteConfirm.value = true }
function startAdd(){
  isAdding.value = true
  editForm.code = ''; editForm.qty = 100; editForm.cost = 0; editForm.date = null; editForm.note = ''
  showEdit.value = true
}
// History
const showHistoryModal = ref(false)
const historyLoading = ref(false)
const historyPos = ref(null)
const historyRecords = ref([])
const historyColumns = [
  { title:'时间', key:'created_at', width:160 },
  { title:'操作', key:'action', width:70, render(r){return h(NTag,{type:r.action==='add'?'success':r.action==='update'?'warning':'error',size:'small'},{default:()=>r.action==='add'?'加仓':r.action==='update'?'调仓':'清仓'})} },
  { title:'数量(前)', key:'qty_before', width:70, align:'right' },
  { title:'数量(后)', key:'qty_after', width:70, align:'right' },
  { title:'成本(前)', width:90, align:'right', render(r){return '¥'+Number(r.cost_before).toFixed(2)} },
  { title:'成本(后)', width:90, align:'right', render(r){return '¥'+Number(r.cost_after).toFixed(2)} },
]
async function showHistory(code){
  showHistoryModal.value = true
  historyLoading.value = true
  try{
    const r = await axios.get(API+'/api/portfolio/'+code+'/history')
    historyRecords.value = r.data.records||[]
    historyPos.value = r.data.position
  }catch(e){} finally { historyLoading.value = false }
}
import { fmtMoney } from '../utils/ui'
const fmt = v => v!=null?fmtMoney(Number(v)):'0'
const statItems = computed(() => [
  { label: '持仓', value: data.count },
  { label: '市值', value: '¥' + fmt(data.total_value) },
  { label: '盈亏', value: '¥' + fmt(data.total_pnl), color: data.total_pnl >= 0 ? '#c22b3f' : '#0a7a5a' },
])
const columns = [
  { title:'代码', key:'stock_code', width:85, fixed:'left', render(r){return h('span',{style:{color:'var(--c-accent)'}},r.stock_code)} },
  { title:'名称', key:'stock_name', width:100, fixed:'left' },
  { title:'数量', key:'quantity', width:70, align:'right' },
  { title:'成本', key:'cost_price', width:95, align:'right', render(r){return '¥'+(r.cost_price||0).toFixed(2)} },
  { title:'现价', key:'current_price', width:95, align:'right', render(r){return '¥'+(r.current_price||0).toFixed(2)} },
  { title:'市值', key:'market_value', width:105, align:'right', render(r){return '¥'+fmt(r.market_value)} },
  { title:'盈亏', key:'pnl', width:130, align:'right', ellipsis:{tooltip:true}, render(r){
    const pnl=r.pnl||0, pct=(r.pnl_pct||0).toFixed(1)
    return h('span',{style:{color:pnl>=0?'#c22b3f':'#0a7a5a',whiteSpace:'nowrap'}}, `¥${fmt(pnl)} (${pnl>=0?'+':''}${pct}%)`)
  }},
  { title:'今日信号', key:'signal_direction', width:115, align:'center', render(r){
    if (!r.signal_direction) return h('span',{style:{color:'var(--c-text-faint)'}},'—')
    const isBuy = r.signal_direction === 'buy'
    const dateStr = (r.signal_date||'').slice(5)
    return h('span',{style:{color:isBuy?'#c22b3f':'#0a7a5a',fontSize:'12px',whiteSpace:'nowrap'}},
      (isBuy?'买入':'卖出') + ' ★'.repeat(r.signal_strength||0) + ' ' + dateStr)
  }},
  // 固定窄列 + 溢出 tooltip：原 minWidth 弹性列会吃光剩余宽度（空备注也占满），挤得盈亏列裁字
  { title:'备注', key:'notes', width:120, fixed:'right', ellipsis:{tooltip:true}, render(r){return h('span',{style:{fontSize:'11px',color:'var(--c-text-dim)'}},r.notes||'')} },
  { title:'操作', width:140, fixed:'right', render(row){return h('span',[
    h(NButton,{size:'tiny',onClick:()=>showHistory(row.stock_code)},{default:()=>'详情'}),
    h(NButton,{size:'tiny',onClick:()=>startEdit(row)},{default:()=>'编辑'}),
    h(NButton,{size:'tiny',type:'error',quaternary:true,style:{marginLeft:'4px'},onClick:()=>askDelete(row.stock_code)},{default:()=>'删除'})
  ])}}
]
function rowProps(row){return {style:'cursor:pointer',onClick:(e)=>{if(!e.target.closest('button'))emit('show-detail',row.stock_code)}}}
async function load(){loading.value=true;try{const r=await axios.get(API+'/api/portfolio');Object.assign(data,r.data)}catch(e){message.error('持仓加载失败，请刷新重试')}finally{loading.value=false}}
onMounted(() => { load(); loadGroups() })
async function doAdd(){
  if(!editForm.code)return
  const auth2 = useAuthStore()
const { isNarrow } = useViewport()
  const headers = auth2.token ? {Authorization: 'Bearer '+auth2.token} : {}
  try{
    const body = {stock_code:editForm.code,quantity:editForm.qty,cost_price:editForm.cost,notes:editForm.note}
    if(editForm.date) body.trade_date = editForm.date
    await axios.post(API+'/api/portfolio', body, {headers})
    showEdit.value=false;await load();message.success('添加成功')
  }catch(e){message.error('添加失败: '+(e.response?.data?.detail||e.message))}
}
async function doSaveEdit(){
  const headers = auth2.token ? {Authorization: 'Bearer '+auth2.token} : {}
  try{await axios.put(API+'/api/portfolio/'+editForm.code,{quantity:editForm.qty,cost_price:editForm.cost,notes:editForm.note},{headers});showEdit.value=false;await load();message.success('已保存')}catch(e){message.error('保存失败')}
}
async function confirmDelete(){
  const headers = auth2.token ? {Authorization: 'Bearer '+auth2.token} : {}
  try{await axios.delete(API+'/api/portfolio/'+deleteCode.value,{headers});showDeleteConfirm.value=false;await load();message.success('已删除')}catch(e){message.error('删除失败')}
}
onMounted(load)
</script>

<style scoped>
.pos-cards { display: flex; flex-direction: column; gap: 10px; overflow-y: auto; padding-bottom: 8px; }
.pos-card { background: var(--c-bg-header); border: 1px solid var(--c-border); border-radius: 10px; padding: 12px 14px; }
.pos-card-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 10px; }
.pos-name { font-size: 15px; font-weight: 600; }
.pos-code { margin-left: 8px; font-size: 12px; color: var(--c-text-dim); }
.pos-pnl { text-align: right; font-size: 16px; font-weight: 600; font-variant-numeric: tabular-nums; white-space: nowrap; }
.pos-pnl span { display: block; font-size: 12px; font-weight: 500; }
.pos-pnl.up { color: var(--c-up); }
.pos-pnl.down { color: var(--c-down); }
.pos-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 10px; margin-top: 12px; padding-top: 10px; border-top: 1px solid var(--c-border); }
.pos-grid span { display: block; font-size: 11px; color: var(--c-text-dim); }
.pos-grid b { display: block; margin-top: 2px; font-size: 13px; font-weight: 500; font-variant-numeric: tabular-nums; }
.pos-card-foot { display: flex; align-items: center; gap: 8px; margin-top: 10px; }
.pos-signal { font-size: 11px; color: var(--c-text-dim); }
.pos-signal.muted { color: var(--c-text-faint); }
.pos-actions { margin-left: auto; display: flex; gap: 4px; }
</style>
