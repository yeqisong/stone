# Vue 3 + Vite 开发模式速查

## 项目结构
```
web-v2/
├── index.html              # 入口 HTML
├── vite.config.js          # Vite 配置（含 API 代理）
├── package.json
├── public/                 # 静态资源（自动复制到 dist）
│   └── login.html          # 登录页（独立，不经过 Vue）
├── src/
│   ├── main.js             # Vue 入口
│   ├── App.vue             # 主应用（布局 + tab 切换）
│   └── components/         # 页面组件
```

## v-if vs v-show

- `v-if` / `v-else` 必须在相邻兄弟元素上
- 如果中间有其他元素（如 `<n-button>`），`v-else` 会找不到匹配的 `v-if`
- 替代方案：用 `v-show="!condition"` 显式控制

## v-if 和 v-for 优先级

Vue 3 中 `v-if` 优先级高于 `v-for`。以下代码会报错：
```html
<div v-for="s in list" v-if="s.active">   <!-- ❌ 先执行 v-if，s 未定义 -->
```
正确写法：
```html
<template v-for="s in list">
  <div v-if="s.active">...</div>
</template>
```

## `<script setup>` 规则

- 顶层 `const` / `ref` / `reactive` 自动暴露给模板，不需要 `return`
- `defineEmits` 声明的 emit 名必须与 `@event` 完全匹配（kebab-case）
- `defineProps` 接收父组件传入的 props

## 组件通信

```javascript
// 子组件
const emit = defineEmits(['show-detail'])
emit('show-detail', stock_code)

// 父组件
<ChildComponent @show-detail="handler" />
```

## watch

```javascript
import { ref, watch } from 'vue'
watch(sourceRef, (newVal, oldVal) => { ... })
```

## nextTick

```javascript
import { nextTick } from 'vue'
await nextTick()
```

## API 代理（开发环境）

`vite.config.js`:
```javascript
server: {
  port: 3000,
  proxy: { '/api': { target: 'http://localhost:8000', changeOrigin: true } }
}
```
