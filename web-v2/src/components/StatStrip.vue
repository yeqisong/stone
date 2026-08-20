<template>
  <div class="stat-strip">
    <div v-for="it in items" :key="it.label" class="stat-item">
      <div class="stat-label">{{ it.label }}</div>
      <div class="stat-value" :style="{ color: it.color || 'var(--c-text)' }">{{ it.value }}</div>
    </div>
  </div>
</template>

<script setup>
defineProps({
  items: { type: Array, required: true, default: () => [] }, // [{label, value, color?}]
})
</script>

<style scoped>
.stat-strip { display: flex; gap: 18px; justify-content: center; padding: 8px 0 12px; flex-wrap: wrap; }
.stat-item { text-align: center; min-width: 62px; }
.stat-label { font-size: 11px; color: var(--c-text-dim); white-space: nowrap; }
.stat-value { font-size: 20px; font-weight: 700; line-height: 1.25; }
@media (max-width: 768px) {
  .stat-strip { gap: 10px; padding: 6px 0 10px; }
  .stat-item { min-width: 56px; }
  .stat-label { font-size: 10px; }
  .stat-value { font-size: 18px; }
}
</style>
