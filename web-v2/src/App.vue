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
        '--c-accent':theme.colors.accent, '--c-accent-weak':theme.colors.accentWeak,
        '--c-up':theme.colors.up, '--c-down':theme.colors.down,
        background:'var(--c-bg)'}">
        <!-- 顶栏 -->
        <div class="app-header">
          <div class="app-header-left">
            <span class="brand"><span class="brand-mark"></span>K道</span>
            <span class="header-date">{{ headerDateLabel }}</span>
            <span class="header-sep"></span>
            <span class="market-status">{{ marketStatus }}</span>
          </div>
          <div class="app-header-right">
            <span class="ws-info" :class="wsConnected?'ok':'bad'" :title="wsConnected ? '实时连接' : '连接失败'">
              <span class="ws-dot"></span><span class="ws-text">{{ wsConnected?'已连接':'连接失败' }}</span>
            </span>
            <button v-if="!wsConnected" type="button" class="header-icon-btn" title="重新连接" aria-label="重新连接" @click="connectWebSocket">
              <AppIcon name="refresh" :size="16" />
            </button>
            <button type="button" class="header-icon-btn notif-btn" :title="notifHint" :aria-label="notifEnabled ? '关闭通知' : '开启通知'" @click="toggleNotif">
              <AppIcon :name="notifEnabled ? 'bell' : 'bell-off'" :size="16" />
            </button>
            <button type="button" class="header-icon-btn theme-btn" :title="'当前' + theme.modeHint + '，点击切换'" aria-label="切换主题" @click="theme.toggle">
              <AppIcon :name="theme.modeLabel" :size="16" />
            </button>
            <n-dropdown trigger="click" :options="userOptions" @select="handleUserMenu">
              <span class="user-chip" title="账户"><AppIcon name="user" :size="14" /><span class="user-name">{{ auth.username || 'admin' }}</span></span>
            </n-dropdown>
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
          <div v-if="pageMeta" class="page-titlebar">
            <h1>{{ pageMeta.title }}</h1>
            <span>{{ pageMeta.sub }}</span>
          </div>
          <div v-if="nav.tab==='p'" style="flex:1;min-height:0;display:flex;flex-direction:column"><PortfolioView @show-detail="nav.showDetail" /></div>
          <div v-if="nav.tab==='m'"><TreemapView @show-detail="nav.showDetail" /></div>
          <div v-if="nav.tab==='s'" style="flex:1;min-height:0;display:flex;flex-direction:column"><SignalsView @show-detail="nav.showDetail" /></div>
          <div v-if="nav.tab==='l'" style="flex:1;min-height:0;display:flex;flex-direction:column"><StocksView @show-detail="nav.showDetail" /></div>
          <div v-if="nav.tab==='n'" style="flex:1;min-height:0;display:flex;flex-direction:column"><NewsView @show-detail="nav.showDetail" /></div>
          <div v-if="nav.tab==='u'" style="flex:1;min-height:0;display:flex;flex-direction:column"><StockFundView @back="nav.tab = 'x'" @show-detail="nav.showDetail" /></div>
          <div v-if="nav.tab==='d'"><DetailView :code="nav.dcode" @back="nav.backFromDetail" /></div>
          <div v-if="nav.tab==='i'" style="flex:1;min-height:0;display:flex;flex-direction:column"><AgentView @show-detail="nav.showDetail" /></div>
          <div v-if="nav.tab==='x'"><StatusView /></div>
          <div v-if="nav.tab==='a'" style="flex:1;min-height:0;display:flex;flex-direction:column"><ModelView /></div>
          <div v-if="nav.tab==='f'" style="flex:1;min-height:0;display:flex;flex-direction:column"><FunctionView /></div>
          <div v-if="nav.tab==='e'" style="flex:1;min-height:0;display:flex;flex-direction:column"><FeatureView /></div>
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
      <!-- 新版本提示：构建哈希变了（后端 /api/version）才出现 -->
      <div v-if="updateReady" class="app-update" role="status">
        <span>页面有新版本</span>
        <n-button size="tiny" type="primary" @click="reloadNow">刷新</n-button>
      </div>
    </n-message-provider>
    </n-dialog-provider>
  </n-config-provider>
