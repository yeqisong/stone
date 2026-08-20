<template>
  <n-config-provider :theme="theme.naiveTheme" :theme-overrides="themeOverrides">
    <n-dialog-provider>
    <n-message-provider>
      <LoginView v-if="showLogin" />
      <div v-else class="app-root" :style="{
        '--c-bg':theme.colors.bg, '--c-bg-header':theme.colors.bgHeader,
        '--c-text':theme.colors.text, '--c-text-dim':theme.colors.textDim,
        '--c-text-dimmer':theme.colors.textDimmer, '--c-text-faint':theme.colors.textFaint,
        '--c-border':theme.colors.border, '--c-border-light':theme.colors.borderLight,
        '--c-card-bg':theme.colors.cardBg, '--c-card-bg-hover':theme.colors.cardBgHover,
        '--c-input-bg':theme.colors.inputBg,
        background:'var(--c-bg)'}">
        <!-- 顶栏 -->
        <div class="app-header">
          <div class="app-header-left">
            <span class="brand">K道</span>
            <span class="header-date" id="header-date"></span>
          </div>
          <div class="app-header-right">
            <n-dropdown trigger="click" :options="userOptions" @select="handleUserMenu">
              <span class="user-chip">👤 {{ auth.username || 'admin' }}</span>
            </n-dropdown>
            <span class="ws-info" :class="wsConnected?'ok':'bad'">
              <span class="ws-dot"></span>{{ wsConnected?'已连接':'连接失败' }}
            </span>
            <n-button v-if="!wsConnected" size="tiny" text class="ws-reconnect" @click="connectWebSocket">重连</n-button>
            <n-button size="tiny" text class="theme-toggle" @click="theme.toggle" :title="theme.modeHint">{{ theme.modeLabel }}</n-button>
          </div>
        </div>

        <!-- 宽屏顶部导航：核心 | 管理 分组 -->
        <div v-if="!isNarrow" class="app-nav">
          <template v-for="t in primaryTabs" :key="t.key">
            <n-button :type="nav.tab===t.key?'primary':'default'" size="small" @click="nav.switchTab(t.key)">{{ t.icon }} {{ t.label }}</n-button>
          </template>
          <span class="nav-divider"></span>
          <template v-for="t in adminTabs" :key="t.key">
            <n-button :type="nav.tab===t.key?'primary':'default'" size="small" @click="nav.switchTab(t.key)">{{ t.icon }} {{ t.label }}</n-button>
          </template>
        </div>

        <!-- 主内容 -->
        <div class="main-content" :class="{ narrow: isNarrow }">
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

        <!-- H5 底部 TabBar：5 核心 + 管理 入口 -->
        <div v-if="isNarrow" class="h5-tabbar">
          <button v-for="t in primaryTabs" :key="t.key" class="h5-tab" :class="{ active: nav.tab===t.key }" @click="nav.switchTab(t.key)">
            <span class="h5-tab-icon">{{ t.icon }}</span>
            <span class="h5-tab-label">{{ t.label }}</span>
          </button>
          <button class="h5-tab" :class="{ active: isAdminTab }" @click="showAdmin = !showAdmin">
            <span class="h5-tab-icon">⋮</span>
            <span class="h5-tab-label">管理</span>
          </button>
        </div>

        <!-- H5 管理抽屉 -->
        <div v-if="isNarrow && showAdmin" class="admin-sheet-mask" @click="showAdmin = false">
          <div class="admin-sheet" @click.stop>
            <div class="admin-sheet-title">管理</div>
            <div class="admin-sheet-grid">
              <button v-for="t in adminTabs" :key="t.key" class="admin-sheet-item" :class="{ active: nav.tab===t.key }" @click="nav.switchTab(t.key); showAdmin = false">
                <span class="admin-sheet-icon">{{ t.icon }}</span>
                <span>{{ t.label }}</span>
              </button>
            </div>
            <button class="admin-sheet-close" @click="showAdmin = false">关闭</button>
          </div>
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
import { useViewport } from './utils/viewport'

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

// ── 响应式视口 + 导航定义 ──
const { isNarrow } = useViewport()
const showAdmin = ref(false)

const primaryTabs = [
  { key: 'p', icon: '💼', label: '持仓' },
  { key: 'm', icon: '📊', label: '选股' },
  { key: 's', icon: '🔴', label: '信号' },
  { key: 'l', icon: '📋', label: '个股' },
  { key: 'x', icon: '🛡', label: '状态' },
]
const adminTabs = [
  { key: 'a', icon: '🧠', label: '模型' },
  { key: 'f', icon: '🔧', label: '函数' },
  { key: 'e', icon: '🔬', label: '特征' },
  { key: 'g', icon: '🔀', label: 'DAG' },
]
const isAdminTab = computed(() => adminTabs.some(t => t.key === nav.tab))

