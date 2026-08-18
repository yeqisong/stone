<template>
<div style="flex:1;min-height:0;display:flex;flex-direction:column;box-sizing:border-box;overflow:hidden">
  <!-- Header -->
  <div style="display:flex;align-items:center;gap:10px;margin-bottom:10px;flex-shrink:0">
    <n-button size="small" quaternary @click="$emit('back')">← 返回</n-button>
    <span style="font-size:17px;font-weight:700;color:var(--c-text)">{{ title }}</span>
    <n-select v-model:value="filterType" :options="typeOpts" size="small" style="width:100px" @update:value="onTypeChange" />
    <n-input v-model:value="search" size="small" placeholder="搜索代码/名称" style="width:180px" clearable @keyup.enter="loadData(1)" />
  </div>

  <!-- Table: 固定总宽 scroll-x，长文本列省略号，纵向单滚动 -->
  <div style="flex:1;min-height:0;overflow:hidden">
    <n-data-table :columns="cols" :data="items" size="small" :loading="loading" :bordered="false" :single-line="true" :scroll-x="3200" :max-height="'100%'" />
  </div>

  <!-- Pagination -->
  <div style="display:flex;justify-content:flex-end;padding:8px 0;flex-shrink:0">
    <n-pagination :page="page" :page-count="pageCount" :page-size="pageSize" @update:page="loadData" />
  </div>

  <!-- History Modal -->
  <n-modal v-model:show="showHistModal" preset="card" :title="'📊 基本面历史 — ' + histCode" style="width:800px;max-width:92vw" :segmented="{content:true}">
    <n-data-table :columns="histCols" :data="histItems" size="small" :bordered="false" :loading="histLoading" />
    <n-pagination v-if="histTotal > histPageSize" :page="histPage" :page-count="Math.ceil(histTotal/histPageSize)" :page-size="histPageSize" size="small" style="margin-top:8px;justify-content:flex-end" @update:page="loadHist" />
  </n-modal>
</div>
</template>

<script setup>
import { ref, computed, h, onMounted } from 'vue'
import { NButton, NDataTable, NInput, NSelect, NPagination, NModal, NTag } from 'naive-ui'
import axios from 'axios'
import { useNavStore } from '../stores/nav'

const emit = defineEmits(['back', 'show-detail'])
const API = window.location.origin
const nav = useNavStore()

const filterType = ref('stock')
const search = ref('')
const loading = ref(false)
const items = ref([])
const page = ref(1)
const total = ref(0)
const pageSize = ref(50)

const typeOpts = [
  { label: '个股', value: 'stock' },
  { label: '指数', value: 'index' },
  { label: 'ETF', value: 'etf' },
]

const title = computed(() => {
  const m = { stock: '个股列表', index: '指数列表', etf: 'ETF 列表' }
  return m[filterType.value] || '股票列表'
})

const pageCount = computed(() => Math.ceil(total.value / pageSize.value))

async function loadData(p, syncUrl = true) {
  page.value = p || 1
  loading.value = true
  try {
    const r = await axios.get(API + '/api/stock_fund_list', {
      params: { stock_type: filterType.value, page: page.value, page_size: pageSize.value, search: search.value }
    })
    items.value = r.data.items || []
    total.value = r.data.total || 0
  } catch(e) { console.error(e) }
  loading.value = false
}

// 类型切换：同步 URL（path 体现 /stock-fund/:type）+ 重新加载
function onTypeChange(v) {
  filterType.value = v
  nav.stockFundType = v
  nav.syncHash()
  loadData(1, false)
}

// 挂载时：从 URL(nav.stockFundType) 初始化并加载——否则刚进入空白
onMounted(() => {
  filterType.value = nav.stockFundType || 'stock'
  nav.tab = 'u'
  nav.syncHash()
  loadData(1, false)
})

