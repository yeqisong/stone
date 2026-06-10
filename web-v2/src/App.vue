<template>
  <n-config-provider :theme="darkTheme">
    <n-dialog-provider>
    <n-message-provider>
      <div style="height:100vh;display:flex;flex-direction:column;background:#101014">
        <!-- Header -->
        <div style="padding:8px 24px;border-bottom:1px solid rgba(255,255,255,.09);display:flex;align-items:center;justify-content:space-between;background:#1a1a1e">
          <div>
            <span style="font-size:18px;font-weight:700;color:#fff">悟道</span>
            <span style="font-size:11px;color:rgba(255,255,255,.38);margin-left:12px">{{overview.latest_date||'-'}}</span>
          </div>
          <div style="display:flex;align-items:center;gap:12px">
            <span style="font-size:12px;color:rgba(255,255,255,.55)">👤 admin</span>
            <n-button size="tiny" text @click="doLogout">退出</n-button>
          </div>
        </div>
        
        <!-- Tabs -->
        <div style="padding:6px 20px;border-bottom:1px solid rgba(255,255,255,.09);display:flex;gap:4px">
          <n-button :type="tab==='p'?'primary':'default'" size="small" @click="tab='p'">💼 持仓</n-button>
          <n-button :type="tab==='s'?'primary':'default'" size="small" @click="tab='s'">🔴 信号</n-button>
          <n-button :type="tab==='l'?'primary':'default'" size="small" @click="tab='l'">📋 个股</n-button>
          <n-button :type="tab==='x'?'primary':'default'" size="small" @click="tab='x'">📊 状态</n-button>
          <n-button :type="tab==='o'?'primary':'default'" size="small" @click="tab='o'">⚙️ 设置</n-button>
        </div>

        <!-- Content -->
        <div style="flex:1;overflow-y:auto;padding:16px 24px">
          <!-- Portfolio -->
          <div v-if="tab==='p'">
            <PortfolioView @show-detail="showDetail" />
          </div>
          <!-- Signals -->
          <div v-if="tab==='s'">
            <SignalsView @show-detail="showDetail" />
          </div>
          <!-- Stocks -->
          <div v-if="tab==='l'">
            <StocksView @show-detail="showDetail" />
          </div>
          <!-- Detail -->
          <div v-if="tab==='d'">
            <DetailView :code="dcode" @back="tab='l'" />
          </div>
          <!-- Data Status -->
          <div v-if="tab==='x'">
            <StatusView />
          </div>
          <!-- Settings -->
          <div v-if="tab==='o'">
            <SettingsView />
          </div>
        </div>
      </div>
    </n-message-provider>
    </n-dialog-provider>
  </n-config-provider>
</template>

<script setup>
import { ref, reactive, onMounted } from 'vue'
import { darkTheme } from 'naive-ui'
import { NConfigProvider, NMessageProvider, NDialogProvider, NButton } from 'naive-ui'
import axios from 'axios'
import PortfolioView from './components/PortfolioView.vue'
import SignalsView from './components/SignalsView.vue'
import StocksView from './components/StocksView.vue'
import DetailView from './components/DetailView.vue'
import StatusView from './components/StatusView.vue'
import SettingsView from './components/SettingsView.vue'

const API = window.location.origin
const tab = ref('p')
const dcode = ref('')
const overview = reactive({latest_date:'',total_rows:0,total_stocks:0})

function showDetail(code) { dcode.value = code; tab.value = 'd' }
function doLogout() { localStorage.clear(); window.location.href='/login.html' }

onMounted(async () => {
  // 设置 auth header
  const token = localStorage.getItem('token')
  if (token) {
    axios.defaults.headers.common['Authorization'] = 'Bearer ' + token
  } else if (window.location.pathname !== '/login.html') {
    window.location.href = '/login.html'
    return
  }
  // 加载概览数据
  try {
    const r = await axios.get(API + '/api/data_status')
    if (r.data.overview) Object.assign(overview, r.data.overview)
  } catch(e) {}
})
</script>
