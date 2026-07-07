<template>
<div style="padding:20px;max-width:1200px;margin:0 auto">
  <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:16px">
    <div style="font-size:18px;font-weight:700;color:var(--c-text)">函数管理（Operator Registry）</div>
    <n-button type="primary" size="small" @click="openCreate">+ 新增函数</n-button>
  </div>

  <div style="display:flex;gap:8px;margin-bottom:12px;flex-wrap:wrap">
    <n-select v-model:value="filterCategory" :options="catOptions" size="small" style="width:110px" placeholder="分类" clearable @update:value="loadData" />
    <n-select v-model:value="filterStatus" :options="statusOptions" size="small" style="width:110px" placeholder="状态" clearable @update:value="loadData" />
    <n-input v-model:value="searchText" size="small" style="width:180px" placeholder="搜索名称" clearable @keyup.enter="loadData" />
    <n-button size="small" @click="loadData">查询</n-button>
  </div>

  <n-data-table :columns="columns" :data="items" :loading="loading" size="small"
    :row-props="rowProps" :expanded-row-keys="expandedKeys" @update:expanded-row-keys="onExpand" />
  <div style="display:flex;justify-content:center;margin-top:10px">
    <n-pagination v-if="totalPages > 1" :page="page" :page-count="totalPages" @update:page="p => { page = p; loadData() }" size="small" />
  </div>

  <!-- Create/Edit Modal -->
  <n-modal v-model:show="showCreate" preset="card" :title="editId ? '编辑函数' : '新增函数'" style="width:850px;max-width:95vw" :mask-closable="false">
    <div style="display:flex;gap:16px">
      <div style="flex:1;min-width:0">
        <n-space vertical>
          <n-input v-model:value="form.name" placeholder="函数英文名（小写字母开头，如 ma, rsi_14）" :disabled="!!editId && isBuiltin" />
          <n-input v-model:value="form.display_name" placeholder="函数中文名" />
          <n-input v-model:value="form.description" type="textarea" placeholder="功能描述，如：计算N周期简单移动平均" :rows="2" />
          <n-select v-model:value="form.category" :options="catOpts" placeholder="分类" />
          <div style="display:flex;align-items:center;justify-content:space-between;margin-top:4px">
            <div style="font-size:12px;font-weight:600;color:var(--c-text-dim)">函数定义（Python def，第一个参数必须是 df）</div>
            <n-button size="tiny" quaternary @click="showAiPrompt = true" :loading="aiLoading" style="font-size:11px">🤖 AI 生成</n-button>
          </div>
          <MonacoEditor v-model="form.source_code" @update:modelValue="parseParams" />
          <!-- 动态参数表 -->
          <div v-if="formParams.length" style="background:var(--c-card-bg);border:1px solid var(--c-border);border-radius:6px;padding:10px">
            <div style="font-size:11px;font-weight:600;color:var(--c-text-dim);margin-bottom:6px">参数配置（从函数签名自动解析）</div>
            <div v-for="(p, i) in formParams" :key="i" style="display:flex;gap:6px;align-items:center;margin-bottom:4px">
              <span style="font-size:11px;color:var(--c-text);min-width:60px">{{ p.name }}</span>
              <n-select v-model:value="p.type" :options="paramTypeOpts" size="tiny" style="width:90px" />
              <n-input v-model:value="p.default" size="tiny" placeholder="默认值" style="width:60px" />
              <n-input v-model:value="p.desc" size="tiny" placeholder="说明" style="flex:1" />
            </div>
          </div>
        </n-space>
      </div>
      <!-- 编码规则侧边栏 -->
      <div style="width:220px;flex-shrink:0;border-left:1px solid var(--c-border);padding-left:12px;font-size:10px;color:var(--c-text-dim);overflow-y:auto;max-height:520px">
        <div style="font-weight:600;color:var(--c-text);margin-bottom:8px">📐 编码规则</div>
        <div style="margin-bottom:10px">
          <div style="color:#f59e0b;font-weight:600;margin-bottom:3px">函数签名</div>
          <div>• 第一个参数必须是 <b>df</b>（接收数据列）</div>
          <div>• 后续参数名小写字母开头</div>
          <div>• 用 <b>默认值</b> 声明可选参数</div>
          <div style="background:var(--c-card-bg);padding:4px 6px;border-radius:4px;margin-top:3px;font-family:monospace;font-size:9px;word-break:break-all">
            def func(df, window=5)
          </div>
        </div>
        <div style="margin-bottom:10px">
          <div style="color:#ef4444;font-weight:600;margin-bottom:3px">🛡 代码安全</div>
          <div>禁止: import, exec, eval, open, os.system, subprocess, __import__</div>
        </div>
        <div style="margin-bottom:10px">
          <div style="color:#f59e0b;font-weight:600;margin-bottom:3px">⚡ 性能约束</div>
          <div>• 沙箱限时 2s，超时禁止发布</div>
          <div>• 超过 500ms 黄色警告</div>
        </div>
        <div style="margin-bottom:10px">
          <div style="color:#2080f0;font-weight:600;margin-bottom:3px">📊 向量化要求</div>
          <div>• 必须用 Pandas/NumPy 向量化</div>
          <div>• 禁止 for 循环逐行处理</div>
          <div>• 返回值长度 = 输入长度</div>
        </div>
        <div style="margin-bottom:10px">
          <div style="color:#10b981;font-weight:600;margin-bottom:3px">🧪 参数类型体系</div>
          <div>• <b>标量</b> → 传入默认值，如 5 / 0.05</div>
          <div>• <b>序列</b> → 一维随机数组，如收盘价</div>
          <div>• <b>矩阵</b> → 二维 DataFrame，如多股票表</div>
          <div style="margin-top:3px;font-size:9px;color:var(--c-text-faint)">函数不耦合股票字段，通过类型绑定业务数据</div>
        </div>
      </div>
    </div>
    <template #footer>
      <n-space justify="flex-end">
        <n-button @click="showCreate = false">取消</n-button>
        <n-button @click="showTestRun = true">🧪 试一试</n-button>
        <n-button @click="doCreate" :loading="creating">存为草稿</n-button>
        <n-button v-if="editId" type="primary" @click="publishFunc" :loading="publishing">📤 保存并发布</n-button>
      </n-space>
    </template>
  </n-modal>

  <!-- 版本历史弹窗 -->
  <n-modal v-model:show="showVersions" preset="card" :title="'版本历史 — ' + (detailItem?.name || '')" style="width:520px;max-width:92vw">
    <n-data-table v-if="versions.length" :columns="verCols" :data="versions" size="small" :row-props="verRowProps" />
    <n-empty v-else description="暂无版本记录，发布后自动保存快照" style="padding:20px" />
  </n-modal>

  <!-- 试运行弹窗 -->
  <n-modal v-model:show="showTestRun" preset="card" title="🧪 试运行" style="width:600px;max-width:92vw">
    <n-space vertical>
      <div style="font-size:12px;color:var(--c-text-dim)">对函数进行沙箱测试，检验语法、安全性和性能。</div>
      <div style="display:flex;gap:8px;align-items:center;margin-top:8px">
        <n-button type="primary" size="small" @click="doTestRun" :loading="testRunning">▶ 执行测试</n-button>
        <n-button size="small" @click="publishFunc" :loading="publishing">📤 保存并发布</n-button>
      </div>
      <div v-if="testResult" style="margin-top:8px">
        <div style="display:flex;gap:8px;align-items:center;margin-bottom:6px">
          <span style="font-size:11px;color:var(--c-text-dim)">耗时：</span>
          <span :style="{fontSize:'14px',fontWeight:700,color:testResult.elapsed_ms<500?'#10b981':testResult.elapsed_ms<2000?'#f59e0b':'#ef4444'}">
            {{ testResult.elapsed_ms }}ms
          </span>
          <n-tag :type="testResult.elapsed_ms<500?'success':testResult.elapsed_ms<2000?'warning':'error'" size="small">
            {{ testResult.elapsed_ms<500 ? '✅ 通过' : testResult.elapsed_ms<2000 ? '⚠️ 警告' : '❌ 不合格' }}
          </n-tag>
        </div>
        <div v-if="testResult.error" style="background:rgba(239,68,68,.08);border:1px solid rgba(239,68,68,.2);border-radius:6px;padding:8px;font-family:monospace;font-size:10px;color:#ef4444;white-space:pre-wrap;max-height:200px;overflow-y:auto">{{ testResult.error }}</div>
        <div v-else-if="testResult.preview" style="margin-top:8px">
          <div style="font-size:11px;color:var(--c-text-dim);margin-bottom:4px">输出预览（前10行）</div>
          <div style="background:var(--c-card-bg);border:1px solid var(--c-border);border-radius:6px;padding:8px;font-family:monospace;font-size:10px;max-height:200px;overflow-y:auto">
            <div v-for="(v, i) in testResult.preview" :key="i">{{ i }}: {{ v }}</div>
          </div>
          <div v-if="testResult.rows" style="font-size:10px;color:var(--c-text-faint);margin-top:4px">共 {{ testResult.rows }} 行</div>
        </div>
      </div>
    </n-space>
  </n-modal>

  <!-- AI 生成弹窗 -->
  <n-modal v-model:show="showAiPrompt" preset="card" title="🤖 AI 生成函数代码" style="width:520px;max-width:92vw">
    <n-space vertical>
      <div style="font-size:12px;color:var(--c-text-dim)">描述你想要的函数功能，AI 会根据编码规则和已有函数自动生成代码。</div>
      <n-input v-model:value="aiRequirement" type="textarea" placeholder="例如：计算N日价格变化率，即 (今日收盘 - N日前收盘) / N日前收盘" :rows="4" />
      <div v-if="aiResult" style="margin-top:8px">
        <div style="font-size:11px;font-weight:600;color:var(--c-text-dim);margin-bottom:4px">生成结果</div>
        <div style="background:var(--c-card-bg);border:1px solid var(--c-border);border-radius:6px;padding:8px;font-family:monospace;font-size:11px;white-space:pre-wrap;max-height:200px;overflow-y:auto">{{ aiResult }}</div>
        <n-button size="small" type="primary" style="margin-top:8px" @click="applyAiResult">✅ 填入编辑器</n-button>
      </div>
    </n-space>
    <template #footer>
      <n-space justify="flex-end">
        <n-button @click="showAiPrompt = false">取消</n-button>
        <n-button type="primary" @click="callAiGenerate" :loading="aiLoading">生成</n-button>
      </n-space>
    </template>
  </n-modal>

  <!-- Detail Modal -->
  <n-modal v-model:show="showDetail" preset="card" :title="detailItem?.name" style="width:560px;max-width:92vw">
    <div v-if="detailItem" style="font-size:13px">
      <table style="width:100%;border-collapse:collapse">
        <tr><td style="width:60px;padding:3px 8px 3px 0;color:var(--c-text-faint);text-align:right;vertical-align:top">中文名</td><td style="padding:3px 0;color:var(--c-text)">{{ detailItem.display_name }}</td></tr>
        <tr><td style="padding:3px 8px 3px 0;color:var(--c-text-faint);text-align:right;vertical-align:top">描述</td><td style="padding:3px 0;color:var(--c-text)">{{ detailItem.description || '—' }}</td></tr>
        <tr><td style="padding:3px 8px 3px 0;color:var(--c-text-faint);text-align:right">分类</td><td style="padding:3px 0;color:var(--c-text)">{{ detailItem.category }}</td></tr>
        <tr><td style="padding:3px 8px 3px 0;color:var(--c-text-faint);text-align:right">状态</td><td style="padding:3px 0;color:var(--c-text)">{{ detailItem.status_text }}</td></tr>
      </table>

      <!-- 函数定义（语法高亮） -->
      <div v-if="detailItem.source_code" style="margin-top:10px">
        <div style="font-size:11px;color:var(--c-text-faint);margin-bottom:4px">函数定义</div>
        <div ref="codeBlock" style="background:#1e1e1e;color:#d4d4d4;border-radius:6px;padding:10px;font-family:'SF Mono',Monaco,Menlo,monospace;font-size:11px;line-height:1.5;white-space:pre;overflow-x:auto;max-height:240px;overflow-y:auto">{{ detailItem.source_code }}</div>
      </div>

      <!-- 入参表格 -->
      <div style="margin-top:12px">
        <div style="font-size:11px;color:var(--c-text-faint);margin-bottom:4px">入参</div>
        <table v-if="detailItem.parameters?.length" style="width:100%;border-collapse:collapse;font-size:11px">
          <thead>
            <tr style="color:var(--c-text-dim);text-align:left;border-bottom:1px solid var(--c-border)">
              <th style="padding:3px 6px">名称</th>
              <th style="padding:3px 6px">类型</th>
              <th style="padding:3px 6px">默认值</th>
              <th style="padding:3px 6px">必填</th>
              <th style="padding:3px 6px">说明</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(p,i) in detailItem.parameters" :key="i" style="border-bottom:1px solid var(--c-border-light)">
              <td style="padding:3px 6px;font-weight:600;color:var(--c-text)">{{ p.name }}</td>
              <td style="padding:3px 6px;color:var(--c-text-dim)">{{ p.type }}</td>
              <td style="padding:3px 6px;color:var(--c-text-dim)">{{ p.default !== undefined && p.default !== '' ? p.default : '—' }}</td>
              <td style="padding:3px 6px;color:var(--c-text-dim)">{{ p.default !== undefined && p.default !== '' ? '否' : '是' }}</td>
              <td style="padding:3px 6px;color:var(--c-text-dim)">{{ p.desc || '—' }}</td>
            </tr>
          </tbody>
        </table>
        <div v-else style="color:var(--c-text-dim);font-size:11px">无</div>
      </div>

      <!-- 出参表格 -->
      <div style="margin-top:12px">
        <div style="font-size:11px;color:var(--c-text-faint);margin-bottom:4px">出参</div>
        <table style="width:100%;border-collapse:collapse;font-size:11px">
          <thead>
            <tr style="color:var(--c-text-dim);text-align:left;border-bottom:1px solid var(--c-border)">
              <th style="padding:3px 6px;width:60px">类型</th>
              <th style="padding:3px 6px">说明</th>
            </tr>
          </thead>
          <tbody>
            <tr style="border-bottom:1px solid var(--c-border-light)">
              <td style="padding:3px 6px;font-weight:600;color:var(--c-text)">{{ outputType }}</td>
              <td style="padding:3px 6px;color:var(--c-text-dim)">{{ outputDesc }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
    <template #footer>
      <n-button size="small" @click="openVersionsFromDetail">📋 版本历史</n-button>
    </template>
  </n-modal>
</div>
</template>

<script setup>
import { ref, computed, onMounted, h, nextTick } from 'vue'
import { NButton, NDataTable, NModal, NSpace, NInput, NSelect, NTag, NEmpty, NPagination } from 'naive-ui'
import MonacoEditor from './MonacoEditor.vue'
import axios from 'axios'

const API = window.location.origin
const loading = ref(false)
const items = ref([])
const total = ref(0)
const page = ref(1)
const PAGE_SIZE = 20
const filterCategory = ref(null)
const filterStatus = ref(null)
const searchText = ref('')
const expandedKeys = ref([])

const showCreate = ref(false)
const showDetail = ref(false)
const detailItem = ref(null)
const creating = ref(false)

// 推断出参类型
const outputType = computed(() => {
  const cat = detailItem.value?.category
  if (cat === 'cross_sectional') return '标量(scalar)'
  return '序列(series)'
})
const outputDesc = computed(() => {
  const cat = detailItem.value?.category
  if (cat === 'cross_sectional') return '单个统计值，如均值、排名'
  return '与输入等长的一维数组'
})
const showAiPrompt = ref(false)
const aiRequirement = ref('')
const aiResult = ref('')
const aiLoading = ref(false)
const showTestRun = ref(false)
const showVersions = ref(false)
const versions = ref([])
const testRunning = ref(false)
const publishing = ref(false)
const testResult = ref(null)
const editId = ref(null)
const isBuiltin = ref(false)
const form = ref({ name:'', display_name:'', description:'', category:'other', source_code:'' })
const formParams = ref([])
const paramTypeOpts = [
  { label:'标量(整数)', value:'scalar' },
  { label:'标量(小数)', value:'scalar' },
  { label:'标量(百分比)', value:'scalar' },
  { label:'序列(一维数组)', value:'series' },
  { label:'矩阵(二维表)', value:'matrix' },
  { label:'字符串', value:'string' },
]

const catOptions = [
  { label:'全部分类', value:'all' },
  { label:'时间序列', value:'time_series' },
  { label:'横截面', value:'cross_sectional' },
  { label:'统计函数', value:'statistical' },
  { label:'其他', value:'other' },
]
const catOpts = catOptions.filter(c => c.value !== 'all')
const statusOptions = [
  { label:'全部状态', value:'all' },
  { label:'草稿', value:'draft' },
  { label:'已发布', value:'published' },
  { label:'已弃用', value:'deprecated' },
]

const statusMap = { draft:'草稿', published:'已发布', deprecated:'已弃用' }

const columns = [
  { title:'名称', key:'name', width:120 },
  { title:'中文名', key:'display_name', width:100 },
  { title:'分类', key:'category', width:80 },
  { title:'状态', key:'status_text', width:60 },
  {
    title:'', key:'actions', width:80,
    render(r) {
      if (r.is_builtin) return ''
      return h('div', { style:'display:flex;gap:8px;align-items:center' }, [
        h('span', { 
          style:'cursor:pointer;color:#2080f0;font-size:14px',
          onClick: (e) => { e.stopPropagation(); openEdit(r) }
        }, '✎'),
        h('span', {
          style:'cursor:pointer;color:#ef4444;font-size:14px',
          onClick: (e) => { e.stopPropagation(); confirmDel(r) }
        }, '✕'),
      ])
    }
  },
]

function rowProps(row) {
  return {
    style: 'cursor:pointer',
    onClick: async () => {
      try {
        const r = await axios.get(API + '/api/functions/' + row.id)
        detailItem.value = { ...row, ...r.data }
      } catch(e) { detailItem.value = row }
      showDetail.value = true
    }
  }
}

function onExpand(keys) { expandedKeys.value = keys }

const totalPages = computed(() => Math.max(1, Math.ceil(total.value / PAGE_SIZE)))

async function loadData() {
  loading.value = true
  try {
    const params = { page: page.value, page_size: PAGE_SIZE }
    if (filterCategory.value && filterCategory.value !== 'all') params.category = filterCategory.value
    if (filterStatus.value && filterStatus.value !== 'all') params.status = filterStatus.value
    if (searchText.value) params.search = searchText.value
    const r = await axios.get(API + '/api/functions', { params })
    items.value = r.data.items.map(i => {
      // 构建用法摘要
      const params = (typeof i.parameters === 'string' ? JSON.parse(i.parameters) : i.parameters) || []
      const usage = params.length
        ? `${i.name}(${params.map(p => p.name + (p.default !== undefined ? '=' + p.default : '')).join(', ')})`
        : `${i.name}()`
      return {
        ...i,
        name: (i.is_builtin ? '🔧 ' : '') + i.name,
        usage: usage + (params.length ? ' — ' + params.map(p => p.desc || p.name).join('; ') : ''),
        status_text: statusMap[i.status] || i.status,
        parameters: params,
      }
    })
    total.value = r.data.total
  } catch(e) {} finally { loading.value = false }
}

function parseParams() {
  const code = form.value.source_code
  const m = code.match(/def\s+\w+\s*\((.*?)\)/)
  if (!m) { formParams.value = []; return }
  const args = m[1].split(',').map(s => s.trim()).filter(s => s && s !== 'df')
  formParams.value = args.map(a => {
    const parts = a.split('=')
    return {
      name: parts[0].trim(),
      type: 'numeric',
      default: parts.length > 1 ? parts[1].trim() : '',
      desc: '',
    }
  })
}

async function openEdit(row) {
  editId.value = row.id
  isBuiltin.value = row.is_builtin
  try {
    const r = await axios.get(API + '/api/functions/' + row.id)
    const d = r.data
    form.value = {
      name: d.name,
      display_name: d.display_name || '',
      description: d.description || '',
      category: d.category || 'other',
      source_code: d.source_code || '',
    }
    formParams.value = (d.parameters || []).map(p => ({
      name: p.name || '',
      type: p.type || 'numeric',
      default: p.default !== undefined ? String(p.default) : '',
      desc: p.desc || '',
    }))
  } catch(e) {
    form.value.source_code = row.source_code || ''
  }
  showCreate.value = true
}

function openCreate() {
  editId.value = null
  isBuiltin.value = false
  form.value = { name:'', display_name:'', description:'', category:'other', source_code:'' }
  formParams.value = []
  showCreate.value = true
}

async function doCreate() {
  if (!form.value.name.trim() || !form.value.display_name.trim() || !form.value.source_code.trim()) {
    alert('函数名、中文名、函数体均为必填')
    return
  }
  creating.value = true
  try {
    await axios.post(API + '/api/functions', {
      ...form.value,
      parameters: formParams.value,
    })
    showCreate.value = false
    await loadData()
  } catch(e) {
    alert(e.response?.data?.detail || '创建失败')
  } finally { creating.value = false }
}

async function doUpdate() {
  if (!editId.value) return
  creating.value = true
  try {
    await axios.put(API + '/api/functions/' + editId.value, {
      ...form.value,
      parameters: formParams.value,
    })
    showCreate.value = false
    await loadData()
  } catch(e) {
    alert(e.response?.data?.detail || '保存失败')
  } finally { creating.value = false }
}

const CODING_RULES = `【函数签名规则】
- 第一个参数必须是 df（接收 Pandas Series 或 DataFrame）
- 所有参数名小写字母开头、仅含字母数字下划线
- 用默认值声明可选参数，如 def func(df, window=5)

【代码安全规则】
- 禁止: import, exec, eval, open, os.system, subprocess, __import__
- AST 静态扫描拦截危险代码

【性能约束】
- 必须用 Pandas/NumPy 向量化操作
- 禁止 for 循环逐行处理
- 返回值长度必须等于输入长度
- 沙箱执行超时 2s`

async function callAiGenerate() {
  if (!aiRequirement.value.trim()) return
  aiLoading.value = true
  aiResult.value = ''
  try {
    const existingFuncs = items.value.map(i => 
      `${i.name}(${(i.parameters||[]).map(p=>p.name+(p.default!==undefined?'='+p.default:'')).join(',')}): ${i.description||i.display_name||''}`
    ).join('\n')

    const prompt = `${CODING_RULES}

【已有函数列表】
${existingFuncs}

【用户需求】
${aiRequirement.value}

请根据以上编码规则和已有函数，生成一个新的 Python 函数。只返回函数代码，不要解释。`

    const r = await axios.post(API + '/api/functions/ai-chat', {
      messages: [{ role: 'user', content: prompt }],
    })
    const content = r.data?.content?.content || r.data?.content || r.data?.message || ''
    // 提取代码块
    const codeMatch = content.match(/```(?:python)?\s*\n?([\s\S]*?)\n?```/)
    const code = codeMatch ? codeMatch[1].trim() : content.trim()
    aiResult.value = code
  } catch(e) {
    aiResult.value = '# 生成失败: ' + (e.response?.data?.detail || e.message)
  } finally {
    aiLoading.value = false
  }
}

function applyAiResult() {
  form.value.source_code = aiResult.value
  parseParams()
  showAiPrompt.value = false
  aiRequirement.value = ''
  aiResult.value = ''
}

async function doTestRun() {
  if (!form.value.source_code.trim()) return
  testRunning.value = true
  testResult.value = null
  try {
    const endpoint = editId.value
      ? API + '/api/functions/' + editId.value + '/test-run'
      : API + '/api/functions/test-run-temp'
    const body = editId.value
      ? { test_data: {} }
      : { source_code: form.value.source_code, parameters: formParams.value, category: form.value.category }
    const r = await axios.post(endpoint, body)
    testResult.value = r.data
  } catch(e) {
    testResult.value = { error: e.response?.data?.detail || '执行失败' }
  } finally { testRunning.value = false }
}

async function publishFunc() {
  if (!editId.value) return
  publishing.value = true
  try {
    // 先试运行
    const tr = await axios.post(API + '/api/functions/' + editId.value + '/test-run', { test_data: {} })
    if (tr.data.status === 'failed' && tr.data.elapsed_ms > 2000) {
      alert('函数性能不达标（>2000ms），无法发布')
      return
    }
    // 发布
    await axios.put(API + '/api/functions/' + editId.value, { status: 'published' })
    alert('发布成功！')
    showTestRun.value = false
    await loadData()
  } catch(e) {
    alert(e.response?.data?.detail || '发布失败')
  } finally { publishing.value = false }
}

async function confirmDel(row) {
  if (!confirm(`确定删除函数「${row.name}」？`)) return
  try {
    await axios.delete(API + '/api/functions/' + row.id)
    await loadData()
  } catch(e) {
    alert(e.response?.data?.detail || '删除失败')
  }
}

const verCols = [
  { title:'版本', key:'version', width:60 },
  { title:'时间', key:'created_at', width:150 },
  { title:'操作', key:'actions', width:60, render(r) {
    return h('span', { style:'cursor:pointer;color:#2080f0', onClick:() => doRollback(r.version) }, '回滚')
  }},
]

function verRowProps(row) {
  return { style: 'cursor:default' }
}

async function openVersionsFromDetail() {
  const d = detailItem.value
  if (!d) return
  showDetail.value = false
  await nextTick()
  openVersions(d)
}

async function openVersions(row) {
  showVersions.value = true
  try {
    const r = await axios.get(API + '/api/functions/' + row.id + '/versions')
    versions.value = r.data.versions || []
  } catch(e) { versions.value = [] }
}

async function doRollback(v) {
  if (!confirm(`确定回滚到版本 v${v}？当前代码将被覆盖。`)) return
  try {
    await axios.post(API + '/api/functions/' + detailItem.value.id + '/rollback/' + v)
    alert('回滚成功，请刷新查看')
    showVersions.value = false
    await loadData()
  } catch(e) {
    alert(e.response?.data?.detail || '回滚失败')
  }
}

onMounted(loadData)
</script>