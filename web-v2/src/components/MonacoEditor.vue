<template>
  <div ref="container" style="width:100%;height:200px;border:1px solid var(--c-border);border-radius:4px"></div>
</template>

<script setup>
import { ref, onMounted, onUnmounted, watch } from 'vue'
import * as monaco from 'monaco-editor'

// completions: [{label, insert, detail}] — 提供时启用该算子集的自动补全（KEPL 公式编辑）
const props = defineProps({ modelValue: String, completions: Array })
const emit = defineEmits(['update:modelValue'])
const container = ref(null)
let editor = null
let provider = null

onMounted(() => {
  if (!container.value) return
  // 有补全集时用独立语言 id 注册（避免污染全局 python 语言的其他编辑器）
  const lang = props.completions?.length ? `kepl-${Math.random().toString(36).slice(2, 8)}` : 'python'
  editor = monaco.editor.create(container.value, {
    value: props.modelValue || '',
    language: lang,
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
  if (props.completions?.length) {
    provider = monaco.languages.registerCompletionItemProvider(lang, {
      triggerCharacters: ['(', ' ', ','],
      provideCompletionItems(model, position) {
        const word = model.getWordUntilPosition(position)
        const range = { startLineNumber: position.lineNumber, endLineNumber: position.lineNumber,
                        startColumn: word.startColumn, endColumn: word.endColumn }
        return {
          suggestions: props.completions.map(c => ({
            label: c.label, kind: monaco.languages.CompletionItemKind.Function,
            insertText: c.insert,
            insertTextRules: monaco.languages.CompletionItemInsertTextRule.InsertAsSnippet,
            detail: c.detail, range,
          })),
        }
      },
    })
  }
})

// 在光标处插入文本（算子速查面板点击调用）
function insertSnippet(text) {
  if (!editor) return
  const sel = editor.getSelection()
  editor.executeEdits('snippet', [{ range: sel, text: text, forceMoveMarkers: true }])
  editor.focus()
}
defineExpose({ insertSnippet })

watch(() => props.modelValue, (v) => {
  if (editor && editor.getValue() !== v) {
    editor.setValue(v || '')
  }
})

onUnmounted(() => {
  provider?.dispose()
  editor?.dispose()
})
</script>
