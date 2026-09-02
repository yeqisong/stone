<template>
  <n-auto-complete v-model:value="text" :options="options" :size="size" :placeholder="placeholder"
    :clearable="true" :style="{ width }" :loading="fetching || undefined"
    @update:value="onInput" @select="onSelect" @keyup.enter="$emit('enter')" />
</template>

<script setup>
import { ref, watch } from 'vue'
import { NAutoComplete } from 'naive-ui'
import axios from 'axios'

/**
 * 股票输入建议框：数字=代码前缀 / 中文=名称前缀 / 字母=拼音首字母前缀，Top10。
 * v-model:value 绑定代码；@select 回传 {code, name}（选中）；@enter 回车。
 */
const props = defineProps({
  modelValue: { type: String, default: '' },
  size: { type: String, default: undefined },
  placeholder: { type: String, default: '代码/名称/拼音首字母' },
  width: { type: [String, Number], default: '100%' },
})
const emit = defineEmits(['update:value', 'select', 'enter'])

const API = window.location.origin
const text = ref(props.modelValue)
const options = ref([])
const fetching = ref(false)
// 注意：loading 传 undefined（而非 false）——naive Input 对"已定义的 loading"会常驻渲染
// 一个 spinner 占位符，把清除 ✕ 顶离右缘一个图标宽
let timer = null
let lastQ = null

watch(() => props.modelValue, (v) => { text.value = v })

function onInput(v) {
  text.value = v
  emit('update:value', v)
  if (timer) clearTimeout(timer)
  const q = (v || '').trim()
  if (!q || lastQ === q) { if (!q) options.value = []; return }
  timer = setTimeout(async () => {
    lastQ = q
    fetching.value = true
    try {
      const r = await axios.get(API + '/api/stocks/suggest', { params: { q, limit: 10 } })
      if ((text.value || '').trim() !== q) return   // 竞态：仅采纳最新关键词的结果
      options.value = (r.data.items || []).map(i => ({
        label: `${i.code}  ${i.name}`, value: i.code,
      }))
    } catch (e) { options.value = [] }
    finally { fetching.value = false }
  }, 200)
}

function onSelect(v, option) {
  emit('update:value', v)
  emit('select', { code: v, name: option?.name || (option?.label || '').slice(7) })
}
</script>
