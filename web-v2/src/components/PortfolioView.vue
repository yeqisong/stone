<template>
<div class="page-fill">
  <StatStrip v-if="data.count" :items="statItems" />

  <n-button type="primary" ghost size="tiny" @click="startAdd" style="margin:6px 0" class="no-shrink self-start">+ 新增持仓</n-button>

  <n-spin v-if="loading" style="padding:40px" />
  <div v-else class="fill-table" style="display:flex;flex-direction:column">
    <n-data-table v-if="data.positions&&data.positions.length" class="fill-table" flex-height :columns="columns" :data="data.positions" size="small" :row-props="rowProps" scroll-x="1000" />
    <n-empty v-else description="暂无持仓" style="flex:1" />
  </div>
</div>

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
        <div style="text-align:center"><div style="font-size:11px;color:var(--c-text-dim)">代码</div><div style="font-size:16px;font-weight:700">{{historyPos.stock_code}}</div></div>
        <div style="text-align:center"><div style="font-size:11px;color:var(--c-text-dim)">名称</div><div style="font-size:16px;font-weight:700">{{historyPos.stock_name}}</div></div>
        <div style="text-align:center"><div style="font-size:11px;color:var(--c-text-dim)">持仓</div><div style="font-size:16px;font-weight:700">{{historyPos.quantity}}股</div></div>
        <div style="text-align:center"><div style="font-size:11px;color:var(--c-text-dim)">成本价</div><div style="font-size:16px;font-weight:700">¥{{historyPos.cost_price.toFixed(2)}}</div></div>
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
import { ref, reactive, h, computed, onMounted } from 'vue'
import { useMessage, NButton, NSpace, NCard, NDataTable, NModal, NForm, NFormItem, NInput, NInputNumber, NDatePicker, NEmpty, NSpin, NTag } from 'naive-ui'
import axios from 'axios'
import { useAuthStore } from '../stores/auth'
import StatStrip from './StatStrip.vue'
const emit = defineEmits(['show-detail'])
const API = window.location.origin
const message = useMessage()
const auth2 = useAuthStore()
const data = reactive({positions:[], count:0, total_value:0, total_pnl:0})
const showEdit = ref(false), showDeleteConfirm = ref(false), loading = ref(true)
function onSuggestFill() {
  if (!editForm.qty) editForm.qty = 100   // 选中建议仅填充代码；数量为空给默认一手
}
const isAdding = ref(false)
const editForm = reactive({code:'', qty:100, cost:0, date:null, note:''})
const deleteCode = ref('')

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
  { label: '盈亏', value: '¥' + fmt(data.total_pnl), color: data.total_pnl >= 0 ? '#ef4444' : '#10b981' },
])
const columns = [
  { title:'代码', key:'stock_code', width:85, fixed:'left', render(r){return h('span',{style:{color:'var(--n-color-target)'}},r.stock_code)} },
  { title:'名称', key:'stock_name', width:100, fixed:'left' },
  { title:'数量', key:'quantity', width:70, align:'right' },
  { title:'成本', key:'cost_price', width:95, align:'right', render(r){return '¥'+(r.cost_price||0).toFixed(2)} },
  { title:'现价', key:'current_price', width:95, align:'right', render(r){return '¥'+(r.current_price||0).toFixed(2)} },
  { title:'市值', key:'market_value', width:105, align:'right', render(r){return '¥'+fmt(r.market_value)} },
  { title:'盈亏', key:'pnl', width:110, align:'right', render(r){
    const pnl=r.pnl||0, pct=(r.pnl_pct||0).toFixed(1)
    return h('span',{style:{color:pnl>=0?'#ef4444':'#10b981',whiteSpace:'nowrap'}}, `¥${fmt(pnl)} (${pnl>=0?'+':''}${pct}%)`)
  }},
  { title:'今日信号', key:'signal_direction', width:115, align:'center', render(r){
    if (!r.signal_direction) return h('span',{style:{color:'var(--c-text-faint)'}},'—')
    const isBuy = r.signal_direction === 'buy'
    const dateStr = (r.signal_date||'').slice(5)
    return h('span',{style:{color:isBuy?'#ef4444':'#10b981',fontSize:'12px',whiteSpace:'nowrap'}},
      (isBuy?'买入':'卖出') + ' ★'.repeat(r.signal_strength||0) + ' ' + dateStr)
  }},
  { title:'备注', key:'notes', minWidth:80, fixed:'right', render(r){return h('span',{style:{fontSize:'11px',color:'var(--c-text-dim)'}},r.notes||'')} },
  { title:'操作', width:140, fixed:'right', render(row){return h('span',[
    h(NButton,{size:'tiny',onClick:()=>showHistory(row.stock_code)},{default:()=>'详情'}),
    h(NButton,{size:'tiny',onClick:()=>{isAdding.value=false;editForm.code=row.stock_code;editForm.qty=row.quantity;editForm.cost=row.cost_price;editForm.date=null;editForm.note=row.notes||'';showEdit.value=true}},{default:()=>'编辑'}),
    h(NButton,{size:'tiny',type:'error',quaternary:true,style:{marginLeft:'4px'},onClick:()=>{deleteCode.value=row.stock_code;showDeleteConfirm.value=true}},{default:()=>'删除'})
  ])}}
]
function rowProps(row){return {style:'cursor:pointer',onClick:(e)=>{if(!e.target.closest('button'))emit('show-detail',row.stock_code)}}}
async function load(){loading.value=true;try{const r=await axios.get(API+'/api/portfolio');Object.assign(data,r.data)}catch(e){message.error('持仓加载失败，请刷新重试')}finally{loading.value=false}}
async function doAdd(){
  if(!editForm.code)return
  const auth2 = useAuthStore()
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
