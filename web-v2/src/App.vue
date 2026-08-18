<template>
  <n-config-provider :theme="theme.naiveTheme">
    <n-dialog-provider>
    <n-message-provider>
      <LoginView v-if="showLogin" />
      <div v-else :style="{height:'100vh',display:'flex',flexDirection:'column',overflow:'hidden',
        '--c-bg':theme.colors.bg, '--c-bg-header':theme.colors.bgHeader,
        '--c-text':theme.colors.text, '--c-text-dim':theme.colors.textDim,
        '--c-text-dimmer':theme.colors.textDimmer, '--c-text-faint':theme.colors.textFaint,
        '--c-border':theme.colors.border, '--c-border-light':theme.colors.borderLight,
        '--c-card-bg':theme.colors.cardBg, '--c-card-bg-hover':theme.colors.cardBgHover,
        '--c-input-bg':theme.colors.inputBg,
        background:'var(--c-bg)'}">
        <div :style="{padding:'8px 24px',borderBottom:'1px solid '+theme.colors.border,display:'flex',alignItems:'center',justifyContent:'space-between',background:theme.colors.bgHeader}">
          <div>
            <span :style="{fontSize:'18px',fontWeight:700,color:theme.colors.text}">K道</span>

            <span :style="{fontSize:'11px',color:theme.colors.textDimmer,marginLeft:'12px'}" id="header-date"></span>
          </div>
          <div style="display:flex;align-items:center;gap:12px">
            <n-dropdown trigger="click" :options="userOptions" @select="handleUserMenu">
              <span :style="{fontSize:'12px',color:theme.colors.textDim,cursor:'pointer'}">👤 {{ auth.username || 'admin' }}</span>
            </n-dropdown>
            <span :style="{fontSize:'9px',color:wsConnected?'#10b981':'#ef4444',display:'flex',alignItems:'center',gap:'2px',lineHeight:'1'}">
              <span :style="{display:'inline-block',width:'6px',height:'6px',borderRadius:'50%',background:wsConnected?'#10b981':'#ef4444'}"></span>
              {{wsConnected?'已连接':'连接失败'}}
            </span>
            <n-button v-if="!wsConnected" size="tiny" text :style="{fontSize:'10px',color:theme.colors.textDim}" @click="connectWebSocket">重连</n-button>
            <n-button size="tiny" text @click="theme.toggle" :style="{fontSize:'16px'}" :title="theme.modeHint">{{ theme.modeLabel }}</n-button>
          </div>
        </div>
        <div :style="{padding:'6px 20px',borderBottom:'1px solid '+theme.colors.border,display:'flex',gap:'4px',overflowX:'auto',flexWrap:'nowrap',WebkitOverflowScrolling:'touch',scrollbarWidth:'none'}">
          <n-button :type="nav.tab==='p'?'primary':'default'" size="small" @click="nav.switchTab('p')" :style="{flexShrink:0}">💼 持仓</n-button>
          <n-button :type="nav.tab==='m'?'primary':'default'" size="small" @click="nav.switchTab('m')" :style="{flexShrink:0}">📊 选股</n-button>
          <n-button :type="nav.tab==='s'?'primary':'default'" size="small" @click="nav.switchTab('s')" :style="{flexShrink:0}">🔴 信号</n-button>
          <n-button :type="nav.tab==='l'?'primary':'default'" size="small" @click="nav.switchTab('l')" :style="{flexShrink:0}">📋 个股</n-button>
          <n-button :type="nav.tab==='a'?'primary':'default'" size="small" @click="nav.switchTab('a')" :style="{flexShrink:0}">🧠 模型</n-button>
          <n-button :type="nav.tab==='f'?'primary':'default'" size="small" @click="nav.switchTab('f')" :style="{flexShrink:0}">🔧 函数</n-button>
          <n-button :type="nav.tab==='e'?'primary':'default'" size="small" @click="nav.switchTab('e')" :style="{flexShrink:0}">🔬 特征</n-button>
          <n-button :type="nav.tab==='g'?'primary':'default'" size="small" @click="nav.switchTab('g')" :style="{flexShrink:0}">🔀 DAG</n-button>
          <n-button :type="nav.tab==='x'?'primary':'default'" size="small" @click="nav.switchTab('x')" :style="{flexShrink:0}">📊 状态</n-button>

        </div>
        <div style="flex:1;overflow-y:auto;padding:6px 20px;display:flex;flex-direction:column" class="main-content">
          <div v-if="nav.tab==='p'"><PortfolioView @show-detail="nav.showDetail" /></div>
          <div v-if="nav.tab==='m'"><TreemapView @show-detail="nav.showDetail" /></div>
          <div v-if="nav.tab==='s'"><SignalsView @show-detail="nav.showDetail" /></div>
          <div v-if="nav.tab==='l'" style="flex:1;min-height:0;display:flex;flex-direction:column"><StocksView @show-detail="nav.showDetail" /></div>
          <div v-if="nav.tab==='u'" style="flex:1;min-height:0;display:flex;flex-direction:column"><StockFundView @back="nav.tab = 'x'" @show-detail="nav.showDetail" /></div>
          <div v-if="nav.tab==='d'"><DetailView :code="nav.dcode" @back="nav.backFromDetail" /></div>
          <div v-if="nav.tab==='x'"><StatusView /></div>
          <div v-if="nav.tab==='a'"><ModelView /></div>
          <div v-if="nav.tab==='f'"><FunctionView /></div>
          <div v-if="nav.tab==='e'"><FeatureView /></div>
          <div v-if="nav.tab==='v'"><FeatureDetail :featureId="nav.fid" @back="nav.backFromFeatureDetail()" @edit="(id) => { nav.backFromFeatureDetail(); nav.pendingEditFeatureId = id }" /></div>
          <div v-if="nav.tab==='g'"><DagFlowEdit :flowId="nav.flowId" @back="nav.backFromFlowEditor()" @show-log="(id) => { nav.flowId = parseInt(id); nav.tab = 'q' }" /></div>
          <div v-if="nav.tab==='q'"><FlowLogView :flowId="nav.flowId" @back="nav.flowId = null; nav.tab = 'g'" /></div>
          <div v-if="nav.tab==='r'"><FlowRunView :flowId="nav.flowId" @back="nav.flowId = null; nav.tab = 'g'" /></div>

        </div>
      </div>
    </n-message-provider>
    </n-dialog-provider>
  </n-config-provider>
