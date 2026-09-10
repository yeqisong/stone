<template>
  <div v-if="total != null" class="lp-bar">
    <span class="lp-total">共 {{ total }} 条</span>
    <n-pagination :page="page" :item-count="total" :page-size="pageSize" size="small"
      @update:page="p => emit('change', p)" />
  </div>
</template>
<script setup>
import { NPagination } from 'naive-ui'
// 全站统一分页器：居中 + 「共 M 条」，置于表格下方（flex 布局中加 no-shrink 防压缩）
// 用法：<ListPagination :total="total" :page="page" :page-size="50" @change="p => { page = p; load() }" />
const props = defineProps({
  total: { type: Number, default: null },
  page: { type: Number, required: true },
  pageSize: { type: Number, required: true },
})
const emit = defineEmits(['change'])
</script>
<style scoped>
.lp-bar { display: flex; justify-content: center; align-items: center; gap: 10px; margin-top: 8px; font-size: 12px; color: var(--c-text-dim); flex-shrink: 0; }
</style>
