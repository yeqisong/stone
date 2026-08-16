<template>
<n-modal v-model:show="visible" preset="card" :title="title" style="width:480px;max-width:92vw" :mask-closable="!running">
  <n-space vertical size="medium">
    <!-- 日期选择（基本面隐藏） -->
    <n-space v-if="showDatePicker" vertical size="small">
      <div style="font-size:12px;color:var(--c-text-dim);margin-bottom:-4px">起始日期</div>
      <n-date-picker v-model:formatted-value="startDate" type="date" value-format="yyyy-MM-dd" :is-date-disabled="d => d > Date.now()" style="width:100%" />
      <div style="font-size:12px;color:var(--c-text-dim);margin-bottom:-4px">截止日期</div>
      <n-date-picker v-model:formatted-value="endDate" type="date" value-format="yyyy-MM-dd" :is-date-disabled="d => d > Date.now()" style="width:100%" />
    </n-space>

    <!-- 强制更新开关 -->
    <div style="display:flex;align-items:center;justify-content:space-between">
      <div>
        <div style="font-size:13px;color:var(--c-text)">{{ forceUpdate ? '🔴 强制更新' : '🟢 断点续传' }}</div>
        <div style="font-size:11px;color:var(--c-text-dim)">{{ forceUpdate ? '重新下载并覆盖已有数据' : '跳过已有数据，仅补全缺失部分' }}</div>
      </div>
      <n-switch v-model:value="forceUpdate" />
    </div>

    <!-- 并发数 -->
    <div style="display:flex;align-items:center;justify-content:space-between">
      <div>
        <div style="font-size:13px;color:var(--c-text)">并发数</div>
        <div style="font-size:11px;color:var(--c-text-dim)">每批拉取的股票数量，默认20。减小可避免OOM</div>
      </div>
      <n-input-number v-model:value="batchSize" :min="1" :max="500" size="small" style="width:80px" />
    </div>

    <!-- 提示信息 -->
    <div v-if="type==='fund'" style="font-size:11px;color:var(--c-text-dim);padding:4px 8px;background:rgba(32,128,240,0.06);border-radius:6px">
      基本面按季度回填，日期范围自动对齐到季度起止。非强制模式跳过已有季度数据。
    </div>
  </n-space>

  <template #footer>
    <n-button @click="onStart" type="primary" :disabled="submitted" :loading="submitted">
      {{ submitted ? '已提交' : (props.type === 'stock_master' ? '开始更新' : '开始补数') }}
    </n-button>
    <n-button @click="onClose">关闭</n-button>
  </template>
</n-modal>
</template>

<script setup>
import { ref, computed, watch } from 'vue'
import { NModal, NSpace, NButton, NSwitch, NDatePicker, NInputNumber, useMessage } from 'naive-ui'
import axios from 'axios'

const props = defineProps({
  show: { type: Boolean, default: false },
  type: { type: String, required: true },  // kline | index | etf | fund | indicator
})

const emit = defineEmits(['close', 'started'])

const message = useMessage()
const API = window.location.origin

const LABELS = {
  kline: '个股日K线', index: '指数日K线', etf: 'ETF日K线',
  fund: '基本面', calendar: '日历统计',
}

const title = computed(() => `📥 ${LABELS[props.type] || props.type} 补数`)
const showDatePicker = computed(() => true)

const visible = computed({
  get: () => props.show,
  set: (v) => { if (!v) onClose() },
})

// 默认日期：5年前 ~ 今天
const today = new Date().toISOString().slice(0, 10)
const fiveYearsAgo = `${new Date().getFullYear() - 5}${today.slice(4)}`

const startDate = ref(fiveYearsAgo)
const endDate = ref(today)
const forceUpdate = ref(false)
const batchSize = ref(20)
const submitted = ref(false)
const running = ref(false)

function onStart() {
  if (showDatePicker.value) {
    if (startDate.value > endDate.value) {
      message.warning('起始日期不能晚于截止日期')
      return
    }
    if (endDate.value > today) {
      message.warning('截止日期不能晚于今天')
      return
    }
  }

  submitted.value = true
  axios.post(API + '/api/data_status/backfill', {
    type: props.type,
    start_date: showDatePicker.value ? startDate.value : undefined,
    end_date: showDatePicker.value ? endDate.value : undefined,
    force: forceUpdate.value,
    batch_size: batchSize.value,
  }).then(r => {
    if (r.data && r.data.ok) {
      message.success(`补数任务已提交（${LABELS[props.type]}）`)
      emit('started', { type: props.type, taskId: r.data.task_id })
      onClose()
    } else if (r.data && r.data.busy) {
      message.warning(r.data.error || '已有补数任务运行中')
    } else {
      message.error(r.data?.error || '提交失败')
    }
  }).catch(e => {
    message.error('提交失败: ' + (e.response?.data?.error || e.message))
  }).finally(() => {
    submitted.value = false
  })
}

function onClose() {
  emit('close')
}

// 重置状态
watch(() => props.show, (v) => {
  if (v) {
    submitted.value = false
  }
})
</script>