</template>

<script setup>
import { computed, ref, h, onMounted, onUnmounted, watch, watchEffect } from 'vue'
import { NConfigProvider, NMessageProvider, NDialogProvider, NButton, NDropdown } from 'naive-ui'
import axios from 'axios'
import { startVersionWatch, canAutoReload } from './utils/version'
import { useAuthStore } from './stores/auth'
import { useNavStore } from './stores/nav'
import { useThemeStore } from './stores/theme'
import { useViewport } from './utils/viewport'
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
import NewsView from './components/NewsView.vue'
import AgentView from './components/AgentView.vue'
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
const now = ref(new Date())
function fmtHeaderDate(d) {
  const ymd = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
  return ymd + ' 周' + '日一二三四五六'[d.getDay()]
}
const headerDateLabel = ref('—')
let clockTimer = null
const marketStatus = computed(() => {
  const d = now.value
  const day = d.getDay()
  const mins = d.getHours() * 60 + d.getMinutes()
  if (day === 0 || day === 6) return '沪深 已收盘'
  if ((mins >= 570 && mins < 690) || (mins >= 780 && mins < 900)) return '沪深 交易中'
  if (mins < 570) return '沪深 未开盘'
  return '沪深 已收盘'
})



const primaryTabs = [
  { key: 'p', label: '持仓' },
  { key: 'm', label: '选股' },
  { key: 's', label: '信号' },
  { key: 'l', label: '个股', icon: 'filter' },
  { key: 'n', label: '资讯', icon: 'n' },
  { key: 'i', label: '问答', icon: 'compass' },
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

// 全局主题：品牌红 = 上涨红；绿色只表示下跌
const themeOverrides = computed(() => {
  const dark = theme.isDark
  const primary = dark ? '#ff7a85' : '#c22b3f'
  const primaryHover = dark ? '#ff9aa2' : '#a82033'
  const primaryPressed = dark ? '#ff6b7a' : '#8f1a2b'
  const primaryInk = dark ? '#1a0d10' : '#ffffff'
  const overrides = {
    common: {
      primaryColor: primary,
      primaryColorHover: primaryHover,
      primaryColorPressed: primaryPressed,
      primaryColorSuppl: primary,
      successColor: dark ? '#a6abb3' : '#5b6068',
      successColorHover: dark ? '#bcc1c8' : '#4a4f56',
      successColorPressed: dark ? '#8b9199' : '#3a3f45',
      warningColor: dark ? '#f2b64c' : '#8a5a00',
      warningColorHover: dark ? '#ffc76b' : '#6f4900',
      warningColorPressed: dark ? '#e0a53b' : '#553800',
      errorColor: primary,
      errorColorHover: primaryHover,
      errorColorPressed: primaryPressed,
      infoColor: dark ? '#a6abb3' : '#5b6068',
      infoColorHover: dark ? '#bcc1c8' : '#4a4f56',
      infoColorPressed: dark ? '#8b9199' : '#3a3f45',
      borderRadius: '8px',
      borderRadiusSmall: '6px',
      fontFamily: '"Inter", "Segoe UI Variable", "Segoe UI", -apple-system, "PingFang SC", "Noto Sans SC", "Source Han Sans SC", "Microsoft YaHei UI", "Microsoft YaHei", sans-serif',
    },
    Button: {
      textColorPrimary: primaryInk,
      textColorHoverPrimary: primaryInk,
      textColorPressedPrimary: primaryInk,
      textColorFocusPrimary: primaryInk,
      heightTiny: '28px',
      heightSmall: '32px',
      fontSizeTiny: '12px',
      fontSizeSmall: '13px',
    },
    Input: { heightTiny: '28px', heightSmall: '32px', fontSizeTiny: '12px', fontSizeSmall: '13px' },
    Select: { heightTiny: '28px', heightSmall: '32px', fontSizeTiny: '12px', fontSizeSmall: '13px' },
    DatePicker: { heightTiny: '28px', heightSmall: '32px', fontSizeTiny: '12px', fontSizeSmall: '13px' },
  }
  if (isNarrow.value) {
    Object.assign(overrides.Button, { heightTiny: '32px', heightSmall: '36px', heightMedium: '40px', fontSizeTiny: '12px', fontSizeSmall: '13px' })
    overrides.Input = { heightTiny: '32px', heightSmall: '36px', fontSizeTiny: '12px', fontSizeSmall: '13px' }
    overrides.Select = { heightTiny: '32px', heightSmall: '36px', fontSizeTiny: '12px', fontSizeSmall: '13px' }
    overrides.DatePicker = { heightTiny: '32px', heightSmall: '36px', fontSizeTiny: '12px', fontSizeSmall: '13px' }
  }
  return overrides
})

// 主题落到 <html>，让登录页/全局 token 也跟随深浅色
watchEffect(() => {
  const el = document.documentElement
  el.dataset.theme = theme.isDark ? 'dark' : 'light'
})

// 关闭管理抽屉：切到非管理 tab 时收起
watch(() => nav.tab, (t) => { if (!adminTabs.some(x => x.key === t)) showAdmin.value = false })

const userOptions = [
  { label: '退出登录', key: 'logout' }
]
function handleUserMenu(key) {
  if (key === 'logout') auth.logout()
}
// ── 版本自检：构建哈希变化 → 自动/提示刷新（解决"总看到上一版"）──
const updateReady = ref(false)
let stopVersionWatch = null
function reloadNow() { location.reload() }
onMounted(() => {
  clockTimer = setInterval(() => { now.value = new Date() }, 60000)
  stopVersionWatch = startVersionWatch({
    onUpdate: () => {
      if (canAutoReload()) { location.reload(); return }
      updateReady.value = true
    },
  })
})
onUnmounted(() => {
  if (stopVersionWatch) stopVersionWatch()
  if (clockTimer) clearInterval(clockTimer)
})

const showLogin = computed(() => !auth.token)

watch(showLogin, (v) => {
  if (v) document.title = '登录 - K道'
})

const pageMeta = computed(() => ({
  p: { title: '持仓', sub: '账户与持仓' },
  m: { title: '选股', sub: '全市场市值分布' },
  s: { title: '信号', sub: '模型信号与模拟交易' },
  l: { title: '个股', sub: '全市场个股列表' },
  n: { title: '资讯', sub: '快讯、公告与研报' },
  i: { title: '问答', sub: '站内数据问答' },
  x: { title: '状态', sub: '数据与系统状态' },
  a: { title: '模型', sub: '版本、训练与评估' },
  f: { title: '函数', sub: 'KEPL 算子注册表' },
  e: { title: '特征', sub: '特征库与 IC 体检' },
  g: { title: 'DAG', sub: '流程编排与执行' },
  w: { title: '数据', sub: '数据库浏览' },
  u: { title: '证券主档', sub: '个股 / 指数 / ETF' },
}[nav.tab] || null))

const titleMap = {
  p: '持仓 - K道',
  m: '选股 - 市值 - K道',
  s: '买点信号 - K道',
  l: '个股列表 - K道',
  n: '新闻资讯 - K道',
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
        const latest = r.data.overview.latest_date
        headerDateLabel.value = fmtHeaderDate(new Date(latest + 'T00:00:00'))
      }
    } catch(e) {}
  }, 200)
}
</script>

