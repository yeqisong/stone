<template>
  <div ref="el" class="rt-chart" :class="{ 'rt-chart-fill': fill }" :style="fill ? null : { height }" />
</template>

<script setup>
// ECharts 容器：实例挂在自身 ref 上（不用 document.getElementById，多个分区同屏不串）。
// 约定沿用 MarketRealtimeView：卸载前（DOM 还在）dispose；主题切换重画；ResizeObserver
// 跟随分栏/放大还原后的容器尺寸变化，不依赖 window.resize。
import { ref, watch, onMounted, onBeforeUnmount, nextTick } from 'vue'
import * as echarts from 'echarts'
import { useThemeStore } from '../../stores/theme'

const props = defineProps({
  option: { type: Object, default: null },   // null = 清空（数据源缺这条时不画空坐标系）
  height: { type: String, default: '200px' },
  fill: { type: Boolean, default: false },   // 撑满父容器剩余高度（父级需是定高的纵向 flex）
})
const theme = useThemeStore()
const el = ref(null)
let chart = null
let ro = null

function render() {
  if (!chart) return
  if (props.option) chart.setOption(props.option, { notMerge: true })
  else chart.clear()
}
onMounted(() => {
  chart = echarts.init(el.value)
  render()
  if (typeof ResizeObserver !== 'undefined') {
    ro = new ResizeObserver(() => { if (chart) chart.resize() })
    ro.observe(el.value)
  }
})
onBeforeUnmount(() => {
  if (ro) ro.disconnect()
  if (chart) { chart.dispose(); chart = null }
})
watch(() => props.option, () => nextTick(render))
watch(() => theme.isDark, () => nextTick(render))
</script>

<style scoped>
.rt-chart { width: 100%; min-width: 0; }
.rt-chart-fill { flex: 1; min-height: 0; }
</style>
