<template>
  <div v-if="show" class="ps-mask" @click.self="close">
    <div class="ps-card" role="dialog" aria-modal="true" aria-label="个人设置">
      <div class="ps-head">
        <div>
          <div class="ps-title">个人设置</div>
          <div class="ps-sub">只影响你自己的展示与 AI 教练，不影响其他用户</div>
        </div>
        <button class="ps-x" type="button" aria-label="关闭" @click="close">×</button>
      </div>

      <div class="ps-section">
        <div class="ps-label">我的交易偏好</div>
        <div class="ps-options">
          <button v-for="m in options" :key="m.value" type="button"
                  class="ps-option" :class="{ active: mode === m.value }"
                  :disabled="loading" @click="mode = m.value">
            <span class="ps-option-name">{{ m.label }}</span>
            <span class="ps-option-desc">{{ m.desc }}</span>
          </button>
        </div>
        <div class="ps-global">
          全局信号偏好：<b>{{ globalLabel }}</b>
          <span>由管理员在「系统设置」维护，用于全市场共享信号生成；个人偏好不会改变它。</span>
        </div>
      </div>

      <div class="ps-actions">
        <span class="ps-error">{{ error }}</span>
        <n-button size="small" @click="close">取消</n-button>
        <n-button size="small" type="primary" :loading="saving" @click="save">保存</n-button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.ps-mask { position: fixed; inset: 0; z-index: 3000; display: flex; align-items: center; justify-content: center; padding: 20px; background: rgba(0,0,0,.38); }
.ps-card { width: 520px; max-width: 100%; padding: 20px; border: 1px solid var(--c-border, #ddd); border-radius: 8px; background: var(--c-card-bg, #fff); color: var(--c-text, #222); box-shadow: 0 18px 50px rgba(0,0,0,.2); }
.ps-head { display: flex; align-items: flex-start; gap: 12px; }
.ps-title { font: 600 17px var(--font-sans, sans-serif); }
.ps-sub { margin-top: 4px; color: var(--c-text-dim, #666); font-size: 12px; }
.ps-x { margin-left: auto; width: 28px; height: 28px; border: 0; background: transparent; color: var(--c-text-dimmer, #888); font-size: 20px; line-height: 1; cursor: pointer; }
.ps-x:hover { color: var(--c-text, #222); }
.ps-section { margin-top: 18px; }
.ps-label { margin-bottom: 8px; color: var(--c-text, #222); font-size: 13px; font-weight: 600; }
.ps-options { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 8px; }
.ps-option { display: flex; flex-direction: column; gap: 4px; padding: 10px 12px; border: 1px solid var(--c-border, #ddd); border-radius: 6px; background: var(--c-bg, #fafafa); color: var(--c-text, #222); font-family: inherit; text-align: left; cursor: pointer; }
.ps-option:hover { border-color: var(--c-accent, #8f1a2b); }
.ps-option.active { border-color: var(--c-accent, #8f1a2b); background: var(--c-accent-weak, #fbeef0); box-shadow: inset 0 0 0 1px var(--c-accent, #8f1a2b); }
.ps-option:disabled { opacity: .6; cursor: not-allowed; }
.ps-option-name { font-size: 13px; font-weight: 600; }
.ps-option-desc { color: var(--c-text-dim, #777); font-size: 11px; line-height: 1.45; }
.ps-global { margin-top: 10px; padding: 9px 11px; border-radius: 5px; background: var(--c-input-bg, #f5f5f5); color: var(--c-text-dim, #666); font-size: 11.5px; line-height: 1.6; }
.ps-global b { color: var(--c-text, #222); }
.ps-global span { display: block; margin-top: 2px; color: var(--c-text-faint, #999); }
.ps-actions { display: flex; align-items: center; justify-content: flex-end; gap: 8px; margin-top: 20px; }
.ps-error { margin-right: auto; color: var(--c-error, #b3261e); font-size: 12px; }
@media (max-width: 560px) {
  .ps-options { grid-template-columns: 1fr; }
}
</style>

<script setup>
import { computed, ref, watch } from 'vue'
import axios from 'axios'
import { NButton } from 'naive-ui'

const props = defineProps({ show: { type: Boolean, default: false } })
const emit = defineEmits(['close'])

const mode = ref('balanced')
const globalMode = ref('balanced')
const loading = ref(false)
const saving = ref(false)
const error = ref('')

const options = [
  { value: 'left', label: '左侧', desc: '偏左侧交易，关注回撤后的机会' },
  { value: 'balanced', label: '均衡', desc: '左侧与右侧兼顾' },
  { value: 'right', label: '右侧', desc: '偏右侧交易，等待趋势确认' },
]
const labelOf = m => options.find(x => x.value === m)?.label || m
const globalLabel = computed(() => labelOf(globalMode.value))

async function load() {
  loading.value = true
  error.value = ''
  try {
    const r = await axios.get('/api/me/settings')
    mode.value = r.data?.trade_preference || 'balanced'
    globalMode.value = r.data?.global_preference || 'balanced'
  } catch (e) {
    error.value = e.response?.data?.detail || '加载个人设置失败'
  } finally {
    loading.value = false
  }
}

async function save() {
  saving.value = true
  error.value = ''
  try {
    await axios.put('/api/me/settings', { trade_preference: mode.value })
    emit('close')
  } catch (e) {
    error.value = e.response?.data?.detail || '保存失败'
  } finally {
    saving.value = false
  }
}

watch(() => props.show, v => { if (v) load() })
function close() { emit('close') }
</script>