<style>
/* 新版本提示条（右下角悬浮，不遮挡主内容） */
.app-update { position: fixed; right: 16px; bottom: 16px; z-index: 3000; display: flex;
  align-items: center; gap: 10px; padding: 8px 12px; font-size: 12px; color: var(--c-text);
  background: var(--c-card-bg); border: 1px solid rgba(194,43,63,0.45); border-radius: 8px;
  box-shadow: 0 4px 16px rgba(0,0,0,0.25); }

.app-root { height: 100vh; height: 100dvh; display: flex; flex-direction: column; overflow: hidden; }
/* 组合按钮：合并组内圆角与边框（naive ButtonGroup 子按钮圆角样式未生效，全局兜底） */
.n-button-group { display: inline-flex; gap: 2px; padding: 2px; background: var(--surface-2); border-radius: 8px; }
.n-button-group .n-button__border, .n-button-group .n-button__state-border { display: none !important; }
.n-button-group > .n-button { border-radius: 6px !important; border: 0 !important; margin: 0 !important; }
.n-button-group > .n-button:not(.n-button--primary-type) { background: transparent !important; }

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
.page-titlebar { display: flex; align-items: flex-end; gap: 12px; margin: 2px 0 16px; flex-shrink: 0; }
.page-titlebar h1 { margin: 0; font-size: 24px; line-height: 1.2; font-weight: 600; letter-spacing: -0.015em; color: var(--c-text); }
.page-titlebar span { margin-bottom: 3px; font-size: 12px; color: var(--c-text-dim); }
@media (max-width: 768px) {
  .page-titlebar { margin: 0 0 12px; }
  .page-titlebar h1 { font-size: 19px; }
  .page-titlebar span { display: none; }
}

