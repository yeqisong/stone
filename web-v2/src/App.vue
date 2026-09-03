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
        '--c-success':theme.colors.success, '--c-error':theme.colors.error,
        '--c-warning':theme.colors.warning, '--c-info':theme.colors.info,
        background:'var(--c-bg)'}">
        <!-- 顶栏 -->
        <div class="app-header">
          <div class="app-header-left">
            <span class="brand"><LogoFull :height="19" /></span>
            <span class="header-date" id="header-date"></span>
          </div>
          <div class="app-header-right">
            <n-dropdown trigger="click" :options="userOptions" @select="handleUserMenu">
              <span class="user-chip"><AppIcon name="user" :size="13" /> {{ auth.username || 'admin' }}</span>
            </n-dropdown>
            <span class="ws-info" :class="wsConnected?'ok':'bad'">
              <span class="ws-dot"></span>{{ wsConnected?'已连接':'连接失败' }}
            </span>
            <n-button v-if="!wsConnected" size="tiny" text class="ws-reconnect" @click="connectWebSocket">重连</n-button>
            <n-button size="tiny" text class="theme-toggle" @click="toggleNotif" :title="notifHint"><AppIcon :name="notifEnabled ? 'bell' : 'bell-off'" :size="13" /> {{ notifEnabled ? '通知开' : '通知关' }}</n-button>
            <n-button size="tiny" text class="theme-toggle" @click="theme.toggle" :title="'当前' + theme.modeHint + '，点击切换'"><AppIcon :name="theme.modeLabel" :size="13" /> {{ theme.modeHint }}</n-button>
          </div>
        </div>

        <!-- 宽屏顶部导航：核心 | 管理 分组 -->
        <div v-if="!isNarrow" class="app-nav">
          <template v-for="t in primaryTabs" :key="t.key">
            <n-button :type="nav.tab===t.key?'primary':'default'" size="small" @click="nav.switchTab(t.key)"><AppIcon :name="t.icon || t.key" :size="13" /><span class="nav-label">{{ t.label }}</span></n-button>
          </template>
          <span class="nav-divider"></span>
          <template v-for="t in adminTabs" :key="t.key">
            <n-button :type="nav.tab===t.key?'primary':'default'" size="small" @click="nav.switchTab(t.key)"><AppIcon :name="t.icon || t.key" :size="13" /><span class="nav-label">{{ t.label }}</span></n-button>
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
          <div v-if="nav.tab==='w'" style="flex:1;min-height:0;display:flex;flex-direction:column"><DbExplorerView /></div>
          <div v-if="nav.tab==='v'"><FeatureDetail :featureId="nav.fid" @back="nav.backFromFeatureDetail()" @edit="(id) => { nav.backFromFeatureDetail(); nav.pendingEditFeatureId = id }" /></div>
          <div v-if="nav.tab==='g'" style="flex:1;min-height:0;display:flex;flex-direction:column"><DagFlowEdit :flowId="nav.flowId" @back="nav.backFromFlowEditor()" @show-log="(id) => { nav.flowId = parseInt(id); nav.tab = 'q' }" /></div>
          <div v-if="nav.tab==='q'" style="flex:1;min-height:0;display:flex;flex-direction:column"><FlowLogView :flowId="nav.flowId" @back="nav.flowId = null; nav.tab = 'g'" /></div>
          <div v-if="nav.tab==='r'" style="flex:1;min-height:0;display:flex;flex-direction:column"><FlowRunView :flowId="nav.flowId" @back="nav.flowId = null; nav.tab = 'g'" /></div>
        </div>

        <!-- H5 底部 TabBar：5 核心 + 管理 入口 -->
        <div v-if="isNarrow" class="h5-tabbar">
          <button v-for="t in primaryTabs" :key="t.key" class="h5-tab" :class="{ active: nav.tab===t.key }" @click="nav.switchTab(t.key)">
            <span class="h5-tab-icon"><AppIcon :name="t.icon || t.key" :size="20" /></span>
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
                <span class="admin-sheet-icon"><AppIcon :name="t.icon || t.key" :size="16" /></span>
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
import { computed, ref, h, onMounted, watch } from 'vue'
import { NConfigProvider, NMessageProvider, NDialogProvider, NButton, NDropdown } from 'naive-ui'
import axios from 'axios'
import { useAuthStore } from './stores/auth'
import { useNavStore } from './stores/nav'
import { useThemeStore } from './stores/theme'
import { useViewport } from './utils/viewport'
import LogoFull from './components/LogoFull.vue'
import AppIcon from './components/AppIcon.vue'