// Columns - all stock_master + fundamentals（前2列固定左 + 跳详情超链接）
const linkStyle = { color: '#2080f0', cursor: 'pointer', textDecoration: 'underline' }
// render 列文本统一省略号（naive ellipsis 属性对 render 列不生效，需自行控制），title 悬浮全文
const ell = (txt, extra = {}) => {
  const s = String(txt ?? '')
  const { onClick, style: extraStyle, maxWidth, ...rest } = extra
  return h('span', {
    style: { display: 'block', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', maxWidth: maxWidth || '100%', ...extraStyle },
    title: s, onClick, ...rest,
  }, s || '—')
}
const cols = [
  { title: '代码', key: 'stock_code', width: 78, fixed: 'left', render(r) {
    return h('span', { style: linkStyle, onClick: () => emit('show-detail', r.stock_code) }, r.stock_code)
  } },
  { title: '名称', key: 'stock_name', width: 150, fixed: 'left', render(r) {
    return ell(r.stock_name || r.stock_code, { maxWidth: '138px', ...linkStyle, textDecoration: 'none', color: '#2080f0', cursor: 'pointer', onClick: () => emit('show-detail', r.stock_code) })
  } },
  { title: '类型', key: 'stock_type', width: 50, render(r) { return { stock: '股', index: '指', etf: 'ETF' }[r.stock_type] || r.stock_type } },
  { title: '交易所', key: 'exchange', width: 60 },
  { title: '行业', key: 'industry', width: 170, render(r) {
    const l1 = r.industry_l1, l2 = r.industry_l2
    return ell((l1 && l2) ? l1 + ' > ' + l2 : (l1 || l2 || r.industry || '—'), { maxWidth: '158px' })
  } },
  { title: '上市日', key: 'ipo_date', width: 90 },
  { title: '状态', key: 'status', width: 45, render(r) { return r.status === 'N' ? '正常' : '退市' } },
  { title: '退市日', key: 'delist_date', width: 90 },
  { title: '沪深港通', key: 'is_hs', width: 72 },
  { title: '实控人', key: 'act_name', width: 130, render(r) { return ell(r.act_name, { maxWidth: '118px' }) } },
  { title: '地域', key: 'area', width: 85, render(r) { return ell(r.area, { maxWidth: '73px' }) } },
  { title: '注册资本', key: 'reg_capital', width: 75, align: 'right', render(r) { return r.reg_capital != null ? (r.reg_capital / 1e8).toFixed(2) + '亿' : '—' } },
  { title: '员工', key: 'employees', width: 60, align: 'right', render(r) { return r.employees || '—' } },
  { title: '主营业务', key: 'main_business', width: 230, render(r) { return ell(r.main_business, { maxWidth: '218px' }) } },
  // Fundamentals
  { title: 'PE', key: 'pe_ttm', width: 55, align: 'right', render(r) { return r.pe_ttm != null ? r.pe_ttm.toFixed(2) : '—' } },
  { title: 'PB', key: 'pb_mrq', width: 55, align: 'right', render(r) { return r.pb_mrq != null ? r.pb_mrq.toFixed(2) : '—' } },
  { title: 'PS(TTM)', key: 'ps_ttm', width: 65, align: 'right', render(r) { return r.ps_ttm != null ? r.ps_ttm.toFixed(2) : '—' } },
  { title: '股息率%', key: 'dv_ratio', width: 65, align: 'right', render(r) { return r.dv_ratio != null ? r.dv_ratio.toFixed(2) : '—' } },
  { title: '股息TTM%', key: 'dv_ttm', width: 75, align: 'right', render(r) { return r.dv_ttm != null ? r.dv_ttm.toFixed(2) : '—' } },
  { title: '换手率%', key: 'turnover_rate', width: 65, align: 'right', render(r) { return r.turnover_rate != null ? r.turnover_rate.toFixed(2) : '—' } },
  { title: '量比', key: 'volume_ratio', width: 55, align: 'right', render(r) { return r.volume_ratio != null ? r.volume_ratio.toFixed(2) : '—' } },
  { title: '市值(亿)', key: 'market_cap', width: 70, align: 'right', render(r) { return r.market_cap != null ? (r.market_cap / 1e8).toFixed(2) : '—' } },
  { title: '流通市值', key: 'circ_mv', width: 80, align: 'right', render(r) { return r.circ_mv != null ? (r.circ_mv / 1e8).toFixed(2) + '亿' : '—' } },
  { title: '总股本', key: 'total_shares', width: 80, align: 'right', render(r) { return r.total_shares != null ? (r.total_shares / 1e8).toFixed(2) + '亿' : '—' } },
  { title: '流通股本', key: 'float_share', width: 80, align: 'right', render(r) { return r.float_share != null ? (r.float_share / 1e8).toFixed(2) + '亿' : '—' } },
  { title: 'ROE%', key: 'roe', width: 55, align: 'right', render(r) { return r.roe != null ? r.roe.toFixed(2) : '—' } },
  { title: '更新', key: 'fund_date', width: 85 },
  { title: '操作', key: 'actions', width: 70, fixed: 'right', render(r) {
    return h(NButton, { size: 'tiny', quaternary: true, onClick: () => openHist(r.stock_code) }, () => '历史')
  }},
]

// History modal
const showHistModal = ref(false)
const histCode = ref('')
const histItems = ref([])
const histTotal = ref(0)
const histPage = ref(1)
const histLoading = ref(false)
const histPageSize = 20

const histCols = [
  { title: '日期', key: 'trade_date', width: 85 },
  { title: 'PE', key: 'pe_ttm', width: 55, render(r) { return r.pe_ttm ?? '—' } },
  { title: 'PB', key: 'pb_mrq', width: 55, render(r) { return r.pb_mrq ?? '—' } },
  { title: 'PS', key: 'ps_ttm', width: 55, render(r) { return r.ps_ttm ?? '—' } },
  { title: '股息率', key: 'dv_ratio', width: 60, render(r) { return r.dv_ratio ?? '—' } },
  { title: '换手率', key: 'turnover_rate', width: 60, render(r) { return r.turnover_rate ?? '—' } },
  { title: '量比', key: 'volume_ratio', width: 55, render(r) { return r.volume_ratio ?? '—' } },
  { title: '市值', key: 'market_cap', width: 70, render(r) { return r.market_cap != null ? (r.market_cap/1e8).toFixed(2) : '—' } },
  { title: 'ROE', key: 'roe', width: 50, render(r) { return r.roe ?? '—' } },
]

async function loadHist(p) {
  histPage.value = p || 1
  histLoading.value = true
  try {
    const r = await axios.get(API + '/api/stock/' + histCode.value + '/fundamentals', { params: { page: histPage.value, page_size: histPageSize } })
    histItems.value = r.data.items || []
    histTotal.value = r.data.total || 0
  } catch(e) {
    // Fallback: query directly
    try {
      const r2 = await axios.get(API + '/api/data_status/fundamentals_history', { params: { code: histCode.value, page: histPage.value, page_size: histPageSize } })
      histItems.value = r2.data.items || []
      histTotal.value = r2.data.total || 0
    } catch(e2) { console.error(e2) }
  }
  histLoading.value = false
}

function openHist(code) {
  histCode.value = code
  histItems.value = []
  histTotal.value = 0
  histPage.value = 1
  showHistModal.value = true
  loadHist(1)
}
</script>

<style>
/* 表头强制不换行（naive 表格 th 默认可能 word-break 长中文标题） */
.n-data-table thead th,
.n-data-table thead th .n-data-table-th__title {
  white-space: nowrap;
}
</style>
