# ECharts Treemap 配置速查

## 数据层级与 levels

```
levels[0] → 根节点（隐式 root，ECharts 自动创建的包裹层）
levels[1] → 第一层数据（我们的 L1 行业）
levels[2] → 第二层数据（个股）
```

## 关键配置项

| 配置 | 作用 | 默认值 |
|------|------|--------|
| `label.show` | 是否显示标签 | `true` |
| `label.fontSize` | 标签字号 | 12 |
| `label.formatter` | 标签格式化函数 | `(params) => string` |
| `upperLabel.show` | 父节点顶部标签（与内容标签分开） | `false` |
| `itemStyle.borderColor` | 节点边框颜色 | `'#fff'` |
| `itemStyle.borderWidth` | 边框宽度 | `0` |
| `itemStyle.gapWidth` | 节点间间距 | `0` |
| `breadcrumb.show` | 面包屑导航（下钻时显示） | `true` |
| `nodeClick` | 节点点击行为：`'zoomToNode'`/`'link'`/`false` | `'zoomToNode'` |
| `roam` | 是否可缩放/平移 | `true` |
| `visibleMin` | 最小可见面积（像素²） | `10` |
| `sort` | 是否按值排序 | `true` |

## 正确用法示例

```javascript
chart.setOption({
  series: [{
    type: 'treemap',
    data: [
      { name: '制造业', value: 100000, children: [
        { name: '贵州茅台', value: 20000 },
        { name: '五粮液', value: 15000 },
      ]},
      { name: '金融业', value: 50000, children: [
        { name: '招商银行', value: 30000 },
      ]},
    ],
    // levels 按深度索引，从 0 开始
    levels: [
      { label: { show: false }, itemStyle: { borderWidth: 4 } },           // level 0: 隐式 root
      { label: { show: true, fontSize: 14 }, itemStyle: { borderWidth: 2 } }, // level 1: L1 行业
      { label: { show: true, fontSize: 10 }, itemStyle: { borderWidth: 0.5 } }, // level 2: 个股
    ],
  }]
})
```

## 点击事件

```javascript
chart.on('click', function(params) {
  // params.data 包含自定义属性
  // params.data.name, params.data.value, params.treePathInfo
})
```

## drill-down（下钻）

```javascript
// 拿到点击节点的 name
chart.dispatchAction({
  type: 'treemapDrillDown',
  name: params.name
})
```

## tooltip

```javascript
tooltip: {
  formatter: function(params) {
    return `<b>${params.name}</b><br/>市值: ${params.value}`
  }
}
```
