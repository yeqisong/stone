<template>
<div class="dbx-root">
  <!-- 顶部：库概览 -->
  <div class="dbx-top">
    <div v-for="m in overviewCards" :key="m.label" class="ov-card">
      <div class="ov-label">{{ m.label }}</div>
      <div class="ov-value">{{ m.value }}</div>
    </div>
    <div style="flex:1" />
    <n-button size="small" quaternary @click="loadAll"><AppIcon name="refresh" :size="13" />  刷新</n-button>
  </div>

  <!-- 主体：左表清单（独立滚动） + 右数据（独立滚动） -->
  <div class="dbx-body">
    <aside class="dbx-left">
      <n-input v-model:value="search" size="small" placeholder="搜索表名" clearable />
      <div class="dbx-table-list">
        <n-spin v-if="loadingTables" size="small" style="padding:20px" />
        <template v-else>
          <div v-for="t in filteredTables" :key="t.name" class="tbl-item"
               :class="{ active: t.name === selected }" @click="selectTable(t.name)">
            <div class="tbl-item-top">
              <span class="tbl-name">{{ t.name }}</span>
              <span class="tbl-size">{{ t.size }}</span>
            </div>
            <div class="tbl-sub">{{ t.comment || (t.rows_est.toLocaleString() + ' 行(估)') }}</div>
          </div>
          <n-empty v-if="!filteredTables.length" description="无匹配表" size="small" style="padding:20px" />
        </template>
      </div>
    </aside>

    <!-- 右：数据表 -->
    <section class="dbx-right">
      <template v-if="selected">
        <div class="dbx-toolbar">
          <div class="dbx-title">
            <span class="dbx-table-name">{{ selected }}</span>
            <span v-if="currentComment" class="dbx-table-comment">{{ currentComment }}</span>
            <span class="dbx-rows">{{ totalRows.toLocaleString() }} 行 · 只读</span>
          </div>
          <div class="dbx-tools">
            <n-button size="tiny" quaternary @click="showSchema = true"><AppIcon name="folder" :size="13" />  字段 ({{ columns.length }})</n-button>
            <n-select v-model:value="orderBy" size="tiny" style="width:150px" clearable
              :options="columns.map(c => ({ label: c.name, value: c.name }))"
              placeholder="排序列" @update:value="() => loadData()" />
            <n-select v-model:value="orderDir" size="tiny" style="width:72px"
              :options="[{ label: '升序', value: 'asc' }, { label: '降序', value: 'desc' }]"
              @update:value="() => loadData()" />
          </div>
        </div>

        <div class="dbx-table-scroll">
          <n-spin v-if="loadingRows" size="small" style="padding:30px" />
          <template v-else>
            <table v-if="rows.length" class="dbx-table">
              <thead>
                <tr>
                  <th v-for="c in columns" :key="c.name" :class="{ num: isNumeric(c.type) }">
                    <span class="th-name">{{ c.name }}</span>
                    <n-tooltip v-if="c.comment" trigger="hover" placement="top" :show-arrow="false">
                      <template #trigger><span class="th-q">?</span></template>
                      <span style="font-size:12px">{{ c.comment }}</span>
                    </n-tooltip>
                  </th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="(row, i) in rows" :key="i">
                  <td v-for="c in columns" :key="c.name" :class="{ num: isNumeric(c.type), 'is-null': row[c.name] === null }">
                    <span class="cell" :title="row[c.name] !== null ? fmt(row[c.name]) : ''">{{ fmt(row[c.name]) }}</span>
                  </td>
                </tr>
              </tbody>
            </table>
            <n-empty v-else description="无数据" size="small" style="padding:30px" />
          </template>
        </div>

        <div class="dbx-pager">
          <n-pagination v-if="totalRows > pageSize" :page="page" :page-size="pageSize"
            :item-count="totalRows" @update:page="p => { page = p; loadData() }" size="small" />
          <span v-else class="pager-hint">最多显示 {{ pageSize }} 行 / 页</span>
        </div>
      </template>
      <n-empty v-else description="点击左侧表查看数据" style="margin:auto" />
    </section>
  </div>

  <!-- 字段元数据抽屉（按钮入口） -->
  <n-drawer v-model:show="showSchema" placement="right" :width="isNarrow ? '100%' : 480">
    <n-drawer-content :title="`字段定义 · ${selected}`" closable>
      <n-data-table :columns="schemaCols" :data="columns" size="small" :max-height="560"
        :row-key="r => r.name" />
    </n-drawer-content>
  </n-drawer>