.app-header { display: flex; align-items: center; justify-content: space-between; background: var(--c-bg-header); border-bottom: 1px solid var(--c-border); padding: 0 24px; height: 56px; }
.app-header-left { display: flex; align-items: center; gap: 14px; min-width: 0; }
.brand { font-size: 17px; font-weight: 600; letter-spacing: -0.01em; color: var(--c-text); display: flex; align-items: center; gap: 8px; white-space: nowrap; flex-shrink: 0; }
.brand-mark { width: 4px; height: 20px; border-radius: 2px; background: var(--c-accent); display: inline-block; }
.header-sep { width: 1px; height: 12px; background: var(--c-border); }
.header-date { font-size: 12px; color: var(--c-text-dim); white-space: nowrap; }
.market-status { font-size: 12px; color: var(--c-text-dim); white-space: nowrap; }
.app-header-right { display: flex; align-items: center; gap: 6px; }
.header-icon-btn { width: 32px; height: 32px; display: inline-flex; align-items: center; justify-content: center; border: 1px solid transparent; border-radius: 8px; background: transparent; color: var(--c-text-dim); cursor: pointer; }
.header-icon-btn:hover { background: var(--c-card-bg-hover); color: var(--c-text); }
.user-chip { height: 32px; padding: 0 10px; font-size: 12px; color: var(--c-text); cursor: pointer; white-space: nowrap; display: inline-flex; align-items: center; gap: 6px; border: 1px solid var(--c-border); border-radius: 8px; background: var(--c-bg-header); }
.user-chip:hover { background: var(--c-card-bg-hover); }
.user-chip .nav-svg { margin-right: 4px; flex-shrink: 0; }
.ws-info { font-size: 12px; display: flex; align-items: center; gap: 5px; white-space: nowrap; color: var(--c-text-dim); }
.ws-info .ws-dot { display: inline-block; width: 6px; height: 6px; border-radius: 50%; background: var(--c-error); }
.ws-info.ok { color: var(--c-text-dim); }
.ws-info.ok .ws-dot { background: var(--c-accent); }

.app-nav { display: flex; align-items: stretch; gap: 0; padding: 0 20px; height: 46px; border-bottom: 1px solid var(--c-border); background: var(--c-bg-header); overflow-x: auto; flex-wrap: nowrap; -webkit-overflow-scrolling: touch; scrollbar-width: none; }
.app-nav::-webkit-scrollbar { display: none; }
.app-nav .n-button { flex-shrink: 0; height: 46px !important; border: 0 !important; border-radius: 0 !important; background: transparent !important; box-shadow: none !important; padding: 0 12px !important; color: var(--c-text-dim) !important; font-size: 13px !important; font-weight: 500 !important; position: relative; }
.app-nav .n-button:hover { color: var(--c-text) !important; background: transparent !important; }
.app-nav .n-button--primary-type { color: var(--c-accent) !important; font-weight: 600 !important; }
.app-nav .n-button--primary-type::after { content: ""; position: absolute; left: 12px; right: 12px; bottom: 0; height: 2px; border-radius: 1px; background: var(--c-accent); }
.app-nav .n-button .n-button__border, .app-nav .n-button .n-button__state-border { display: none !important; }
.app-nav .n-button:focus-visible { outline: none !important; }
.app-nav .n-button .n-base-wave, .n-button-group .n-button .n-base-wave { display: none !important; }
.app-nav .n-button .nav-svg { margin-right: 6px; }
.nav-divider { width: 1px; height: 18px; background: var(--c-border); margin: auto 10px; flex-shrink: 0; }

