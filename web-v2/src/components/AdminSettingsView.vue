<template>
  <div class="settings-page">
    <div class="settings-intro card">
      <div class="intro-icon"><AppIcon name="sliders" :size="18" /></div>
      <div class="intro-main">
        <div class="intro-title">全局系统设置</div>
        <div class="intro-desc">
          仅管理员可见。这里的修改会影响全市场共享信号与所有 LLM 链路；
          个人偏好请从右上角「个人设置」修改。
        </div>
      </div>
      <span class="role-badge">ADMIN</span>
    </div>

    <div class="settings-columns">
      <section class="card preference-card">
        <header class="section-head">
          <div class="section-title"><AppIcon name="target" :size="14" /> 全局交易偏好</div>
          <span class="section-tag">共享信号</span>
        </header>
        <p class="section-desc">
          用于 model_signal 阈值与飞书 AI 上下文，影响所有用户收到的共享买入信号。
        </p>
        <div class="pref-options">
          <button v-for="m in preferenceOptions" :key="m.value" type="button"
                  class="pref-option" :class="{ active: preferenceMode === m.value }"
                  @click="savePreference(m.value)">
            <span class="pref-name">{{ m.label }}</span>
            <span class="pref-desc">{{ m.desc }}</span>
          </button>
        </div>
        <div class="card-note">
          个人偏好不影响全局信号，请到右上角「个人设置」调整。
        </div>
      </section>

      <section class="card llm-card">
        <header class="section-head">
          <div class="section-title"><AppIcon name="zap" :size="14" /> LLM 运行时配置</div>
          <span class="status-badge" :class="{ on: llm.configured }">
            {{ llm.configured ? `已配置 · 尾号 ${llm.key_tail}` : '未配置' }}
          </span>
        </header>
        <p class="section-desc">新闻总结、雪球打分、问答 Agent 共用同一套运行时配置。</p>

        <div class="form-grid">
          <label class="field full">
            <span class="field-label">API Key</span>
            <n-input v-model:value="llm.api_key" type="password" show-password-on="click"
                     :placeholder="llm.configured ? '留空表示保留当前 Key' : '粘贴 API Key'" />
            <span class="field-hint">Key 明文不会回传前端；留空即保留已存值。</span>
          </label>
          <label class="field">
            <span class="field-label">模型 ID</span>
            <n-input v-model:value="llm.model" placeholder="如 glm-5.3-flash" />
          </label>
          <label class="field">
            <span class="field-label">Base URL</span>
            <n-input v-model:value="llm.base_url" placeholder="https://..." />
          </label>
          <label class="field">
            <span class="field-label">RPM 限速</span>
            <n-input-number v-model:value="llm.rpm" :min="0" style="width:100%" />
          </label>
          <label class="field">
            <span class="field-label">并发数</span>
            <n-input-number v-model:value="llm.workers" :min="0" style="width:100%" />
          </label>
        </div>

        <div class="form-actions">
          <n-button size="small" type="primary" @click="saveLlm">保存配置</n-button>
          <n-button size="small" :loading="testing" @click="testLlm">测试连通性</n-button>
          <span v-if="testResult" class="test-result"
                :class="{ ok: testOk === true, bad: testOk === false }">{{ testResult }}</span>
        </div>
      </section>
    </div>

    <section class="card monitor-card">
      <header class="section-head">
        <div class="section-title"><AppIcon name="activity" :size="14" /> 模型执行监测</div>
        <span v-if="llm.model" class="monitor-model">{{ llm.model }}</span>
        <span class="refresh-hint">5 秒刷新 · 进程内计数</span>
      </header>
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
    </section>

    <section class="card advanced-card">
      <button type="button" class="advanced-head" @click="settingsOpen = !settingsOpen">
        <span class="advanced-title"><AppIcon name="file-text" :size="14" /> 高级配置（只读）</span>
        <span class="advanced-toggle">{{ settingsOpen ? '收起' : '展开' }} {{ settingsOpen ? '▴' : '▾' }}</span>
      </button>
      <pre v-if="settingsOpen" class="settings-json">{{ settingsText }}</pre>
    </section>
  </div>