</div>
</template>

<script setup>
import AppIcon from './AppIcon.vue'
import { ref, computed, onMounted, h } from 'vue'
import { useViewport } from '../utils/viewport'
const { isNarrow } = useViewport()
import { NButton, NInput, NDataTable, NEmpty, NPagination, NSelect, NSpin, NTooltip, NDrawer, NDrawerContent } from 'naive-ui'
import axios from 'axios'

const API = window.location.origin
const authHeaders = () => ({ Authorization: 'Bearer ' + (localStorage.getItem('token') || '') })

const overview = ref(null)
const tables = ref([])
const search = ref('')
const loadingTables = ref(false)
const selected = ref('')
const columns = ref([])
const rows = ref([])
const totalRows = ref(0)
const page = ref(1)
const pageSize = 50
const loadingRows = ref(false)
const orderBy = ref(null)
const orderDir = ref('desc')
const showSchema = ref(false)

const overviewCards = computed(() => {
  const o = overview.value || {}
  return [
    { label: '数据库', value: o.database || '—' },
    { label: '库大小', value: o.size || '—' },
    { label: '表数量', value: o.n_tables ?? '—' },
    { label: 'PostgreSQL', value: o.version || '—' },
  ]
})

const filteredTables = computed(() => {
  const q = (search.value || '').toLowerCase()
  return q ? tables.value.filter(t => t.name.includes(q)) : tables.value
})

const currentComment = computed(() => {
  const t = tables.value.find(x => x.name === selected.value)
  return t ? (t.comment || '') : ''
})

const isNumeric = t => /int|num|dec|real|float|double|money|serial/i.test(t || '')

function fmt(v) {
  if (v === null || v === undefined) return 'NULL'
  if (typeof v === 'object') {
    const s = JSON.stringify(v)
    return s.length > 80 ? s.slice(0, 80) + '…' : s
  }
  return String(v)
}

const schemaCols = [
  { title: '字段', key: 'name', width: 170, render: r => h('code', { style: 'font-size:11px' }, r.name) },
  { title: '类型', key: 'type', width: 140, render: r => h('span', { style: 'font-size:11px;color:var(--c-text-dim)' }, r.type) },
  { title: '可空', key: 'nullable', width: 56, align: 'center', render: r => (r.nullable ? '✓' : '') },
  { title: '默认值', key: 'default', width: 110, ellipsis: { tooltip: true }, render: r => r.default || '—' },
  { title: '注释', key: 'comment', ellipsis: { tooltip: true }, render: r => r.comment || '—' },
]

async function loadAll() {
  loadingTables.value = true
  try {
    const [o, t] = await Promise.all([
      axios.get(API + '/api/dbex/overview', { headers: authHeaders() }),
      axios.get(API + '/api/dbex/tables', { headers: authHeaders() }),
    ])
    overview.value = o.data
    tables.value = t.data.tables || []
  } catch (e) { console.error(e) }
  loadingTables.value = false
}

async function selectTable(name) {
  selected.value = name
  page.value = 1
  orderBy.value = null
  orderDir.value = 'desc'
  try {
    const c = await axios.get(API + `/api/dbex/tables/${name}/columns`, { headers: authHeaders() })
    columns.value = c.data.columns || []
  } catch (e) { columns.value = [] }
  loadData()
}

async function loadData() {
  if (!selected.value) return
  loadingRows.value = true
  try {
    const params = { offset: (page.value - 1) * pageSize, limit: pageSize }
    if (orderBy.value) { params.order_by = orderBy.value; params.order_dir = orderDir.value }
    const r = await axios.get(API + `/api/dbex/tables/${selected.value}/rows`, { params, headers: authHeaders() })
    rows.value = r.data.rows
    totalRows.value = r.data.total
  } catch (e) { console.error(e) }
  loadingRows.value = false
}

onMounted(loadAll)
</script>

<style scoped>
.dbx-root { flex: 1; min-height: 0; display: flex; flex-direction: column; gap: 10px; padding: 4px 2px; }

/* 顶部概览 */
.dbx-top { display: flex; gap: 10px; flex-wrap: wrap; align-items: center; }
.ov-card { background: var(--c-card-bg); border: 1px solid var(--c-border); border-radius: 8px; padding: 8px 14px; text-align: center; min-width: 86px; }
.ov-label { font-size: 10px; color: var(--c-text-faint); }
.ov-value { font-size: 14px; font-weight: 700; color: var(--c-text); margin-top: 2px; }

