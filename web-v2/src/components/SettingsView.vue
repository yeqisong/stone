<template>
<div>
  <div v-if="loading" style="text-align:center;padding:20px">加载中...</div>
  <template v-else>
    <h4 style="margin-bottom:8px;color:var(--c-text)">🎯 全局交易偏好</h4>
    <n-radio-group v-model:value="prefMode" @update:value="setPref">
      <n-radio-button value="left" label="左侧(早触发)" />
      <n-radio-button value="balanced" label="均衡" />
      <n-radio-button value="right" label="右侧(晚确认)" />
    </n-radio-group>
    <n-tag style="margin-left:8px" :type="prefMode==='left'?'error':prefMode==='right'?'success':'warning'">{{prefMode==='left'?'左侧':prefMode==='right'?'右侧':'均衡'}}</n-tag>
    
    <div style="display:flex;align-items:center;gap:8px;margin:12px 0 8px">
      <h4 style="margin:0;color:var(--c-text)">📐 基础指标配置</h4>
      <n-tag size="tiny" :bordered="false" type="default" v-if="saving">保存中...</n-tag>
    </div>
    <div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:10px">
      <div v-for="ind in indicators" :key="ind.name"
        style="background:var(--c-card-bg);border:1px solid var(--c-border);border-radius:10px;padding:14px"
        @mouseenter="hoverCard=ind.name" @mouseleave="hoverCard=''">
        <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:10px">
          <span style="font-weight:600;font-size:13px;color:var(--c-text)">{{ind.display}}</span>
          <n-button v-if="editMode!==ind.name && hoverCard===ind.name" size="tiny" text @click="startEditIndicator(ind)">✎</n-button>
        </div>
        <div style="display:flex;flex-direction:column;gap:6px">
          <!-- MA: 多值数组 -->
          <template v-if="ind.name==='ma'">
            <div v-for="(v,idx) in ind.params.periods" :key="idx" style="display:flex;align-items:center;gap:6px;font-size:12px">
              <span style="color:var(--c-text-dim);width:36px">MA{{[5,20,60,250][idx]}}</span>
              <template v-if="editMode===ind.name">
                <n-input-number v-model:value="ind.params.periods[idx]" size="tiny" style="flex:1" :min="2" :max="500" />
              </template>
              <template v-else>
                <span style="color:var(--c-text);font-weight:500;flex:1;text-align:right">{{v}}</span>
              </template>
            </div>
          </template>
          <!-- 其他指标 -->
          <template v-else>
            <div v-for="(v,k) in ind.params" :key="k" style="display:flex;align-items:center;gap:6px;font-size:12px">
              <span style="color:var(--c-text-dim);width:40px;white-space:nowrap">{{paramLabel(k)}}</span>
              <template v-if="editMode===ind.name">
                <n-input-number v-model:value="ind.params[k]" size="tiny" style="flex:1" :min="1" :max="k==='std_mult'?10:500" :step="k==='std_mult'?0.5:1" />
              </template>
              <template v-else>
                <span style="color:var(--c-text);font-weight:500;flex:1;text-align:right">{{v}}</span>
              </template>
            </div>
          </template>
        </div>
        <div v-if="editMode===ind.name" style="display:flex;justify-content:flex-end;gap:6px;margin-top:10px">
          <n-button size="tiny" @click="editMode=''">取消</n-button>
          <n-button size="tiny" type="primary" @click="saveIndicator(ind.name)">保存</n-button>
        </div>
      </div>
    </div>
    
    <h4 style="margin:12px 0 8px;color:var(--c-text)">🤖 DeepSeek API Key</h4>
    <form @submit.prevent="saveKey" style="display:inline">
      <n-space>
        <n-input v-model:value="dsKey" type="password" placeholder="sk-..." show-password style="width:300px" size="small" autocomplete="new-password" />
        <n-button type="primary" size="small" @click="saveKey">保存</n-button>
      </n-space>
    </form>
    <div style="font-size:11px;color:var(--c-text-dim);margin-top:4px">{{dsConfigured?'✅ 已配置':'⚠️ 未配置'}}</div>
  </template>
</div>
</template>
<script setup>
import { ref, onMounted } from 'vue'
import { NInputNumber, NCard, NRadioGroup, NRadioButton, NTag, NList, NListItem, NSwitch, NInput, NButton, NSpace, NSpin, useDialog } from 'naive-ui'
import axios from 'axios'
const API = window.location.origin
const loading = ref(true)
const dialog = useDialog()
const prefMode = ref('balanced')
const strategies = ref([])
const indicators = ref([])
const editMode = ref('')
const hoverCard = ref('')  // 鼠标悬停的卡片
const saving = ref(false)
const dsKey = ref(''), dsConfigured = ref(false)

function paramLabel(k) {
  const labels = {
    period:'周期', std_mult:'标准差', fast:'快线', slow:'慢线', signal:'信号',
    vol_ma_period:'量能MA', periods:'均线',
  }
  return labels[k]||k
}

function startEditIndicator(ind) {
  editMode.value = ind.name
}
async function saveIndicator(name) {
  const ind = indicators.value.find(i=>i.name===name)
  if(!ind) return
  saving.value = true
  try {
    await axios.post(API+'/api/settings/update_params', {strategy_name: name, params: ind.params})
    editMode.value = ''
  } catch(e) {
    dialog.warning({title:'保存失败', content: e.response?.data?.detail||e.message, positiveText:'确定'})
  } finally { saving.value = false }
}
async function load(){
  loading.value = true
  try{
    const r = await axios.get(API+'/api/settings')
    strategies.value = r.data.strategies||[]
    indicators.value = r.data.indicators||[]
    dsConfigured.value = r.data.deepseek_configured
    prefMode.value = r.data.preference?.mode||'balanced'
  }catch(e){} finally { loading.value = false }
}
async function setPref(m){
  try{ await axios.post(API+'/api/settings/preference',{mode:m}) }catch(e){}
}
async function toggle(name, v){
  try{ await axios.post(API+'/api/settings/toggle_strategy',{strategy_name:name,enabled:v}); load() }catch(e){}
}
async function saveKey(){
  if(!dsKey.value.trim()){
    dialog.warning({title:'提示',content:'请输入 API Key',positiveText:'确定'}); return
  }
  dialog.warning({
    title:'确认覆盖',
    content:'将覆盖之前的 DeepSeek API Key，确定继续？',
    positiveText:'确定', negativeText:'取消',
    onPositiveClick:()=>{ axios.post(API+'/api/settings/deepseek_key',{api_key:dsKey.value}).then(()=>{load()}).catch(()=>{}) }
  })
}
onMounted(load)
</script>