</template>

<style scoped>
.settings-page {
  padding: 16px;
  display: flex;
  flex-direction: column;
  gap: 12px;
  flex: 1;
  min-height: 0;
  overflow-y: auto;
}
.card {
  border: 1px solid var(--c-border);
  border-radius: 8px;
  background: var(--c-card-bg);
}
.settings-intro { display: flex; align-items: center; gap: 12px; padding: 14px 16px; }
.intro-icon {
  width: 36px; height: 36px; flex: none;
  display: flex; align-items: center; justify-content: center;
  border-radius: 8px; color: var(--c-accent); background: var(--c-accent-weak);
}
.intro-title { color: var(--c-text); font-size: 14px; font-weight: 600; }
.intro-desc { margin-top: 3px; color: var(--c-text-dim); font-size: 12px; line-height: 1.55; }
.role-badge {
  margin-left: auto; flex: none; padding: 1px 8px;
  border: 1px solid var(--c-border); border-radius: 10px;
  color: var(--c-text-faint); font: 10px/18px ui-monospace, Consolas, monospace;
}

.settings-columns {
  display: grid;
  grid-template-columns: 340px minmax(0, 1fr);
  gap: 12px;
  align-items: stretch;
}
.preference-card,
.llm-card { padding: 14px 16px; }
.preference-card { display: flex; flex-direction: column; }
.section-head { display: flex; align-items: center; gap: 8px; min-width: 0; }
.section-title { display: flex; align-items: center; gap: 5px; color: var(--c-text); font-size: 13px; font-weight: 600; }
.section-tag { margin-left: auto; padding: 0 6px; border: 1px solid var(--c-border-light); border-radius: 3px; color: var(--c-text-faint); font-size: 10px; line-height: 18px; }
.section-desc { margin: 6px 0 12px; color: var(--c-text-dim); font-size: 11.5px; line-height: 1.6; }

.pref-options { display: flex; flex-direction: column; gap: 6px; }
.pref-option {
  display: flex; align-items: baseline; gap: 10px;
  width: 100%; padding: 9px 11px;
  border: 1px solid var(--c-border); border-radius: 6px;
  background: var(--c-bg); color: var(--c-text);
  font-family: inherit; text-align: left; cursor: pointer;
}
.pref-option:hover { border-color: var(--c-accent); }
.pref-option.active {
  border-color: var(--c-accent);
  background: var(--c-accent-weak);
  box-shadow: inset 3px 0 0 var(--c-accent);
}
.pref-name { flex: none; font-size: 12.5px; font-weight: 600; }
.pref-desc { color: var(--c-text-dim); font-size: 11px; line-height: 1.4; }
.card-note { margin-top: auto; padding-top: 12px; color: var(--c-text-faint); font-size: 10.5px; line-height: 1.5; }

.status-badge {
  margin-left: auto; flex: none; padding: 0 7px; border-radius: 10px;
  border: 1px solid var(--c-border); color: var(--c-text-faint);
  font-size: 10px; line-height: 18px;
}
.status-badge.on { border-color: color-mix(in srgb, var(--c-down) 35%, transparent); color: var(--c-down); background: color-mix(in srgb, var(--c-down) 8%, transparent); }
.form-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px 12px; }
.field { display: flex; flex-direction: column; gap: 4px; min-width: 0; }
.field.full { grid-column: 1 / -1; }
.field-label { color: var(--c-text-dim); font-size: 11.5px; }
.field-hint { color: var(--c-text-faint); font-size: 10.5px; line-height: 1.45; }
.form-actions { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin-top: 14px; }
.test-result { font-size: 11.5px; color: var(--c-text-dim); }
.test-result.ok { color: var(--c-down); }
.test-result.bad { color: var(--c-up); }

