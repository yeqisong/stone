<template>
  <div ref="container" style="width:100%;height:200px;border:1px solid var(--c-border);border-radius:4px"></div>
</template>

<script setup>
import { ref, onMounted, onUnmounted, watch } from 'vue'
import * as monaco from 'monaco-editor'

const props = defineProps({ modelValue: String })
const emit = defineEmits(['update:modelValue'])
const container = ref(null)
let editor = null

onMounted(() => {
  if (!container.value) return
  editor = monaco.editor.create(container.value, {
    value: props.modelValue || '',
    language: 'python',
    theme: 'vs-dark',
    minimap: { enabled: false },
    lineNumbers: 'on',
    scrollBeyondLastLine: false,
    fontSize: 12,
    tabSize: 4,
    automaticLayout: true,
  })
  editor.onDidChangeModelContent(() => {
    emit('update:modelValue', editor.getValue())
  })
})

watch(() => props.modelValue, (v) => {
  if (editor && editor.getValue() !== v) {
    editor.setValue(v || '')
  }
})

onUnmounted(() => {
  editor?.dispose()
})
</script>