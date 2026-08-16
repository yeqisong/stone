# Pinia 状态管理（未使用，备查）

## 何时需要 Pinia
当前项目**未使用** Pinia，因为：
- 各页面组件各自管理自己的数据（`ref`/`reactive`）
- 跨组件通信通过 `defineEmits`/`defineProps` 父子传递
- 全局状态只有 tab 切换和 token，放在 App.vue 够用

如果未来需要**跨页面共享数据**（如用户设置、缓存搜索结果等），可以引入 Pinia。

## 快速安装
```bash
npm install pinia
```

## 定义 Store
```javascript
// stores/counter.js
import { defineStore } from 'pinia'

export const useCounterStore = defineStore('counter', () => {
  const count = ref(0)
  function increment() { count.value++ }
  return { count, increment }
})
```

## 在组件中使用
```javascript
import { useCounterStore } from '@/stores/counter'
const store = useCounterStore()
// 模板中直接使用 {{ store.count }}
```

## 核心概念
- **State**: `ref()` / `reactive()` 定义的状态
- **Getter**: `computed()` 定义的派生状态
- **Action**: 任意函数，可同步可异步

## 与 Vuex 区别
- 无 `mutation`，直接修改 state
- 完整的 TypeScript 支持
- 支持多个 store（非单一根 store）
- 支持 devtools
