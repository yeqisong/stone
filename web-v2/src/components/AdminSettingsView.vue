<template>
  <div style="padding:12px 16px;display:flex;flex-direction:column;gap:12px;flex:1;min-height:0;overflow-y:auto">
    <div style="padding:10px 12px;border:1px solid var(--c-border);border-radius:var(--radius);background:var(--c-card-bg);font-size:12px;color:var(--c-text-dim)">
      仅管理员可见。个人偏好请从右上角用户菜单进入“个人设置/修改密码”；此处修改的是全局系统配置。
    </div>

    <div style="border:1px solid var(--c-border);border-radius:var(--radius);padding:12px">
      <div style="font-size:13px;font-weight:600;margin-bottom:10px">全局交易偏好</div>
      <n-button-group size="small">
        <n-button v-for="m in preferenceOptions" :key="m.value" :type="preferenceMode===m.value?'primary':'default'" @click="savePreference(m.value)">{{ m.label }}</n-button>
      </n-button-group>
    </div>

    <div style="border:1px solid var(--c-border);border-radius:var(--radius);padding:12px">
      <div style="font-size:13px;font-weight:600;margin-bottom:10px">LLM 运行时配置</div>
      <n-form label-placement="left" label-width="100">
        <n-form-item label="API Key">
          <n-input v-model:value="llm.api_key" type="password" :placeholder="llm.configured ? ('已配置，尾号 ' + llm.key_tail + '；留空保留') : '未配置'" />
        </n-form-item>
        <n-form-item label="Base URL"><n-input v-model:value="llm.base_url" /></n-form-item>
        <n-form-item label="模型"><n-input v-model:value="llm.model" /></n-form-item>
        <n-form-item label="RPM"><n-input-number v-model:value="llm.rpm" :min="0" /></n-form-item>
        <n-form-item label="并发"><n-input-number v-model:value="llm.workers" :min="0" /></n-form-item>
      </n-form>
      <n-space>
        <n-button size="small" type="primary" @click="saveLlm">保存</n-button>
        <n-button size="small" @click="testLlm">测试连通性</n-button>
        <span style="font-size:12px;color:var(--c-text-dim)">{{ testResult }}</span>
      </n-space>
    </div>

    <div class="settings-card">
      <div class="card-title">
        模型执行监测
        <span class="card-sub">LLM 运行时指标 · 5 秒刷新 / 进程内计数</span>
        <span v-if="llm.model" class="card-model">{{ llm.model }}</span>
      </div>
      <div class="metric-grid">
        <div class="metric">
          <div class="metric-label">在途并发</div>
          <div class="metric-value">{{ llmMetrics.active ?? '-' }}<span>/ {{ llmMetrics.config?.workers ?? '-' }}</span></div>
        </div>
        <div class="metric">
          <div class="metric-label">近 1 分钟</div>
          <div class="metric-value">{{ llmMetrics.rpm_1m ?? '-' }}<span>/ {{ llmMetrics.config?.rpm ?? '-' }} RPM</span></div>
        </div>
        <div class="metric">
          <div class="metric-label">峰值并发</div>
          <div class="metric-value">{{ llmMetrics.peak ?? '-' }}</div>
        </div>
        <div class="metric">
          <div class="metric-label">成功 / 失败</div>
          <div class="metric-value">{{ llmMetrics.ok ?? 0 }}<span>/ {{ llmMetrics.fail ?? 0 }}</span></div>
        </div>
        <div class="metric">
          <div class="metric-label">平均时延</div>
          <div class="metric-value">{{ llmMetrics.avg_ms ?? '-' }}<span>ms</span></div>
        </div>
        <div class="metric">
          <div class="metric-label">P95 时延</div>
          <div class="metric-value">{{ llmMetrics.p95_ms ?? '-' }}<span>ms</span></div>
        </div>
      </div>
      <div v-if="llmMetrics.recent_errors?.length" class="metric-errors">
        <span v-for="e in llmMetrics.recent_errors.slice(-3)" :key="e.at + e.code">
          {{ e.at }} / {{ e.code }} / {{ e.msg }}
        </span>
      </div>
    </div>

    <div style="border:1px solid var(--c-border);border-radius:var(--radius);padding:12px">
      <div style="font-size:13px;font-weight:600;margin-bottom:10px">系统配置</div>
      <pre style="white-space:pre-wrap;word-break:break-all;font-size:12px;color:var(--c-text-dim);max-height:260px;overflow:auto">{{ settingsText }}</pre>
    </div>
  </div>
