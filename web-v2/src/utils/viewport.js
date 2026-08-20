import { ref, onMounted, onUnmounted } from 'vue'

// 响应式视口判断（H5 导航 / 表格精简 / 主题覆盖共用）
export function useViewport(breakpoint = 768) {
  const isNarrow = ref(typeof window !== 'undefined' ? window.innerWidth < breakpoint : false)

  function onResize() {
    isNarrow.value = window.innerWidth < breakpoint
  }

  onMounted(() => window.addEventListener('resize', onResize))
  onUnmounted(() => window.removeEventListener('resize', onResize))

  return { isNarrow }
}