import { connectWebSocket, wsState, addWsListener } from './utils/ws'

const theme = useThemeStore()
const wsConnected = ref(false)
// ── 风控告警 → Chrome 通知（step3）──
const notifEnabled = ref(localStorage.getItem('notif_enabled') === '1')
const notifHint = computed(() => notifEnabled.value ? '风控告警 Chrome 通知已开启，点击关闭'
                                      : ('开启风控告警 Chrome 通知' + (typeof Notification !== 'undefined' && Notification.permission === 'denied' ? '（浏览器已禁止，请在地址栏设置中放行）' : '')))
function toggleNotif() {
  if (notifEnabled.value) {
    notifEnabled.value = false
    localStorage.setItem('notif_enabled', '0')
    return
  }
  if (typeof Notification === 'undefined') { return }
  if (Notification.permission === 'granted') {
    notifEnabled.value = true
    localStorage.setItem('notif_enabled', '1')
    new Notification('K道 风控告警已开启', { body: '止损/回撤/行业超限将实时推送' })
  } else {
    Notification.requestPermission().then(p => {
      if (p === 'granted') {
        notifEnabled.value = true
        localStorage.setItem('notif_enabled', '1')
        new Notification('K道 风控告警已开启', { body: '止损/回撤/行业超限将实时推送' })
      }
    })
  }
}
addWsListener(data => {
  if (data && data.type === 'risk_alert' && notifEnabled.value
      && typeof Notification !== 'undefined' && Notification.permission === 'granted') {
    for (const a of (data.alerts || [])) {
      const n = new Notification(`风控告警 · ${a.title}`, { body: a.body || '', tag: 'risk-' + a.id })
      n.onclick = () => { window.focus(); nav.switchTab('x'); n.close() }
    }
  }
})

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
import DbExplorerView from './components/DbExplorerView.vue'
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
  { key: 'p', label: '持仓' },
  { key: 'm', label: '选股' },
  { key: 's', label: '信号' },
  { key: 'l', label: '个股', icon: 'filter' },
  { key: 'x', label: '状态' },
]
const adminTabs = [
  { key: 'a', label: '模型' },
  { key: 'f', label: '函数' },
  { key: 'e', label: '特征' },
  { key: 'g', label: 'DAG' },
  { key: 'w', label: '数据' },
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
.app-root { height: 100vh; height: 100dvh; display: flex; flex-direction: column; overflow: hidden; }
/* 组合按钮：合并组内圆角与边框（naive ButtonGroup 子按钮圆角样式未生效，全局兜底） */
.n-button-group { display: inline-flex; }
.n-button-group > .n-button { border-radius: 0 !important; }
.n-button-group > .n-button:first-child { border-radius: 3px 0 0 3px !important; }
.n-button-group > .n-button:last-child { border-radius: 0 3px 3px 0 !important; }
.n-button-group > .n-button:only-child { border-radius: 3px !important; }
.n-button-group > .n-button + .n-button { margin-left: -1px; }

/* 数字防跳动：全站表格与统计值统一等宽数字 */
.app-root .n-data-table td, .app-root .n-data-table th { font-variant-numeric: tabular-nums; }
.app-root .stat-value, .app-root .num { font-variant-numeric: tabular-nums; }

/* ═══ 全站数据表规范：单元格不换行（长文本省略号，横向滚动看全量） ═══ */
.n-data-table-td, .n-data-table-th { white-space: nowrap; }
.n-data-table-td { overflow: hidden; text-overflow: ellipsis; }
/* H5：左侧最多固定一列 —— 第一个固定列保持 sticky（offset 恒为 0），其余固定列退回普通定位 */
@media (max-width: 768px) {
  .n-data-table-td--fixed-left ~ .n-data-table-td--fixed-left,
  .n-data-table-th--fixed-left ~ .n-data-table-th--fixed-left { position: static !important; }
}
.app-header { display: flex; align-items: center; justify-content: space-between; background: var(--c-bg-header); border-bottom: 1px solid var(--c-border); padding: 8px 20px; }
.app-header-left { display: flex; align-items: center; gap: 12px; min-width: 0; }
.brand { font-size: 18px; font-weight: 700; color: var(--c-text); display: flex; align-items: center; gap: 6px; }
.header-date { font-size: 11px; color: var(--c-text-dimmer); white-space: nowrap; }
.app-header-right { display: flex; align-items: center; gap: 12px; }
.user-chip { font-size: 12px; color: var(--c-text-dim); cursor: pointer; white-space: nowrap; display: inline-flex; align-items: center; }
.user-chip .nav-svg { margin-right: 4px; flex-shrink: 0; }
.ws-info { font-size: 10px; display: flex; align-items: center; gap: 3px; white-space: nowrap; color: var(--c-text-dimmer); }
.ws-info .ws-dot { display: inline-block; width: 6px; height: 6px; border-radius: 50%; background: #ef4444; }
.ws-info.ok { color: #10b981; }
.ws-info.ok .ws-dot { background: #10b981; }

.app-nav { display: flex; align-items: center; gap: 4px; padding: 6px 16px; border-bottom: 1px solid var(--c-border); overflow-x: auto; flex-wrap: nowrap; -webkit-overflow-scrolling: touch; scrollbar-width: none; }
.app-nav::-webkit-scrollbar { display: none; }
.app-nav .n-button { flex-shrink: 0; }
.app-nav .n-button .nav-svg { margin-right: 5px; }
.nav-divider { width: 1px; height: 18px; background: var(--c-border); margin: 0 10px; flex-shrink: 0; }

.main-content { flex: 1; overflow-y: auto; padding: 6px 16px; display: flex; flex-direction: column; -webkit-overflow-scrolling: touch; overscroll-behavior: contain; }
.main-content.narrow { padding: 6px 10px calc(64px + env(safe-area-inset-bottom)); }

/* 弹窗统一宽度兜底：固定宽度卡片在窄屏不超出视口（可滚动查看） */
@media (max-width: 768px) {
  .n-modal-container .n-card { max-width: 92vw !important; }
  .n-modal .n-card { max-width: 92vw !important; }
}

/* 中间断点：769~1100px 导航只留图标（10 按钮放不下），标题 tooltip 兜底 */
@media (min-width: 769px) and (max-width: 1100px) {
  .app-nav .nav-label { display: none; }
}

/* H5 底部 TabBar */
.h5-tabbar { position: fixed; left: 0; right: 0; bottom: 0; z-index: 100; display: flex; height: 52px; padding-bottom: env(safe-area-inset-bottom); background: var(--c-bg-header); border-top: 1px solid var(--c-border); }
.h5-tab { flex: 1; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 1px; background: none; border: none; padding: 0; margin: 0; cursor: pointer; color: var(--c-text-dimmer); min-width: 0; }
.h5-tab-icon { font-size: 18px; line-height: 1; display: flex; align-items: center; justify-content: center; }
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
.admin-sheet-icon { font-size: 18px; display: flex; align-items: center; }
.admin-sheet-close { width: 100%; padding: 12px 0; border-radius: 10px; background: var(--c-card-bg); border: 1px solid var(--c-border); color: var(--c-text-dim); font-size: 13px; cursor: pointer; }

@media (max-width: 768px) {
  .app-root { --h5-tabbar-h: 80px; }  /* 底部 TabBar(52px)+safe-area 供页面图高扣减 */
  .app-header { padding: 8px 12px; }
  .app-header-left { gap: 8px; }
  .header-date { font-size: 10px; }
}
</style>
