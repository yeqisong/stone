<template>
  <svg :viewBox="'0 0 24 24'" :width="size" :height="size" fill="none" stroke="currentColor"
    stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"
    :style="{ verticalAlign: '-2px', flexShrink: 0 }" v-html="path" />
</template>

<script setup>
import { computed } from 'vue'
import { ICONS } from '../utils/icons'

// 全站共享图标组件：name 取 utils/icons.js 注册表键名
const props = defineProps({ name: { type: String, required: true }, size: { type: Number, default: 13 } })
const path = computed(() => ICONS[props.name] || '')
</script>
