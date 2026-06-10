<template>
<n-space vertical size="medium">
  <n-card v-if="data.count" size="small" style="text-align:center">
    <div style="display:flex;gap:60px;justify-content:center;padding:8px 0">
      <div><div style="font-size:11px;color:rgba(255,255,255,.45)">持仓</div><div style="font-size:22px;font-weight:700;color:#fff">{{data.count}}</div></div>
      <div><div style="font-size:11px;color:rgba(255,255,255,.45)">市值</div><div style="font-size:22px;font-weight:700;color:#fff">¥{{fmt(data.total_value)}}</div></div>
      <div><div style="font-size:11px;color:rgba(255,255,255,.45)">盈亏</div><div style="font-size:22px;font-weight:700" :style="{color:data.total_pnl>=0?'#ef4444':'#10b981'}">¥{{fmt(data.total_pnl)}}</div></div>
    </div>
  </n-card>

  <n-card v-if="showAdd" title="新增持仓" size="small">
    <n-space align="flex-end" wrap>
      <n-input v-model:value="pf.code" placeholder="000001" style="width:100px" size="small" />
      <n-input-number v-model:value="pf.qty" :min="1" style="width:90px" size="small" />
      <n-input-number v-model:value="pf.cost" :min="0" :step="0.01" style="width:100px" size="small" />
      <n-button type="primary" size="small" @click="doAdd">添加</n-button>
      <n-button size="small" @click="showAdd=false">取消</n-button>
    </n-space>
  </n-card>
  <n-button v-else type="primary" ghost size="small" @click="showAdd=true">+ 新增持仓</n-button>

  <n-spin v-if="loading" />
  <template v-else>
    <n-card v-if="data.positions&&data.positions.length" title="持仓明细" size="small">
      <n-data-table :columns="columns" :data="data.positions" size="small" :row-props="rowProps" />
    </n-card>
    <n-empty v-else description="暂无持仓" />
  </template>
</n-space>

<n-modal v-model:show="showEdit" title="✎ 编辑持仓" preset="card" style="width:400px">
  <n-form label-width="70px" size="small">
    <n-form-item label="数量"><n-input-number v-model:value="editForm.qty" :min="0" /></n-form-item>
    <n-form-item label="成本价"><n-input-number v-model:value="editForm.cost" :min="0" :step="0.01" /></n-form-item>
    <n-form-item label="备注"><n-input v-model:value="editForm.note" type="textarea" :rows="2" /></n-form-item>
  </n-form>
  <template #footer>
    <n-space justify="flex-end"><n-button @click="showEdit=false">取消</n-button><n-button type="primary" @click="doSaveEdit">保存</n-button></n-space>
  </template>
</n-modal>
<n-modal v-model:show="showDeleteConfirm" preset="dialog" title="确认删除" type="warning" positive-text="确认删除" negative-text="取消" @positive-click="confirmDelete">
  <p>确定删除该持仓？删除后数据不可恢复。</p>
</n-modal>
</template>

<script setup>
import { ref, reactive, h, onMounted } from 'vue'
import { useMessage, NButton, NSpace, NCard, NDataTable, NModal, NForm, NFormItem, NInput, NInputNumber, NEmpty, NSpin } from 'naive-ui'
import axios from 'axios'
const emit = defineEmits(['show-detail'])
const API = window.location.origin
const message = useMessage()
const data = reactive({positions:[], count:0, total_value:0, total_pnl:0})
const showAdd = ref(false), showEdit = ref(false), showDeleteConfirm = ref(false), loading = ref(true)
const pf = reactive({code:'', qty:100, cost:0, note:''})
const editForm = reactive({code:'', qty:0, cost:0, note:''})
const deleteCode = ref('')
const fmt = v => v!=null?Number(v).toLocaleString():'0'
const columns = [
  { title:'代码', key:'stock_code', width:85, render(r){return h('span',{style:{color:'var(--n-color-target)'}},r.stock_code)} },
  { title:'名称', key:'stock_name', width:100 },
  { title:'数量', key:'quantity', width:70, align:'right' },
  { title:'成本', width:95, align:'right', render(r){return '¥'+(r.cost_price||0).toFixed(2)} },
  { title:'现价', width:95, align:'right', render(r){return '¥'+(r.current_price||0).toFixed(2)} },
  { title:'市值', width:105, align:'right', render(r){return '¥'+fmt(r.market_value)} },
  { title:'盈亏', minWidth:120, align:'right', render(r){
    const pnl=r.pnl||0, pct=(r.pnl_pct||0).toFixed(1)
    return h('span',{style:{color:pnl>=0?'#ef4444':'#10b981'}}, `¥${fmt(pnl)} (${pnl>=0?'+':''}${pct}%)`)
  }},
  { title:'备注', key:'notes', minWidth:80, render(r){return h('span',{style:{fontSize:'11px',color:'rgba(255,255,255,.45)'}},r.notes||'')} },
  { title:'操作', width:120, fixed:'right', render(row){return h('span',[
    h(NButton,{size:'tiny',onClick:()=>{editForm.code=row.stock_code;editForm.qty=row.quantity;editForm.cost=row.cost_price;editForm.note=row.notes||'';showEdit.value=true}},{default:()=>'编辑'}),
    h(NButton,{size:'tiny',type:'error',quaternary:true,style:{marginLeft:'6px'},onClick:()=>{deleteCode.value=row.stock_code;showDeleteConfirm.value=true}},{default:()=>'删除'})
  ])}}
]
function rowProps(row){return {style:'cursor:pointer',onClick:()=>emit('show-detail',row.stock_code)}}
async function load(){loading.value=true;try{const r=await axios.get(API+'/api/portfolio');Object.assign(data,r.data)}catch(e){}finally{loading.value=false}}
async function doAdd(){
  if(!pf.code)return
  try{await axios.post(API+'/api/portfolio',{stock_code:pf.code,quantity:pf.qty,cost_price:pf.cost,notes:pf.note});showAdd.value=false;pf.code='';pf.qty=100;pf.cost=0;pf.note='';await load();message.success('添加成功')}catch(e){message.error('添加失败: '+ (e.response?.data?.detail||e.message))}
}
async function doSaveEdit(){
  try{await axios.put(API+'/api/portfolio/'+editForm.code,{quantity:editForm.qty,cost_price:editForm.cost,notes:editForm.note});showEdit.value=false;await load();message.success('已保存')}catch(e){message.error('保存失败')}
}
async function confirmDelete(){
  try{await axios.delete(API+'/api/portfolio/'+deleteCode.value);showDeleteConfirm.value=false;await load();message.success('已删除')}catch(e){message.error('删除失败')}
}
onMounted(load)
</script>