</template>

<script setup>
import { computed, ref, onMounted, watch } from 'vue'
import { NConfigProvider, NMessageProvider, NDialogProvider, NButton, NDropdown } from 'naive-ui'
import axios from 'axios'
import { useAuthStore } from './stores/auth'
import { useNavStore } from './stores/nav'
import { useThemeStore } from './stores/theme'

import { connectWebSocket, wsState } from './utils/ws'

const theme = useThemeStore()
const wsConnected = ref(false)
onMounted(() => {
  connectWebSocket()
  theme.startAutoTimer()
  setInterval(() => { wsConnected.value = wsState.connected }, 1000)
})
import LoginView from './components/LoginView.vue'
import PortfolioView from './components/PortfolioView.vue'
import SignalsView from './components/SignalsView.vue'
import TreemapView from './components/TreemapView.vue'
import StocksView from './components/StocksView.vue'
import StockFundView from './components/StockFundView.vue'
import DetailView from './components/DetailView.vue'
import StatusView from './components/StatusView.vue'

import ModelView from './components/ModelView.vue'
import FunctionView from './components/FunctionView.vue'
import FeatureView from './components/FeatureView.vue'
import FeatureDetail from './components/FeatureDetail.vue'
import DagFlowEdit from './components/DagFlowEdit.vue'
import FlowLogView from './components/FlowLogView.vue'
import FlowRunView from './components/FlowRunView.vue'

const auth = useAuthStore()
const nav = useNavStore()

const userOptions = [
  { label: '退出登录', key: 'logout' }
]
function handleUserMenu(key) {
  if (key === 'logout') auth.logout()
}
const showLogin = computed(() => !auth.token)

watch(showLogin, (v) => {
  if (v) document.title = '登录 - K道'
})

const titleMap = {
  p: '持仓 - K道',
  m: '选股 - 市值 - K道',
  s: '买点信号 - K道',
  l: '个股列表 - K道',
  d: '个股详情 - K道',
  x: '数据状态 - K道',
  a: '模型管理 - K道',
  f: '函数管理 - K道',
  e: '特征管理 - K道',
  g: 'DAG 流程 - K道',
  o: '系统设置 - K道',
}

// tab 变化时更新页面标题
watch(() => nav.tab, (t) => {
  if (t === 'd') {
    document.title = (nav.dcode ? nav.dcode + ' - ' : '') + '个股详情 - K道'
  } else {
    document.title = titleMap[t] || 'K道'
  }
}, { immediate: true })

if (!showLogin.value) {
  setTimeout(async () => {
    try {
      const r = await axios.get(window.location.origin + '/api/data_status')
      if (r.data.overview && r.data.overview.latest_date) {
        const el = document.getElementById('header-date')
        if (el) el.textContent = r.data.overview.latest_date
      }
    } catch(e) {}
  }, 200)
}
</script>