</template>

<style scoped>
.settings-card { border: 1px solid var(--c-border); border-radius: var(--radius); padding: 12px; }
.card-title { display: flex; align-items: baseline; gap: 8px; margin-bottom: 10px; font-size: 13px; font-weight: 600; }
.card-sub { color: var(--c-text-faint); font-size: 11px; font-weight: 400; }
.card-model { margin-left: auto; padding: 0 6px; border: 1px solid var(--c-border-light); border-radius: 3px; color: var(--c-text-dim); font: 10px/18px ui-monospace, Consolas, monospace; }
.metric-grid { display: grid; grid-template-columns: repeat(6, minmax(0, 1fr)); gap: 6px; }
.metric { min-width: 0; padding: 7px 9px; border: 1px solid var(--c-card-bg-hover); border-radius: var(--radius-sm); background: var(--c-bg); }
.metric-label { color: var(--c-text-dim); font-size: 11px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.metric-value { margin-top: 2px; color: var(--c-text); font-size: 16px; font-weight: 600; line-height: 1.3; }
.metric-value span { margin-left: 2px; color: var(--c-text-faint); font-size: 11px; font-weight: 400; }
.metric-errors { display: flex; flex-direction: column; gap: 1px; margin-top: 8px; color: var(--c-up); font-size: 11px; line-height: 1.5; }
@media (max-width: 1100px) { .metric-grid { grid-template-columns: repeat(3, minmax(0, 1fr)); } }
@media (max-width: 600px) { .metric-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
</style>

<script setup>
import { onMounted, onUnmounted, ref } from 'vue'
import axios from 'axios'
import {
  NButton, NButtonGroup, NForm, NFormItem, NInput, NInputNumber, NSpace,
  useMessage,
} from 'naive-ui'

const message = useMessage()
const preferenceMode = ref('balanced')
const preferenceOptions = [
  { label: '左侧', value: 'left' }, { label: '均衡', value: 'balanced' }, { label: '右侧', value: 'right' },
]
const llm = ref({ configured: false, key_tail: '', api_key: '', base_url: '', model: '', rpm: 0, workers: 0 })
const llmMetrics = ref({})
const testResult = ref('')
const settingsText = ref('')
let metricsTimer = null

async function load() {
  try {
    const [s, l] = await Promise.all([axios.get('/api/settings'), axios.get('/api/llm_config')])
    preferenceMode.value = s.data.preference?.mode || 'balanced'
    settingsText.value = JSON.stringify(s.data, null, 2)
    llm.value = { ...l.data, api_key: '' }
  } catch (e) {
    message.error(e.response?.data?.detail || '加载系统配置失败')
  }
}
async function savePreference(mode) {
  try {
    await axios.post('/api/settings/preference', { mode })
    preferenceMode.value = mode
    message.success('已保存全局偏好')
  } catch (e) { message.error(e.response?.data?.detail || '保存失败') }
}
async function saveLlm() {
  try {
    await axios.post('/api/llm_config', {
      api_key: llm.value.api_key || '', base_url: llm.value.base_url || '',
      model: llm.value.model || '', rpm: Number(llm.value.rpm || 0),
      workers: Number(llm.value.workers || 0),
    })
    message.success('LLM 配置已保存')
    load()
  } catch (e) { message.error(e.response?.data?.detail || '保存失败') }
}
async function testLlm() {
  testResult.value = '测试中…'
  try {
    const r = await axios.post('/api/llm_config/test', {
      api_key: llm.value.api_key || '', base_url: llm.value.base_url || '',
      model: llm.value.model || '', rpm: 0, workers: 0,
    })
    testResult.value = r.data.ok ? `连通正常 ${r.data.latency_ms || ''}ms` : (r.data.error || '失败')
  } catch (e) { testResult.value = e.response?.data?.detail || '测试失败' }
}
async function loadLlmMetrics() {
  try { llmMetrics.value = (await axios.get('/api/llm_config/metrics')).data } catch (_) {}
}
onMounted(() => {
  load()
  loadLlmMetrics()
  metricsTimer = setInterval(loadLlmMetrics, 5000)
})
onUnmounted(() => { if (metricsTimer) clearInterval(metricsTimer) })
</script>