// H5 下提升全局控件尺寸（触控目标 + 字号基线）
const themeOverrides = computed(() => isNarrow.value ? {
  Button: { heightTiny: '32px', heightSmall: '36px', heightMedium: '40px', fontSizeTiny: '12px', fontSizeSmall: '13px' },
  Input: { heightTiny: '32px', heightSmall: '36px', fontSizeTiny: '12px', fontSizeSmall: '13px' },
  Select: { heightTiny: '32px', heightSmall: '36px', fontSizeTiny: '12px', fontSizeSmall: '13px' },
  DatePicker: { heightTiny: '32px', heightSmall: '36px', fontSizeTiny: '12px', fontSizeSmall: '13px' },
} : {})

// 关闭管理抽屉：切到非管理 tab 时收起
watch(() => nav.tab, (t) => { if (!adminTabs.some(x => x.key === t)) showAdmin.value = false })

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

<style>
.app-root { height: 100vh; display: flex; flex-direction: column; overflow: hidden; }
.app-header { display: flex; align-items: center; justify-content: space-between; background: var(--c-bg-header); border-bottom: 1px solid var(--c-border); padding: 8px 20px; }
.app-header-left { display: flex; align-items: center; gap: 12px; min-width: 0; }
.brand { font-size: 18px; font-weight: 700; color: var(--c-text); }
.header-date { font-size: 11px; color: var(--c-text-dimmer); white-space: nowrap; }
.app-header-right { display: flex; align-items: center; gap: 12px; }
.user-chip { font-size: 12px; color: var(--c-text-dim); cursor: pointer; white-space: nowrap; }
.ws-info { font-size: 10px; display: flex; align-items: center; gap: 3px; white-space: nowrap; color: var(--c-text-dimmer); }
.ws-info .ws-dot { display: inline-block; width: 6px; height: 6px; border-radius: 50%; background: #ef4444; }
.ws-info.ok { color: #10b981; }
.ws-info.ok .ws-dot { background: #10b981; }

.app-nav { display: flex; align-items: center; gap: 4px; padding: 6px 16px; border-bottom: 1px solid var(--c-border); overflow-x: auto; flex-wrap: nowrap; -webkit-overflow-scrolling: touch; scrollbar-width: none; }
.app-nav::-webkit-scrollbar { display: none; }
.app-nav .n-button { flex-shrink: 0; }
.nav-divider { width: 1px; height: 18px; background: var(--c-border); margin: 0 10px; flex-shrink: 0; }

.main-content { flex: 1; overflow-y: auto; padding: 6px 16px; display: flex; flex-direction: column; -webkit-overflow-scrolling: touch; overscroll-behavior: contain; }
.main-content.narrow { padding: 6px 10px calc(64px + env(safe-area-inset-bottom)); }

/* H5 底部 TabBar */
.h5-tabbar { position: fixed; left: 0; right: 0; bottom: 0; z-index: 100; display: flex; height: 52px; padding-bottom: env(safe-area-inset-bottom); background: var(--c-bg-header); border-top: 1px solid var(--c-border); }
.h5-tab { flex: 1; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 1px; background: none; border: none; padding: 0; margin: 0; cursor: pointer; color: var(--c-text-dimmer); min-width: 0; }
.h5-tab-icon { font-size: 18px; line-height: 1; }
.h5-tab-label { font-size: 10px; line-height: 1.2; }
.h5-tab.active { color: #2080f0; }
.h5-tab.active .h5-tab-label { font-weight: 600; }

/* H5 管理抽屉 */
.admin-sheet-mask { position: fixed; inset: 0; z-index: 120; background: rgba(0, 0, 0, .4); display: flex; align-items: flex-end; }
.admin-sheet { width: 100%; background: var(--c-bg-header); border-radius: 16px 16px 0 0; padding: 16px 16px calc(16px + env(safe-area-inset-bottom)); box-shadow: 0 -4px 20px rgba(0,0,0,.15); }
.admin-sheet-title { font-size: 13px; font-weight: 600; color: var(--c-text); text-align: center; margin-bottom: 12px; }
.admin-sheet-grid { display: grid; grid-template-columns: repeat(2, 1fr); gap: 10px; margin-bottom: 12px; }
.admin-sheet-item { display: flex; align-items: center; justify-content: center; gap: 6px; padding: 14px 0; border-radius: 10px; background: var(--c-card-bg); border: 1px solid var(--c-border); color: var(--c-text); font-size: 13px; cursor: pointer; }
.admin-sheet-item.active { color: #2080f0; border-color: rgba(32,128,240,.4); background: rgba(32,128,240,.08); }
.admin-sheet-icon { font-size: 18px; }
.admin-sheet-close { width: 100%; padding: 12px 0; border-radius: 10px; background: var(--c-card-bg); border: 1px solid var(--c-border); color: var(--c-text-dim); font-size: 13px; cursor: pointer; }

@media (max-width: 768px) {
  .app-header { padding: 8px 12px; }
  .app-header-left { gap: 8px; }
  .header-date { font-size: 10px; }
}
</style>
