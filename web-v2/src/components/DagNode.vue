<template>
  <div class="dag-node" :class="[selected ? 'selected' : '', data?.status ? 'status-'+data.status : '']">
    <Handle type="target" :position="Position.Top" id="t-top" />
    <span class="dag-node-label">{{ data.label }}</span>
    <Handle type="source" :position="Position.Bottom" id="s-bottom" />
  </div>
</template>

<script setup>
import { Handle, Position } from '@vue-flow/core'
defineProps(['id', 'data', 'selected'])
</script>

<style scoped>
.dag-node {
  background: var(--c-card-bg);
  border: 1px solid var(--c-border);
  border-radius: var(--radius);
  padding: 10px 16px;
  text-align: center;
  min-width: 100px;
  font-size: var(--text-meta);
  font-weight: 600;
  color: var(--c-text);
  position: relative;
}
.dag-node.selected {
  border-color: var(--c-warning);
  box-shadow: 0 0 0 2px color-mix(in srgb, var(--c-up) 20%, transparent);
}
.dag-node-label {
  display: block;
}
.dag-node.status-pending { border-color: var(--c-text-faint); }
.dag-node.status-running { border-color: var(--c-warning); box-shadow: 0 0 0 2px color-mix(in srgb, var(--c-warning) 30%, transparent); }
.dag-node.status-success { border-color: var(--c-down); }
.dag-node.status-failed { border-color: var(--c-up); }
.dag-node :deep(.vue-flow__handle) {
  width: 6px !important;
  height: 6px !important;
  background: var(--c-text-dim) !important;
  border: 1px solid var(--c-card-bg) !important;
  opacity: 0;
  transition: opacity 0.15s;
}
.dag-node:hover :deep(.vue-flow__handle) {
  opacity: 0.4;
}
.dag-node :deep(.vue-flow__handle:hover) {
  opacity: 0.9;
}
</style>