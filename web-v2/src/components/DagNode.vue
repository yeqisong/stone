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
  border-radius: 8px;
  padding: 10px 16px;
  text-align: center;
  min-width: 100px;
  font-size: 11px;
  font-weight: 600;
  color: var(--c-text);
  position: relative;
}
.dag-node.selected {
  border-color: var(--c-info);
  box-shadow: 0 0 0 2px rgba(32,128,240,0.2);
}
.dag-node-label {
  display: block;
}
.dag-node.status-pending { border-color: #94a3b8; }
.dag-node.status-running { border-color: var(--c-info); box-shadow: 0 0 0 2px color-mix(in srgb, var(--c-info) 30%, transparent); }
.dag-node.status-success { border-color: #10b981; }
.dag-node.status-failed { border-color: #ef4444; }
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