.monitor-card { padding: 14px 16px; }
.monitor-model {
  margin-left: auto; padding: 0 6px; border: 1px solid var(--c-border-light);
  border-radius: 3px; color: var(--c-text-dim);
  font: 10px/18px ui-monospace, Consolas, monospace;
}
.refresh-hint { color: var(--c-text-faint); font-size: 10.5px; white-space: nowrap; }
.metric-grid { display: grid; grid-template-columns: repeat(6, minmax(0, 1fr)); gap: 6px; margin-top: 12px; }
.metric {
  min-width: 0; padding: 8px 10px;
  border: 1px solid var(--c-border-light);
  border-radius: 6px; background: var(--c-bg);
}
.metric-label { color: var(--c-text-dim); font-size: 10.5px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.metric-value { margin-top: 3px; color: var(--c-text); font-size: 17px; font-weight: 600; line-height: 1.25; }
.metric-value span { margin-left: 2px; color: var(--c-text-faint); font-size: 10.5px; font-weight: 400; }
.metric-errors { display: flex; flex-direction: column; gap: 2px; margin-top: 10px; color: var(--c-up); font-size: 10.5px; line-height: 1.5; }

.advanced-card { overflow: hidden; }
.advanced-head {
  display: flex; align-items: center; justify-content: space-between;
  width: 100%; padding: 11px 16px; border: 0; background: transparent;
  color: var(--c-text); font-family: inherit; cursor: pointer;
}
.advanced-title { display: flex; align-items: center; gap: 6px; font-size: 12px; font-weight: 600; }
.advanced-toggle { color: var(--c-text-faint); font-size: 11px; }
.settings-json {
  margin: 0; padding: 12px 16px 14px;
  border-top: 1px solid var(--c-border-light);
  color: var(--c-text-dim); background: var(--c-bg);
  font: 11px/1.6 ui-monospace, SFMono-Regular, Consolas, monospace;
  white-space: pre-wrap; word-break: break-all;
  max-height: 320px; overflow: auto;
}

@media (max-width: 1100px) {
  .settings-columns { grid-template-columns: 1fr; }
  .metric-grid { grid-template-columns: repeat(3, minmax(0, 1fr)); }
}
@media (max-width: 600px) {
  .settings-page { padding: 10px; }
  .settings-intro { align-items: flex-start; }
  .role-badge { display: none; }
  .form-grid { grid-template-columns: 1fr; }
  .metric-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}
</style>

<script setup>
import { onMounted, onUnmounted, ref } from 'vue'
import axios from 'axios'
import { NButton, NInput, NInputNumber, useMessage } from 'naive-ui'
import AppIcon from './AppIcon.vue'

const message = useMessage()
const preferenceMode = ref('balanced')
const preferenceOptions = [
  { label: '左侧', value: 'left', desc: '回撤后寻找机会' },
  { label: '均衡', value: 'balanced', desc: '左侧与右侧兼顾' },
  { label: '右侧', value: 'right', desc: '等待趋势确认' },
]
const llm = ref({ configured: false, key_tail: '', api_key: '', base_url: '', model: '', rpm: 0, workers: 0 })
const llmMetrics = ref({})
const testResult = ref('')
const testOk = ref(null)
const testing = ref(false)
const settingsText = ref('')
const settingsOpen = ref(false)
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
  testing.value = true
  testResult.value = '测试中…'
  testOk.value = null
  try {
    const r = await axios.post('/api/llm_config/test', {
      api_key: llm.value.api_key || '', base_url: llm.value.base_url || '',
      model: llm.value.model || '', rpm: 0, workers: 0,
    })
    testOk.value = !!r.data.ok
    testResult.value = r.data.ok ? `连通正常 ${r.data.latency_ms || ''}ms` : (r.data.error || '失败')
  } catch (e) {
    testOk.value = false
    testResult.value = e.response?.data?.detail || '测试失败'
  } finally {
    testing.value = false
  }
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
