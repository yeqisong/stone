# Naive UI + Vue 3 正确用法速查

## 弹窗 Modal (已踩坑多次)

### ❌ 错误用法
```html
<n-modal v-model:show="showEdit" preset="card" title="编辑">
  <n-form>...</n-form>
</n-modal>
```
`preset="card"` 在 `<script setup>` 模式下可能导致 `exposed` 空指针。

### ✅ 正确用法
```html
<n-modal v-model:show="showEdit">
  <n-card style="width:450px" title="编辑" role="dialog" aria-modal="true">
    <n-space vertical> ... </n-space>
    <template #footer>
      <n-button @click="showEdit=false">取消</n-button>
      <n-button type="primary">保存</n-button>
    </template>
  </n-card>
</n-modal>
```
`n-modal` 包裹 `n-card`（不使用 preset），`n-card` 加 `role="dialog"` 和 `aria-modal="true"`。

## 数据表格 n-data-table

### 行点击
```javascript
function rowProps(row) {
  return {
    style: 'cursor:pointer',
    onClick: (e) => {
      // 必须检查点击目标不是按钮
      if (!e.target.closest('button')) emit('show-detail', row.stock_code)
    }
  }
}
```

### 排序
```javascript
// columns 定义
{ title:'最新价', key:'price', sorter:true, sortOrder: sortField==='price'?sortDir:false }
// @update:sorter 事件
<n-data-table @update:sorter="handleSorter" />
// 排序方向: 'ascend' | 'descend' | false
```

## 暗色主题文本颜色

```html
<!-- 不在 n-card 内时，标题和文本需要显式指定颜色 -->
<h4 style="margin-bottom:8px;color:#fff">标题</h4>
<div style="font-size:11px;color:rgba(255,255,255,.45)">描述文本</div>
```

## 首次加载报错处理

Naive UI 的 `n-button` 使用 `FadeInExpandTransition`，在首次 mount 后的异步响应式更新中可能报：
```
Slot "default" invoked outside of the render function
```

### 解决方案
- 非关键数据（如头部日期）使用原生 DOM 操作更新，绕过 Vue 响应式
- 或使用 `setTimeout(() => {...}, 200)` 延迟更新

## Naive UI Provider 嵌套顺序

```html
<n-config-provider :theme="darkTheme">
  <n-dialog-provider>
    <n-message-provider>
      <!-- 应用内容 -->
    </n-message-provider>
  </n-dialog-provider>
</n-config-provider>
```

## import 规则

- 每次使用 `<n-button>` 等组件，必须在 `import` 中显式导入
- 不能只导入父组件而不导入子组件