/* 主体：左清单 + 右数据，各自独立滚动，页面不滚动 */
.dbx-body { flex: 1; min-height: 0; display: flex; gap: 12px; }

.dbx-left { width: 300px; flex-shrink: 0; display: flex; flex-direction: column; gap: 8px; min-height: 0; }
.dbx-table-list { flex: 1; min-height: 0; overflow-y: auto; display: flex; flex-direction: column; gap: 3px; padding-right: 2px; }

.tbl-item { cursor: pointer; padding: 7px 10px; border-radius: 6px; background: var(--c-card-bg); border: 1px solid var(--c-border); transition: border-color .15s; flex-shrink: 0; }
.tbl-item:hover { border-color: #2080f0; }
.tbl-item.active { border-color: #2080f0; background: rgba(32, 128, 240, .10); }
.tbl-item-top { display: flex; justify-content: space-between; align-items: baseline; gap: 6px; }
.tbl-name { font-size: 12px; font-weight: 600; color: var(--c-text); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.tbl-size { font-size: 10px; color: var(--c-text-faint); flex-shrink: 0; }
.tbl-sub { font-size: 10px; color: var(--c-text-faint); margin-top: 2px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

/* 右侧数据区 */
.dbx-right { flex: 1; min-width: 0; min-height: 0; display: flex; flex-direction: column; gap: 8px; background: var(--c-card-bg); border: 1px solid var(--c-border); border-radius: 8px; padding: 10px 12px; }
.dbx-toolbar { display: flex; align-items: center; justify-content: space-between; gap: 8px; flex-wrap: wrap; }
.dbx-title { display: flex; align-items: baseline; gap: 8px; min-width: 0; }
.dbx-table-name { font-size: 13px; font-weight: 700; color: var(--c-text); }
.dbx-table-comment { font-size: 11px; color: var(--c-text-faint); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 260px; }
.dbx-rows { font-size: 11px; color: var(--c-text-dim); flex-shrink: 0; }
.dbx-tools { display: flex; gap: 6px; align-items: center; flex-shrink: 0; }

/* 数据表 */
.dbx-table-scroll { flex: 1; min-height: 0; overflow: auto; border: 1px solid var(--c-border); border-radius: 6px; }
.dbx-table { border-collapse: collapse; width: 100%; font-size: 12px; min-width: max-content; }
.dbx-table thead th {
  position: sticky; top: 0; z-index: 1;
  background: var(--c-bg); color: var(--c-text-dim);
  font-size: 11px; font-weight: 600; text-align: left; white-space: nowrap;
  padding: 8px 12px; border-bottom: 1px solid var(--c-border);
}
.dbx-table thead th.num, .dbx-table tbody td.num { text-align: right; }
.dbx-table tbody td {
  padding: 7px 12px; color: var(--c-text);
  border-bottom: 1px solid var(--c-border-light);
  white-space: nowrap; max-width: 360px;
}
.dbx-table tbody td .cell {
  display: inline-block; max-width: 336px;
  overflow: hidden; text-overflow: ellipsis; vertical-align: bottom;
}
.dbx-table tbody tr:nth-child(even) td { background: var(--c-bg); }
.dbx-table tbody tr:hover td { background: var(--c-card-bg-hover); }
.dbx-table tbody td.is-null { color: var(--c-text-faint); font-style: italic; }

.th-name { margin-right: 4px; }
.th-q {
  display: inline-flex; align-items: center; justify-content: center;
  width: 13px; height: 13px; border-radius: 50%;
  font-size: 9px; line-height: 1; cursor: help;
  color: #2080f0; background: rgba(32, 128, 240, .12);
  vertical-align: 1px;
}
.dbx-table thead th:hover .th-q { background: rgba(32, 128, 240, .25); }

/* 分页 */
.dbx-pager { display: flex; justify-content: center; align-items: center; min-height: 28px; }
.pager-hint { font-size: 11px; color: var(--c-text-faint); }
</style>

/* H5：左清单 300px 固定宽在窄屏挤压数据区 → 纵向布局，清单收成顶部横向列表 */
@media (max-width: 768px) {
  .dbx-body { flex-direction: column; }
  .dbx-left { width: 100%; flex-shrink: 1; max-height: 30vh; }
  .dbx-table-list { flex-direction: row; flex-wrap: wrap; }
  .tbl-item { font-size: 11px; padding: 4px 8px; }
}
