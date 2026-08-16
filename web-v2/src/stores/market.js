import { defineStore } from 'pinia'
import { ref } from 'vue'

export const useMarketStore = defineStore('market', () => {
  const selDate = ref(new Date().toISOString().slice(0, 10))
  const drillStack = ref([])  // [{name, id}]

  function setDate(dateStr) {
    selDate.value = dateStr
  }

  function resetDrill() {
    drillStack.value = []
  }

  return { selDate, drillStack, setDate, resetDrill }
})
