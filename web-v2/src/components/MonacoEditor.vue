<template>
  <div ref="container" :style="`width:100%;height:${height}px;border:1px solid var(--c-border);border-radius:4px`"></div>
</template>

<script setup>
import { ref, onMounted, onUnmounted, watch } from 'vue'
import * as monaco from 'monaco-editor'
import { useThemeStore } from '../stores/theme'

// completions: [{label, insert, detail}] — 提供时启用该算子集的自动补全（KEPL 公式编辑）
// height: 编辑区高度（默认 200）
const props = defineProps({ modelValue: String, completions: Array, readonly: Boolean, height: { type: Number, default: 200 } })
const emit = defineEmits(['update:modelValue'])
const container = ref(null)
const theme = useThemeStore()
let editor = null
let provider = null

// 深浅主题各定义一套，背景对齐 theme store 卡片色，避免编辑器成为深色孤岛
monaco.editor.defineTheme('kdao-dark', {
  base: 'vs-dark',
  inherit: true,
  rules: [],
  colors: { 'editor.background': '#141419' },
})
monaco.editor.defineTheme('kdao-light', {
  base: 'vs',
  inherit: true,
  rules: [],
  colors: { 'editor.background': '#f7f8fa' },
})

onMounted(() => {
  if (!container.value) return
  // 有补全集时用独立语言 id 注册（避免污染全局 python 语言的其他编辑器）
  const lang = props.completions?.length ? `kepl-${Math.random().toString(36).slice(2, 8)}` : 'python'
  editor = monaco.editor.create(container.value, {
    value: props.modelValue || '',
    language: lang,
    theme: theme.isDark ? 'kdao-dark' : 'kdao-light',
    readOnly: !!props.readonly,
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

// 主题切换即时跟随
watch(() => theme.isDark, (d) => {
  monaco.editor.setTheme(d ? 'kdao-dark' : 'kdao-light')
})
watch(() => props.readonly, (ro) => {
  editor?.updateOptions({ readOnly: !!ro })
})

onUnmounted(() => {
  provider?.dispose()
  editor?.dispose()
})
</script>
