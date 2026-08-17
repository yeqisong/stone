import { defineStore } from 'pinia'
import { ref } from 'vue'
import { bjDateStr } from '../utils/date.js'

export const useMarketStore = defineStore('market', () => {
  const selDate = ref(bjDateStr())
  const drillStack = ref([])  // [{name, id}]

  function setDate(dateStr) {
    selDate.value = dateStr
  }

  function resetDrill() {
    drillStack.value = []
  }

  return { selDate, drillStack, setDate, resetDrill }
})
