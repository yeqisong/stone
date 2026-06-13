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
    
    <h4 style="margin:12px 0 8px;color:var(--c-text)">📊 策略启停</h4>
    <n-list>
      <template v-for="s in strategies" :key="s.name">
      <n-list-item v-if="s.name!=='global_preference'">
        <div style="display:flex;align-items:center;justify-content:space-between;width:100%;gap:10px">
          <div style="flex:1;min-width:0">
            <b>{{s.display||s.name}}</b>
            <div v-if="editing!==s.name" style="font-size:11px;color:var(--c-text-dim);word-break:break-all">{{JSON.stringify(s.params)}}</div>
            <n-input v-else v-model:value="editText" type="textarea" :rows="3" size="small" style="font-family:monospace;font-size:11px;margin-top:4px" />
          </div>
          <div style="display:flex;align-items:center;gap:6px;flex-shrink:0">
            <n-switch :value="s.enabled" @update:value="v=>toggle(s.name,v)" />
            <n-button v-if="editing!==s.name" size="tiny" @click="startEdit(s)">✎</n-button>
            <template v-else>
              <n-button size="tiny" type="primary" @click="saveParams(s.name)">保存</n-button>
              <n-button size="tiny" @click="editing=''">取消</n-button>
            </template>
          </div>
        </div>
      </n-list-item>
      </template>
    </n-list>
    
    <h4 style="margin:12px 0 8px;color:var(--c-text)">🤖 DeepSeek API Key</h4>
    <n-space>
      <n-input v-model:value="dsKey" type="password" placeholder="sk-..." show-password style="width:300px" size="small" />
      <n-button type="primary" size="small" @click="saveKey">保存</n-button>
    </n-space>
    <div style="font-size:11px;color:var(--c-text-dim);margin-top:4px">{{dsConfigured?'✅ 已配置':'⚠️ 未配置'}}</div>
  </template>
</div>
</template>
<script setup>
import { ref, onMounted } from 'vue'
import { NCard, NRadioGroup, NRadioButton, NTag, NList, NListItem, NSwitch, NInput, NButton, NSpace, NSpin, useDialog } from 'naive-ui'
import axios from 'axios'
const API = window.location.origin
const loading = ref(true)
const dialog = useDialog()
const prefMode = ref('balanced')
const strategies = ref([])
const dsKey = ref(''), dsConfigured = ref(false)
const editing = ref('')
const editText = ref('')

function startEdit(s) {
  editing.value = s.name
  editText.value = JSON.stringify(s.params, null, 2)
}
async function saveParams(name) {
  try {
    const params = JSON.parse(editText.value)
    await axios.post(API+'/api/settings/update_params', {strategy_name: name, params})
    editing.value = ''
    await load()
  } catch(e) {
    dialog.warning({title:'参数格式错误', content: e.message, positiveText:'确定'})
  }
}
async function load(){
  loading.value = true
  try{
    const r = await axios.get(API+'/api/settings')
    strategies.value = r.data.strategies||[]
    dsConfigured.value = r.data.deepseek_configured
    const p = (r.data.strategies||[]).find(s=>s.name==='global_preference')
    if(p) prefMode.value = p.params?.mode||'balanced'
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