.main-content { flex: 1; overflow-y: auto; padding: 6px 16px; display: flex; flex-direction: column; -webkit-overflow-scrolling: touch; overscroll-behavior: contain; }
.main-content.narrow { padding: 6px 10px calc(64px + env(safe-area-inset-bottom)); }

/* 整页列表统一布局：根节点填满 main-content（自动随视口高度变化，无需 JS 重算），
   筛选区/分页器固定，n-data-table 加 flex-height + .fill-table 内部滚动，分页器始终一屏可见 */
.page-fill { flex: 1; min-height: 0; display: flex; flex-direction: column; overflow: hidden; }
.fill-table { flex: 1; min-height: 0; }
.page-fill .no-shrink { flex-shrink: 0; }
/* page-fill 是列向 flex，直接子元素默认被 align-items:stretch 拉成整条通栏：
   行内控件（裸按钮/开关等）必须显式收起；工具行用 display:flex 的 div 包住则天然对齐 */
.page-fill .self-start { align-self: flex-start; }

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
.h5-tabbar { position: fixed; left: 0; right: 0; bottom: 0; z-index: 100; display: flex; height: 58px; padding-bottom: env(safe-area-inset-bottom); background: var(--c-bg-header); border-top: 1px solid var(--c-border); }
.h5-tab { flex: 1; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 2px; background: none; border: none; padding: 0; margin: 0; cursor: pointer; color: var(--c-text-dim); min-width: 0; }
.h5-tab-icon { font-size: 18px; line-height: 1; display: flex; align-items: center; justify-content: center; }
.h5-tab-label { font-size: 11px; line-height: 1.2; }
.h5-tab.active { color: var(--c-accent); }
.h5-tab.active .h5-tab-label { font-weight: 600; }

/* H5 管理抽屉 */
.admin-sheet-mask { position: fixed; inset: 0; z-index: 120; background: rgba(0, 0, 0, .4); display: flex; align-items: flex-end; }
.admin-sheet { width: 100%; background: var(--c-bg-header); border-radius: 16px 16px 0 0; padding: 16px 16px calc(16px + env(safe-area-inset-bottom)); box-shadow: 0 -4px 20px rgba(0,0,0,.15); }
.admin-sheet-title { font-size: 13px; font-weight: 600; color: var(--c-text); text-align: center; margin-bottom: 12px; }
.admin-sheet-grid { display: grid; grid-template-columns: repeat(2, 1fr); gap: 10px; margin-bottom: 12px; }
.admin-sheet-item { display: flex; align-items: center; justify-content: center; gap: 6px; padding: 14px 0; border-radius: 10px; background: var(--c-card-bg); border: 1px solid var(--c-border); color: var(--c-text); font-size: 13px; cursor: pointer; }
.admin-sheet-item.active { color: var(--c-accent); border-color: var(--c-accent); background: var(--c-accent-weak); }
.admin-sheet-icon { font-size: 18px; display: flex; align-items: center; }
.admin-sheet-close { width: 100%; padding: 12px 0; border-radius: 10px; background: var(--c-card-bg); border: 1px solid var(--c-border); color: var(--c-text-dim); font-size: 13px; cursor: pointer; }

@media (max-width: 768px) {
  .app-root { --h5-tabbar-h: 80px; }  /* 底部 TabBar(52px)+safe-area 供页面图高扣减 */
  .app-header { padding: 0 12px; }
  .app-header-left { gap: 8px; min-width: 0; overflow: hidden; }
  .app-header-right { gap: 2px; }
  .brand { font-size: 16px; gap: 6px; }
  .brand-mark { height: 18px; }
  .header-date { font-size: 11px; overflow: hidden; text-overflow: ellipsis; }
  .market-status, .header-sep, .notif-btn, .ws-text { display: none; }
  .user-name { display: none; }
  .user-chip { padding: 0 8px; }
}
</style>
