<template>
<div>
  <n-space align="center" :wrap="false" style="margin-bottom:8px;overflow-x:auto;scrollbar-width:none;flex-wrap:nowrap">
    <n-button size="tiny" @click="$emit('back')"><AppIcon name="arrow-left" :size="13" /> 返回</n-button>
    <StockSuggestInput v-model:value="code" size="tiny" width="170px" @select="load" @enter="load" />
    <n-button type="primary" size="tiny" @click="load">查询</n-button>
    <n-select v-model:value="adj" @update:value="reloadChart" size="tiny" style="width:105px" :options="adjOptions" />
    <n-button-group size="tiny">
      <n-button v-for="p in PERIODS" :key="p.v" size="tiny" :type="period===p.v?'primary':'default'" @click="switchPeriod(p.v)">{{ p.t }}</n-button>
    </n-button-group>
    <n-button-group size="tiny">
      <n-button v-for="r in RANGES" :key="r.d" size="tiny" :type="range===r.d?'primary':'default'" @click="switchRange(r.d)">{{ r.t }}</n-button>
    </n-button-group>
    <n-button-group size="tiny">
      <n-button size="tiny" :disabled="!prevCode" @click="jump(prevCode)"><AppIcon name="arrow-left" :size="13" /> 上一只</n-button>
      <n-button size="tiny" :disabled="!nextCode" @click="jump(nextCode)">下一只 <AppIcon name="arrow-right" :size="13" /></n-button>
    </n-button-group>
  </n-space>

  <n-spin v-if="loading" />
  <n-empty v-else-if="notFound" description="未找到该证券，请检查代码" style="padding:40px" />
  <template v-else-if="detail">
    <!-- 分组状态：持仓为 portfolio 固有状态（不可移除）；自选/动态组 hover 出 ✕ 点击确认移除；末尾 + 加组 -->
    <div style="display:flex;align-items:center;gap:6px;flex-wrap:wrap;margin-bottom:8px">
      <span style="font-size:11px;color:var(--c-text-dim)">分组</span>
      <n-tag v-if="stockGroups.in_portfolio" size="small" type="info" :bordered="false">持仓</n-tag>
      <n-tag v-for="g in stockGroups.groups" :key="g.id" size="small" closable :bordered="false"
        class="dt-grp-tag" :type="g.is_default ? 'warning' : 'default'"
        :title="g.added_at ? '加入于 ' + g.added_at : ''"
        @close="confirmRemoveGroup(g)">{{ g.name }}</n-tag>
      <n-button size="tiny" dashed @click="openAddGroup"><AppIcon name="plus" :size="12" />  分组</n-button>
    </div>

    <!-- Summary Cards - unified stat-row style -->
    <StatStrip :items="summaryItems" />

    <!-- Strategy Signals -->
    <div style="margin-bottom:12px;min-height:50px">
      <h4 style="margin-bottom:6px;font-size:14px;color:var(--c-text)"><AppIcon name="target" :size="13" />  策略信号 ({{detail.latest_signal_date||detail.latest_trade_date}})</h4>
      <div v-if="detail.latest_signals&&detail.latest_signals.length" style="display:flex;gap:8px;flex-wrap:wrap">
        <div v-for="s in detail.latest_signals" :key="s.strategy_name" style="flex:1;min-width:200px;border-radius:8px;padding:10px 12px;background:var(--c-card-bg-hover);border:1px solid var(--c-border)">
          <div style="display:flex;align-items:center;gap:6px;margin-bottom:4px">
            <n-tag :type="s.direction==='buy'?'error':s.direction==='sell'?'success':'default'" size="small" :bordered="false">{{dirName(s.direction)}}</n-tag>
            <span v-if="s.combined_signal" style="color:var(--n-color-target);font-size:13px;font-weight:600">{{s.strength}}</span>
            <span v-else style="font-size:12px">{{''.repeat(s.strength)}}</span>
          </div>
          <div style="font-size:12px;line-height:1.5;color:var(--c-text)">{{s.reason}}</div>
          <div v-if="s.model_version" style="font-size:10px;color:var(--c-text-faint);margin-top:2px"><AppIcon name="f" :size="13" />  {{s.model_version}}</div>
        </div>
      </div>
      <n-empty v-else description="暂无信号" style="padding:10px" />
    </div>

    <!-- Left-Right Layout -->
    <div style="display:flex;gap:12px;flex-wrap:wrap">
      <div style="flex:1;min-width:320px">
        <div style="margin-bottom:12px">
          <h4 style="margin-bottom:4px;font-size:14px;color:var(--c-text)"><AppIcon name="trending-up" :size="13" />  K线图 <span style="font-size:11px;color:var(--c-text-dim)">{{dateRange}}</span></h4>
          <div :id="'c1'" style="width:100%;height:340px"></div>
        </div>
        <div style="margin-bottom:12px">
          <h4 style="margin-bottom:4px;font-size:14px;color:var(--c-text)"><AppIcon name="trending-up" :size="13" />  均线 (MA5/10/20/30/60/120/180)</h4>
          <div :id="'c6'" style="width:100%;height:180px"></div>
        </div>
        <div style="margin-bottom:12px">
          <h4 style="margin-bottom:4px;font-size:14px;color:var(--c-text)"><AppIcon name="bar-chart-2" :size="13" />  成交量</h4>
          <div :id="'c2'" style="width:100%;height:160px"></div>
        </div>
        <div style="margin-bottom:12px">
          <h4 style="margin-bottom:4px;font-size:14px;color:var(--c-text)"><AppIcon name="s" :size="13" />  MACD</h4>
          <div :id="'c3'" style="width:100%;height:160px"></div>
        </div>
        <div style="margin-bottom:12px">
          <h4 style="margin-bottom:4px;font-size:14px;color:var(--c-text)"> RSI</h4>
          <div :id="'c4'" style="width:100%;height:160px"></div>
        </div>
        <div v-if="benchData.length" style="margin-bottom:12px">
          <h4 style="margin-bottom:4px;font-size:14px;color:var(--c-text)"> 相对强弱 <span style="font-size:11px;color:var(--c-text-dim)">个股 vs 沪深300（窗口起点=1）</span></h4>
          <div :id="'c8'" style="width:100%;height:160px"></div>
        </div>
        <div style="margin-bottom:12px">
          <h4 style="margin-bottom:4px;font-size:14px;color:var(--c-text)"> 换手率 <span style="font-size:11px;color:var(--c-text-dim)">%（含分位线）</span></h4>
          <div :id="'c9'" style="width:100%;height:140px"></div>
        </div>
        <div v-if="marginData.length > 2" style="margin-bottom:12px">
          <h4 style="margin-bottom:4px;font-size:14px;color:var(--c-text)"> 两融余额 <span style="font-size:11px;color:var(--c-text-dim)">融资 / 合计（亿元）</span></h4>
          <div :id="'c10'" style="width:100%;height:140px"></div>
        </div>
        <!-- 带右侧 Y 轴的图统一殿后（PE分位%/资金流累计/ATR%），避免中段双轴观感错乱 -->
        <div style="margin-bottom:12px">
          <h4 style="margin-bottom:4px;font-size:14px;color:var(--c-text)"><AppIcon name="bar-chart-2" :size="13" />  PE历史分位 <span style="font-size:11px;color:var(--c-text-dim)">{{peRange}}</span></h4>
          <div :id="'c5'" style="width:100%;height:160px"></div>
        </div>
        <div v-if="mfData.length" style="margin-bottom:12px">
          <h4 style="margin-bottom:4px;font-size:14px;color:var(--c-text)"> 资金流 <span style="font-size:11px;color:var(--c-text-dim)">主力净流入(超大+大单, 万元) / 累计</span></h4>
          <div :id="'c7'" style="width:100%;height:160px"></div>
        </div>
        <div style="margin-bottom:12px">
          <h4 style="margin-bottom:4px;font-size:14px;color:var(--c-text)"> ATR <span style="font-size:11px;color:var(--c-text-dim)">14日真实波幅 / 占价比</span></h4>
          <div :id="'c11'" style="width:100%;height:140px"></div>
        </div>
      </div>

      <!-- Right: Fundamentals & Overview -->
      <div style="width:100%;max-width:320px;display:flex;flex-direction:column;gap:12px" class="detail-sidebar">
        <div>
          <h4 style="margin-bottom:6px;font-size:14px;color:var(--c-text)"> 基本面</h4>
          <!-- v-if 判断对象存在即可：industry 可能为空，不能因此隐藏整块 -->
          <table v-if="detail.fundamentals!=null&&Object.keys(detail.fundamentals).length" style="width:100%;border-collapse:collapse;font-size:12px">
            <tr v-for="row in fundRows" :key="row.lbl" style="background:var(--c-card-bg)">
              <td style="width:85px;white-space:nowrap;padding:4px 6px;border:1px solid var(--c-border);color:var(--c-text-dim);font-size:11px;text-align:left">{{row.lbl}}</td>
              <td style="padding:4px 6px;border:1px solid var(--c-border);color:var(--c-text);text-align:left">{{row.val}}</td>
            </tr>
          </table>
          <n-empty v-else-if="!loading" description="暂无基本面数据" style="padding:10px" />
        </div>
        <div>
          <h4 style="margin-bottom:6px;font-size:14px;color:var(--c-text)"><AppIcon name="bar-chart-2" :size="13" />  行情概览</h4>
          <table style="width:100%;border-collapse:collapse;font-size:12px">
            <tr><td style="width:85px;white-space:nowrap;padding:5px 8px;border:1px solid var(--c-border);color:var(--c-text-dim);font-size:11px">日期</td><td style="padding:5px 8px;border:1px solid var(--c-border);color:var(--c-text)" :style="{background:hoverInfo?'rgba(32,128,240,0.06)':'transparent'}">{{hoverInfo?hoverInfo.date:detail.latest_trade_date}}</td></tr>
            <tr><td style="width:85px;white-space:nowrap;padding:5px 8px;border:1px solid var(--c-border);color:var(--c-text-dim);font-size:11px">开盘</td><td style="padding:5px 8px;border:1px solid var(--c-border);color:var(--c-text)">¥{{hoverInfo?hoverInfo.open.toFixed(2):(detail.open||0).toFixed(2)}}</td></tr>
            <tr><td style="width:85px;white-space:nowrap;padding:5px 8px;border:1px solid var(--c-border);color:var(--c-text-dim);font-size:11px">最高</td><td style="padding:5px 8px;border:1px solid var(--c-border)" :style="{color:hoverInfo?'#ef4444':'var(--c-text)'}">¥{{hoverInfo?hoverInfo.high.toFixed(2):(detail.high||0).toFixed(2)}}</td></tr>
            <tr><td style="width:85px;white-space:nowrap;padding:5px 8px;border:1px solid var(--c-border);color:var(--c-text-dim);font-size:11px">最低</td><td style="padding:5px 8px;border:1px solid var(--c-border)" :style="{color:hoverInfo?'#10b981':'var(--c-text)'}">¥{{hoverInfo?hoverInfo.low.toFixed(2):(detail.low||0).toFixed(2)}}</td></tr>
            <tr><td style="width:85px;white-space:nowrap;padding:5px 8px;border:1px solid var(--c-border);color:var(--c-text-dim);font-size:11px">收盘</td><td style="padding:5px 8px;border:1px solid var(--c-border)"><span style="font-weight:600" :style="{color:hoverInfo?(hoverInfo.close>=hoverInfo.prevClose?'#ef4444':'#10b981'):priceColor}">¥{{hoverInfo?hoverInfo.close.toFixed(2):(detail.close||0).toFixed(2)}}</span><span v-if="hoverInfo&&hoverInfo.prevClose" style="font-size:10px;margin-left:4px" :style="{color:hoverInfo.close>=hoverInfo.prevClose?'#ef4444':'#10b981'}">{{((hoverInfo.close-hoverInfo.prevClose)/hoverInfo.prevClose*100).toFixed(2)}}%</span><span v-else-if="priceChg!=null" style="font-size:10px;margin-left:4px" :style="{color:priceColor}">{{priceChg>=0?'+':''}}{{priceChg.toFixed(2)}}%</span></td></tr>
            <tr><td style="width:85px;white-space:nowrap;padding:5px 8px;border:1px solid var(--c-border);color:var(--c-text-dim);font-size:11px">成交量</td><td style="padding:5px 8px;border:1px solid var(--c-border);color:var(--c-text)">{{hoverInfo?fmt(hoverInfo.volume)+'股':fmt(detail.volume)+'股'}}</td></tr>
            <tr><td style="width:85px;white-space:nowrap;padding:5px 8px;border:1px solid var(--c-border);color:var(--c-text-dim);font-size:11px">成交额</td><td style="padding:5px 8px;border:1px solid var(--c-border);color:var(--c-text)">{{hoverInfo&&hoverInfo.amount?fmt(hoverInfo.amount)+'元':(detail.amount?fmt(detail.amount)+'元':'-')}}</td></tr>
            <tr><td style="width:85px;white-space:nowrap;padding:5px 8px;border:1px solid var(--c-border);color:var(--c-text-dim);font-size:11px">换手率</td><td style="padding:5px 8px;border:1px solid var(--c-border);color:var(--c-text)">{{(hoverInfo&&hoverInfo.turnover!=null?hoverInfo.turnover:(detail.turnover!=null?detail.turnover:null))!=null ? (hoverInfo&&hoverInfo.turnover!=null?hoverInfo.turnover:detail.turnover).toFixed(2)+'%':'—'}}</td></tr>
          </table>
        </div>
        <div v-if="chipData && chipData.buckets && chipData.buckets.length">
          <h4 style="margin-bottom:6px;font-size:14px;color:var(--c-text)"> 筹码分布 <span style="font-size:11px;color:var(--c-text-dim)">{{chipData.decay?'换手衰减':'全量'}},现价口径<template v-if="chipData.date_from"> · {{chipData.date_from}}~{{chipData.date_to}}</template></span></h4>
          <div :id="'c12'" style="width:100%;height:280px"></div>
          <div v-if="chipData.profit_ratio!=null" style="font-size:11px;color:var(--c-text-dim);margin-top:2px">
            现价 ¥{{chipData.current}} · 获利盘 <span :style="{color:chipData.profit_ratio>=0.5?'#10b981':'#ef4444',fontWeight:600}">{{(chipData.profit_ratio*100).toFixed(1)}}%</span>
          </div>
        </div>
        <div v-if="sigStats">
          <h4 style="margin-bottom:6px;font-size:14px;color:var(--c-text)"> 信号回看</h4>
          <div style="font-size:11px;line-height:1.7;color:var(--c-text-dim)">
            近{{sigStats.years}}年 <b style="color:var(--c-text)">{{sigStats.n}}</b> 条信号（buy {{sigStats.buys}}/sell {{sigStats.sells}}）<br/>
            平均前瞻：5d <span :style="{color:sigStats.f5>=0?'#ef4444':'#10b981'}">{{(sigStats.f5*100).toFixed(1)}}%</span> ·
            10d <span :style="{color:sigStats.f10>=0?'#ef4444':'#10b981'}">{{(sigStats.f10*100).toFixed(1)}}%</span> ·
            20d <span :style="{color:sigStats.f20>=0?'#ef4444':'#10b981'}">{{(sigStats.f20*100).toFixed(1)}}%</span>
          </div>
        </div>
      </div>
    </div>
  </template>

  <!-- 加入分组弹窗：自选+动态组多选，支持就地新建分组并勾选 -->
  <n-modal v-model:show="showAddGroup">
    <n-card style="width:400px;max-width:92vw" title="加入分组" role="dialog" aria-modal="true">
      <n-space vertical size="small">
        <div style="font-size:11px;color:var(--c-text-dim)">自选与自定义分组可多选；持仓请到持仓页维护</div>
        <n-checkbox-group v-model:value="pickedGroups">
          <n-space size="small">
            <n-checkbox v-for="g in allGroups" :key="g.id" :value="g.id" :label="g.name" />
          </n-space>
        </n-checkbox-group>
        <div style="display:flex;gap:6px;align-items:center">
          <n-input v-model:value="newGroupName" size="small" placeholder="新分组名称（回车即建）" maxlength="32" style="flex:1" @keyup.enter="createGroupInline" />
          <n-button size="small" :loading="addingGroup" @click="createGroupInline">新增分组</n-button>
        </div>
      </n-space>
      <template #footer>
        <n-space justify="flex-end">
          <n-button size="small" @click="showAddGroup=false">取消</n-button>
          <n-button size="small" type="primary" :loading="savingGroups" @click="saveGroups">保存</n-button>
        </n-space>
      </template>
    </n-card>
  </n-modal>
</div>
</template>

<script setup>
import AppIcon from './AppIcon.vue'
import StockSuggestInput from './StockSuggestInput.vue'
import { ref, computed, watch, onMounted, onUnmounted, nextTick } from 'vue'
import {NCard, NButton, NInput, NSpace, NSpin, NTag, NEmpty, NDescriptions, NDescriptionsItem, NSelect, NButtonGroup, NModal, NCheckboxGroup, NCheckbox, useDialog, useMessage} from 'naive-ui'
import axios from 'axios'
import * as echarts from 'echarts'
import { useNavStore } from '../stores/nav'
import { useAuthStore } from '../stores/auth'
import StatStrip from './StatStrip.vue'

const props = defineProps({ code: String })
const emit = defineEmits(['back'])
const API = window.location.origin
const _nav = useNavStore()
const dialog = useDialog()
const message = useMessage()
const authStore = useAuthStore()
const _authH = () => authStore.token ? {Authorization: 'Bearer '+authStore.token} : {}
const code = ref(props.code||'')
const loading = ref(false)
const detail = ref(null)
const notFound = ref(false)
const hcnt = ref(0)

// ── 分组状态（持仓 / 自选 / 动态组）──
const stockGroups = ref({ groups: [], in_portfolio: false })
const allGroups = ref([])          // 加组弹窗里的全量分组
const pickedGroups = ref([])       // 弹窗勾选
const showAddGroup = ref(false), newGroupName = ref(''), addingGroup = ref(false), savingGroups = ref(false)

async function loadStockGroups(){
  try{
    const r = await axios.get(API + '/api/watch/stock-groups?code=' + code.value)
    stockGroups.value = r.data
  }catch(e){ stockGroups.value = { groups: [], in_portfolio: false } }
}
function confirmRemoveGroup(g){
  dialog.warning({
    title: '移除分组',
    content: `是否将该股从「${g.name}」移除？`,
    positiveText: '移除', negativeText: '取消',
    onPositiveClick: async () => {
      const ids = stockGroups.value.groups.map(x => x.id).filter(id => id !== g.id)
      try{
        await axios.put(API + '/api/watch/stock-groups',
          { stock_code: code.value, group_ids: ids }, { headers: _authH() })
        await loadStockGroups()
        message.success(`已移出「${g.name}」`)
      }catch(e){ message.error(e.response?.data?.detail || '移除失败') }
    }
  })
}
async function openAddGroup(){
  showAddGroup.value = true
  newGroupName.value = ''
  pickedGroups.value = stockGroups.value.groups.map(g => g.id)
  try{
    const r = await axios.get(API + '/api/watch/groups')
    allGroups.value = r.data.groups || []
  }catch(e){ allGroups.value = [] }
}
async function createGroupInline(){
  const name = newGroupName.value.trim()
  if (!name) return
  addingGroup.value = true
  try{
    const r = await axios.post(API + '/api/watch/groups', { name }, { headers: _authH() })
    allGroups.value.push({ id: r.data.id, name, is_default: false, count: 0 })
    pickedGroups.value.push(r.data.id)
    newGroupName.value = ''
    message.success(`分组「${name}」已创建并勾选`)
  }catch(e){ message.error(e.response?.data?.detail || '创建失败') }
  addingGroup.value = false
}
async function saveGroups(){
  savingGroups.value = true
  try{
    await axios.put(API + '/api/watch/stock-groups',
      { stock_code: code.value, group_ids: pickedGroups.value }, { headers: _authH() })
    showAddGroup.value = false
    await loadStockGroups()
    message.success('分组已更新')
  }catch(e){ message.error(e.response?.data?.detail || '保存失败') }
  savingGroups.value = false
}
// 复权记忆：记住用户上次选择
const adj = ref(localStorage.getItem('detail_adj') || 'none')
// K 线周期与区间（方案1：区间快捷选择 + 日/周/月切换；days 上限 7000 ≈ 全部历史）
const PERIODS = [{v:'day',t:'日'},{v:'week',t:'周'},{v:'month',t:'月'}]
const RANGES = [{t:'1月',d:30},{t:'1年',d:250},{t:'2年',d:500},{t:'5年',d:1250},{t:'10年',d:2500},{t:'20年',d:4900},{t:'全部',d:7000}]
const period = ref('day')
const range = ref(500)

async function loadKline() {
  const r = await axios.get(API + '/api/stock/' + code.value + '/kline?days=' + range.value + '&period=' + period.value + '&adjust=' + adj.value)
  lastKline = r.data
  await nextTick()
  drawCharts(lastKline)
}
function switchPeriod(v) { period.value = v; loadKline() }
function switchRange(d) { range.value = d; loadKline() }
let lastKline = null
const dateRange = ref('')
const peData = ref([])
const peRange = ref('')
// 上下只：从列表页跳转时写入 localStorage 的最近列表导航上下文
const prevCode = ref(null)
const nextCode = ref(null)
let navList = []
function loadNavList() {
  try { navList = JSON.parse(localStorage.getItem('detail_nav_list') || '[]') || [] } catch (e) { navList = [] }
}
function updateNav() {
  loadNavList()
  prevCode.value = null; nextCode.value = null
  if (!navList.length) return
  const i = navList.indexOf(code.value)
  if (i > 0) prevCode.value = navList[i - 1]
  if (i >= 0 && i < navList.length - 1) nextCode.value = navList[i + 1]
}
function jump(c) { if (c) { _nav.dcode = c; _nav.syncHash(); code.value = c; load() } }
// 供列表页调用：记录当前列表代码序列
window.__setDetailNavList = (codes) => { localStorage.setItem('detail_nav_list', JSON.stringify(codes || [])) }

const fundRows = computed(() => {
  const f = detail.value?.fundamentals
  if (!f || !Object.keys(f).length) return []
  const Y = v => v != null ? fmtMoney(Number(v)) : '—'
  const P = v => v != null ? Number(v).toFixed(2) : '—'
  const B = (v, d = 0) => v != null ? (v / 1e8).toFixed(d) + '亿' : '—'
  return [
    { lbl:'行业', val: f.industry || '—' },
    { lbl:'PE(TTM)', val: f.pe_ttm != null ? P(f.pe_ttm) : '亏损' },
    { lbl:'PE', val: f.pe != null ? P(f.pe) : '—' },
    { lbl:'PB', val: f.pb_mrq != null ? P(f.pb_mrq) : '—' },
    { lbl:'PS(TTM)', val: f.ps_ttm != null ? P(f.ps_ttm) : '—' },
    { lbl:'PS', val: f.ps != null ? P(f.ps) : '—' },
    { lbl:'ROE', val: f.roe != null ? f.roe.toFixed(2) + '%' : '—' },
    { lbl:'营收同比', val: f.revenue_yoy != null ? f.revenue_yoy.toFixed(1) + '%' : '—' },
    { lbl:'净利同比', val: f.profit_yoy != null ? f.profit_yoy.toFixed(1) + '%' : '—' },
    { lbl:'股息率', val: f.dv_ratio != null ? f.dv_ratio.toFixed(2) + '%' : '—' },
    { lbl:'股息TTM', val: f.dv_ttm != null ? f.dv_ttm.toFixed(2) + '%' : '—' },
    { lbl:'换手率', val: f.turnover_rate != null ? f.turnover_rate.toFixed(2) + '%' : '—' },
    { lbl:'量比', val: f.volume_ratio != null ? P(f.volume_ratio) : '—' },
    { lbl:'总市值', val: B(f.market_cap, 2) },
    { lbl:'流通市值', val: B(f.circ_mv, 2) },
    { lbl:'总股本', val: B(f.total_shares, 2) },
    { lbl:'流通股本', val: B(f.float_share, 2) },
    { lbl:'自由流通', val: B(f.free_share, 2) },
    { lbl:'注册资本', val: f.reg_capital != null ? Number(f.reg_capital).toFixed(1) + '万元' : '—' },
    { lbl:'员工', val: f.employees != null ? Y(f.employees) : '—' },
    { lbl:'主营', val: f.main_business || '—' },
  ]
})

const summaryItems = computed(() => {
  const d = detail.value
  if (!d) return []
  return [
    { label: d.stock_code, value: d.stock_name },
    { label: '最新价', value: '¥' + (d.close || 0).toFixed(2) + (priceChg.value != null ? (priceChg.value >= 0 ? '+' : '') + priceChg.value.toFixed(2) + '%' : ''), color: priceChg.value != null ? priceColor.value : undefined },
    { label: '数据日期', value: d.latest_trade_date },
    { label: '历史信号', value: hcnt.value },
  ]
})

const adjOptions = [
  {value:'none',label:'不复权'},
  {value:'qfq',label:'前复权'},
  {value:'hfq',label:'后复权'},
]
const dirName = d => ({buy:'买入',sell:'卖出',neutral:'中性'}[d]||d)
import { fmtMoney } from '../utils/ui'
const fmt = v => v!=null?fmtMoney(Number(v)):'0'
const priceChg = ref(null)
const priceColor = ref('#fff')
const hoverInfo = ref(null)  // crosshair hover 时动态更新的行情数据

// ── 详情页扩展图数据（best-effort：失败隐藏卡片不影响主图）──
const mfData = ref([])       // 资金流（万元）
const benchData = ref([])    // 沪深300 同窗 K 线（相对强弱基准）
const marginData = ref([])   // 两融余额（元）
const toplistData = ref([])  // 龙虎榜上榜记录
const chipData = ref(null)   // 筹码分布（现价口径）
const sigData = ref([])      // 模型信号史（含前瞻收益）
const sigStats = ref(null)   // 信号摘要（侧栏）
const mfCum = ref([])        // 主力净流入累计（与 mfData 同长）

// ── K线组 ↔ PE图 日历窗口联动 ──────────────────────────────────────
// PE 序列是全历史财报日（跨度远大于 K 线窗口），echarts.connect 的百分比
// 联动会把两图对到不同日历时段——所以 PE 不进 connect 组，改按「可见日期
// 区间」双向翻译：主图缩放→日期窗→PE 轴索引→派发 dataZoom（反向亦然）。
let klineDates = []        // 当前 K 线日期轴（drawCharts 每次刷新）
let klineMainChart = null  // c1：派发给它一个，connect 组全员同步
let syncingAx = false      // 派发中标志，切断 事件→派发→事件 回环
const pctIdx = (arr, pct) => Math.min(arr.length - 1, Math.max(0, Math.round((arr.length - 1) * pct / 100)))
// 有序日期数组中找 [>=d0 的首索引, <=d1 的末索引]（窗口越界向内夹）
const dateSpanIdx = (arr, d0, d1) => {
  let i0 = 0, i1 = arr.length - 1
  while (i0 < arr.length && arr[i0] < d0) i0++
  i0 = Math.min(i0, arr.length - 1)   // 窗口整体在对方数据之后时夹到末位，防 start>end
  while (i1 > i0 && arr[i1] > d1) i1--
  return [i0, i1]
}
function syncPeFromKline(){
  const peEl = document.getElementById('c5'); const c5 = peEl && peEl._echart
  if (!c5 || !klineDates.length || !peData.value.length) return
  const dz = klineMainChart?.getOption()?.dataZoom?.[0]
  if (!dz || peData.value.length < 2) return
  const [a, b] = [pctIdx(klineDates, dz.start ?? 0), pctIdx(klineDates, dz.end ?? 100)]
  const [i0, i1] = dateSpanIdx(peData.value.map(d => d.date), klineDates[a], klineDates[b])
  const n = peData.value.length
  syncingAx = true
  try { c5.dispatchAction({ type: 'dataZoom', start: i0 / (n - 1) * 100, end: i1 / (n - 1) * 100 }) } finally { setTimeout(() => { syncingAx = false }) }
}
function syncKlineFromPe(){
  const peEl = document.getElementById('c5'); const c5 = peEl && peEl._echart
  if (!c5 || !klineMainChart || !klineDates.length || !peData.value.length) return
  const dz = c5.getOption()?.dataZoom?.[0]
  if (!dz || klineDates.length < 2) return
  const peDates = peData.value.map(d => d.date)
  const [a, b] = [pctIdx(peDates, dz.start ?? 0), pctIdx(peDates, dz.end ?? 100)]
  const [i0, i1] = dateSpanIdx(klineDates, peDates[a], peDates[b])
  const n = klineDates.length
  syncingAx = true
  try { klineMainChart.dispatchAction({ type: 'dataZoom', start: i0 / (n - 1) * 100, end: i1 / (n - 1) * 100 }) } finally { setTimeout(() => { syncingAx = false }) }
}

// 实例复用：已存在则 setOption 更新（notMerge=true 全量重置 / false 按 series id 合并）
function makeChart(id, opt, notMerge = true){
  const el = document.getElementById(id)
  if(!el) return null
  let c = el._echart
  if(!c){ c = echarts.init(el); el._echart = c }
  c.setOption(opt, { notMerge })
  return c
}

function calcPriceChange(kd){
  if(!kd||!kd.kline||kd.kline.length<2) { priceChg.value=null; priceColor.value='#fff'; return }
  const kl = kd.kline
  const last = kl[kl.length-1], prev = kl[kl.length-2]
  if(last.close && prev.close){
    priceChg.value = (last.close - prev.close) / prev.close * 100
    priceColor.value = priceChg.value >= 0 ? '#ef4444' : '#10b981'
  }
}

async function load(){
  if(!code.value) return
  loading.value = true
  notFound.value = false
  detail.value = null
  hoverInfo.value = null
  updateNav()
  try{
    const [r1, r2] = await Promise.all([
      axios.get(API+'/api/stock/'+code.value+'/detail'),
      axios.get(API+'/api/stock/'+code.value+'/kline?days='+range.value+'&period='+period.value+'&adjust='+adj.value),
    ])
    detail.value = r1.data; hcnt.value = r1.data.history_count
    if(r2.data.kline) lastKline = r2.data
  }catch(e){
    if(e.response?.status===404) notFound.value = true
  } finally { loading.value = false }
  // PE data (best-effort, 失败不影响 main charts)
  await nextTick()
  drawCharts(lastKline)
  try{
    const r=await axios.get(API+'/api/stock/'+code.value+'/pe_history')
    if(r.data.data&&r.data.data.length){peData.value=r.data.data;nextTick(()=>drawPeChart())}
  }catch(e){}
  // ── 扩展图数据（全部 best-effort：空数据隐藏卡片，异常不影响主图）──
  await Promise.allSettled([
    loadStockGroups(),
    axios.get(API+'/api/stock/'+code.value+'/moneyflow?days=7000').then(r=>{
      mfData.value = r.data.data||[]
      const cum=[]; let s=0
      mfData.value.forEach(d=>{s+=d.net_main; cum.push(+s.toFixed(0))})
      mfCum.value = cum
    }),
    // 基准与 K 线同窗同周期：相对强弱两条线起点才可比
    axios.get(API+'/api/stock/000300/kline?days='+range.value+'&period='+period.value+'&adjust=none&type=index').then(r=>{
      benchData.value = r.data.kline||[]
    }),
    axios.get(API+'/api/stock/'+code.value+'/margin?days=7000').then(r=>{ marginData.value = r.data.data||[] }),
    axios.get(API+'/api/stock/'+code.value+'/top_list?days=3650').then(r=>{ toplistData.value = r.data.data||[] }),
    axios.get(API+'/api/stock/'+code.value+'/chip').then(r=>{ chipData.value = r.data }),
    axios.get(API+'/api/stock/'+code.value+'/signals?days=1095').then(r=>{ sigData.value = r.data.data||[] }),
  ])
  computeSigStats()
  await nextTick()
  drawAuxCharts(lastKline)
  drawChip()
}

// dataZoom 滑块统一低调样式：ECharts 画在 canvas 上，颜色必须字面量（CSS 变量不生效，
// 会回退默认配色反而刺眼）；填充/手柄/数据剪影全部弱化，滑块只做窗口提示不抢图表主体
const DZ_SLIDER = {
  height:16, bottom:4, handleSize:6,
  borderColor:'transparent',
  backgroundColor:'rgba(128,128,128,0.08)',
  fillerColor:'rgba(96,165,250,0.10)',
  dataBackground:{lineStyle:{color:'transparent'},areaStyle:{color:'rgba(128,128,128,0.05)'}},
  selectedDataBackground:{lineStyle:{color:'rgba(96,165,250,0.35)'},areaStyle:{color:'rgba(96,165,250,0.06)'}},
  handleStyle:{color:'rgba(148,163,184,0.45)',borderColor:'rgba(148,163,184,0.35)'},
  moveHandleStyle:{color:'rgba(148,163,184,0.2)'},
  textStyle:{color:'rgba(148,163,184,0.6)',fontSize:9},
  labelStyle:{color:'transparent'},
}

// 联动图组统一 grid：固定像素边距让 11 张图绘图区严格同宽（带右轴的图右边距留在
// 轴标签，无右轴的图留白——对齐优先于零留白）
const GRID_STD = { left:54, right:48, bottom:30 }

function drawCharts(kd){
  const dates = kd.kline.map(d=>d.trade_date)
  const closes = kd.kline.map(d=>d.close)
  const ohlc = kd.kline.map(d=>[d.open,d.close,d.low,d.high])
  const vols = kd.kline.map(d=>d.volume)
  const amts = kd.kline.map(d=>d.amount)
  const trns = kd.kline.map(d=>d.turnover)
  const bmid = kd.kline.map(d=>d.boll_mid), bup = kd.kline.map(d=>d.boll_upper), blo = kd.kline.map(d=>d.boll_lower)
  const rs = kd.kline.map(d=>d.rsi), di = kd.kline.map(d=>d.dif), de = kd.kline.map(d=>d.dea), ba = kd.kline.map(d=>d.macd_bar)
  // 均线 MA5/10/20/30/60/120/180（前端按 close 计算，前 N-1 点为 null）
  const MA = (n) => closes.map((_, i) => { if (i < n - 1) return null; let s = 0; for (let j = 0; j < n; j++) s += closes[i - j]; return +(s / n).toFixed(2); })
  const ma5 = MA(5), ma10 = MA(10), ma20 = MA(20), ma30 = MA(30), ma60 = MA(60), ma120 = MA(120), ma180 = MA(180)
  const vc = ohlc.map(d=>d[1]>=d[0]?'rgba(239,68,68,0.85)':'rgba(16,185,129,0.85)')
  const bc = ba.map(v=>v>=0?'rgba(239,68,68,0.85)':'rgba(16,185,129,0.85)')
  // 浅灰色网格线（比默认的 --c-border-light 更浅）
  const gl = {lineStyle:{color:'rgba(128,128,128,0.1)'}}

  dateRange.value = dates[0]+' ~ '+dates[dates.length-1]
  // 默认显示最近 1 年（约 250 交易日；不足则全显示）
  const totalDays = dates.length
  const SHOW = totalDays <= 250 ? 0 : ((totalDays - 250) / totalDays * 100).toFixed(1)
  const dz = [
    // 滚轮缩放时间窗（普通滚轮=以鼠标为中心缩放，Shift+滚轮=平移）；
    // 五图同组（echarts.connect），任一图滚轮缩放全组同步
    {type:'inside', xAxisIndex:0, zoomOnMouseWheel:true, moveOnMouseWheel:'shift'},
    {type:'slider', xAxisIndex:0, start:SHOW, end:100, ...DZ_SLIDER}
  ]
  const xA = {type:'category',data:dates,axisLabel:{show:false},
    axisLine:{lineStyle:{color:'rgba(128,128,128,0.15)'}},
    axisTick:{show:false}}
  const tt = {trigger:'axis',axisPointer:{type:'cross'}}

  // 实例复用：已存在则 setOption(notMerge) 更新，避免每次 dispose+init 卡顿
  function make(id, opt){
    const el = document.getElementById(id)
    if(!el) return null
    let c = el._echart
    if(!c){ c = echarts.init(el); el._echart = c }
    c.setOption(opt, { notMerge: true })
    return c
  }

  const tooltipFmt = p => {
    if(!p||!p.length) return ''
    const d = p[0]; const idx = d.dataIndex; const o = ohlc[idx]
    if(!o) return ''
    const chg = o[1] && ohlc[idx-1] ? ((o[1]-ohlc[idx-1][1])/ohlc[idx-1][1]*100).toFixed(2) : '—'
    const color = chg>=0?'#ef4444':'#10b981'
    const mid = bmid[idx], up = bup[idx], lo = blo[idx]
    // 信号/龙虎榜叠加信息（数据晚于主图到达也不怕：formatter 每次悬停现查 ref）
    const s = sigData.value.find(x=>x.date===dates[idx])
    const t = toplistData.value.find(x=>x.date===dates[idx])
    let extra = ''
    if(s) extra += `<br/><span style="color:${s.direction==='buy'?'#ef4444':'#10b981'}">${s.direction==='buy'?'▲':'▼'}模型信号 ${s.direction==='buy'?'买入':'卖出'} @¥${s.price!=null?s.price.toFixed(2):'—'}</span>`
      + (s.f10d!=null ? ` <span style="font-size:10px">前瞻10d ${(s.f10d*100).toFixed(1)}%</span>` : '')
    if(t) extra += `<br/><span style="color:#f59e0b">◆龙虎榜 ${t.reason||''}${t.net!=null?` 净买入${(t.net/1e4/1e4).toFixed(2)}亿`:''}</span>`
    return `<div style="font-size:12px"><b>${dates[idx]}</b><br/>
      开: ${o[0].toFixed(2)}  收: <span style="color:${color}">${o[1].toFixed(2)}</span> (${chg}%)<br/>
      高: ${o[3].toFixed(2)}  低: ${o[2].toFixed(2)}  量: ${(vols[idx]/1e6).toFixed(1)}M<br/>
      <span style="color:#f59e0b">BOLL上轨: ${up!=null?up.toFixed(2):'—'}</span>
      <span style="color:#60a5fa"> 中轨: ${mid!=null?mid.toFixed(2):'—'}</span>
      <span style="color:#f59e0b"> 下轨: ${lo!=null?lo.toFixed(2):'—'}</span>${extra}</div>`
  }

  const c1 = make('c1', {
    tooltip:{trigger:'axis',axisPointer:{type:'cross'},formatter:tooltipFmt},
    grid:{...GRID_STD, top:18},
    xAxis:xA, yAxis:{scale:true,splitLine:gl},
    dataZoom:dz,
    series:[
      {name:'K线',type:'candlestick',data:ohlc,itemStyle:{color:'#ef4444',color0:'#10b981',borderColor:'#ef4444',borderColor0:'#10b981'}},
      {name:'上轨',type:'line',data:bup,lineStyle:{color:'#f59e0b',width:2},symbol:'none',smooth:true},
      {name:'中轨',type:'line',data:bmid,lineStyle:{color:'#60a5fa',width:2},symbol:'none',smooth:true},
      {name:'下轨',type:'line',data:blo,lineStyle:{color:'#f59e0b',width:2},symbol:'none',smooth:true}
    ]
  })
  // 成交量分位线（P20/P50/P80）：基于**当前显示窗口**计算——回答"今天的天量/
  // 地量在这段行情里处于什么位置"。窗口随 dataZoom（滑块/滚轮/五图联动）变化
  // 时重算；分位用线性插值（对齐 numpy.percentile），零量日（停牌）剔除。
  const quantile = (arr, q) => {
    if (!arr.length) return 0
    const s = [...arr].sort((a, b) => a - b)
    const pos = (s.length - 1) * q, lo = Math.floor(pos), hi = Math.ceil(pos)
    return s[lo] + (s[hi] - s[lo]) * (pos - lo)
  }
  const volMarkLines = (i0, i1) => {
    const w = vols.slice(i0, i1 + 1).filter(v => v > 0)
    const mk = (q, name, color) => {
      const v = quantile(w, q)
      return { yAxis: v, label: { formatter: `${name} ${(v / 1e6).toFixed(1)}M`, position: 'insideEndTop', fontSize: 9, color },
               lineStyle: { color, type: 'dashed', width: 1 } }
    }
    return { silent: true, symbol: 'none', animation: false,
             data: [mk(0.2, 'P20', 'rgba(156,163,175,0.8)'), mk(0.5, 'P50', '#60a5fa'), mk(0.8, 'P80', 'rgba(156,163,175,0.8)')] }
  }
  const initI0 = Math.max(0, Math.round((totalDays - 1) * (parseFloat(SHOW) || 0) / 100))

  const c2 = make('c2', {
    tooltip:tt, grid:{...GRID_STD, top:8},
    xAxis:xA, yAxis:{axisLabel:{fontSize:9,formatter:v=>(v/1e6).toFixed(0)+'M'},splitLine:gl},
    dataZoom:dz,
    series:[{name:'量',type:'bar',data:vols,itemStyle:{color:p=>vc[p.dataIndex]},
      markLine: volMarkLines(initI0, totalDays - 1)}]
  })
  // 缩放跟随：五图 connect 联动时任何一图缩放都会改 c2 的窗口，节流后按可见区间重算
  if (c2) {
    let _vqLast = 0
    c2.on('datazoom', () => {
      const now = Date.now()
      if (now - _vqLast < 120) return
      _vqLast = now
      try {
        const d = c2.getOption().dataZoom?.[0]
        if (!d) return
        const i0 = Math.max(0, Math.round((totalDays - 1) * (d.start ?? 0) / 100))
        const i1 = Math.min(totalDays - 1, Math.round((totalDays - 1) * (d.end ?? 100) / 100))
        c2.setOption({ series: [{ markLine: volMarkLines(i0, i1) }] })
      } catch (e) { /* 分位线是增强信息，异常静默不影响主图 */ }
    })
  }
  const c3 = make('c3', {
    tooltip:tt, grid:{...GRID_STD, top:8},
    xAxis:xA, yAxis:{splitLine:gl},
    dataZoom:dz,
    series:[
      {name:'柱',type:'bar',data:ba,itemStyle:{color:p=>bc[p.dataIndex]}},
      {name:'DIF',type:'line',data:di,lineStyle:{color:'#f59e0b',width:1},symbol:'none',smooth:true},
      {name:'DEA',type:'line',data:de,lineStyle:{color:'#06b6d4',width:1},symbol:'none',smooth:true}
    ]
  })
  const c4 = make('c4', {
    tooltip:tt, grid:{...GRID_STD, top:8},
    xAxis:xA, yAxis:{min:0,max:100,splitLine:gl},
    dataZoom:dz,
    series:[{name:'RSI',type:'line',data:rs,lineStyle:{color:'#8b5cf6',width:1.5},symbol:'none',smooth:true,areaStyle:{color:'rgba(139,92,246,0.1)'},
      markLine:{silent:true,symbol:'none',data:[{yAxis:70,label:{formatter:'超买'},lineStyle:{color:'#ef4444',type:'dashed'}},{yAxis:30,label:{formatter:'超卖'},lineStyle:{color:'#10b981',type:'dashed'}}]}}
    ]
  })
  calcPriceChange(kd)
  // crosshair 交互：hover K 线时更新动态行情数据（含成交额/换手）
  if(c1){
    c1.on('mousemove', p=>{
      if(p.dataIndex!=null){
        const o = ohlc[p.dataIndex]
        hoverInfo.value = { date: dates[p.dataIndex], open: o[0], close: o[1], low: o[2], high: o[3], volume: vols[p.dataIndex], amount: amts[p.dataIndex] ?? null, turnover: trns[p.dataIndex] ?? null, prevClose: p.dataIndex>0 ? ohlc[p.dataIndex-1][1] : null }
      }
    })
    c1.on('mouseout', ()=>{ hoverInfo.value = null })
  }
  // 均线图（MA5~MA180，与 K 线联动 zoom/十字线；图例可点选显隐——七条线
  // 仅靠颜色难辨，2026-09-15 用户反馈补）
  const c6 = make('c6', {
    tooltip: tt,
    legend:{show:true, top:0, left:'center', itemWidth:14, itemHeight:8, itemGap:6,
            textStyle:{fontSize:9, color:'#9ca3af'}},
    grid:{...GRID_STD, top:26},
    xAxis: { ...xA, axisLabel: { show: true, fontSize: 9, interval: 'auto' } },
    yAxis:{scale:true,splitLine:gl},
    dataZoom:dz,
    series:[
      {name:'MA5',type:'line',data:ma5,lineStyle:{color:'#ef4444',width:1},symbol:'none',smooth:true},
      {name:'MA10',type:'line',data:ma10,lineStyle:{color:'#f59e0b',width:1},symbol:'none',smooth:true},
      {name:'MA20',type:'line',data:ma20,lineStyle:{color:'#06b6d4',width:1},symbol:'none',smooth:true},
      {name:'MA30',type:'line',data:ma30,lineStyle:{color:'#10b981',width:1},symbol:'none',smooth:true},
      {name:'MA60',type:'line',data:ma60,lineStyle:{color:'#8b5cf6',width:1},symbol:'none',smooth:true},
      {name:'MA120',type:'line',data:ma120,lineStyle:{color:'#ec4899',width:1},symbol:'none',smooth:true},
      {name:'MA180',type:'line',data:ma180,lineStyle:{color:'#64748b',width:1},symbol:'none',smooth:true},
    ]
  })
  const charts = [c1,c2,c3,c4,c6].filter(Boolean)
  if(charts.length){charts.forEach(c=>c.group='s');echarts.connect('s')}
  // K线组 ↔ PE图 日历联动（正向）：监听任一成员缩放（connect 会同步到 c1 的
  // dataZoom），节流后翻译日期窗给 PE 图；重绘（range/period/复权切换）后立即对齐一次
  klineDates = dates
  klineMainChart = c1 || charts[0] || null
  if (klineMainChart) {
    let _axLast = 0
    klineMainChart.off('datazoom')   // drawCharts 每次重绘都会走到，避免处理器累积
    klineMainChart.on('datazoom', () => {
      const now = Date.now()
      if (syncingAx || now - _axLast < 120) return
      _axLast = now
      syncPeFromKline()
    })
    syncPeFromKline()
  }
}

// ── 扩展图（资金流/相对强弱/换手率/两融/ATR + K线信号/龙虎榜叠加）──
// 与主五图共用交易日轴（辅助序列 reindex 到 K 线日期，缺失日 null 断开），
// 进同一 connect 组：任一图缩放全组同步。数据晚于主图到达/换窗重绘均安全。
function drawAuxCharts(kd){
  if(!kd||!kd.kline||!kd.kline.length) return
  const dates = kd.kline.map(d=>d.trade_date)
  const closes = kd.kline.map(d=>d.close)
  const ohlc = kd.kline.map(d=>[d.open,d.close,d.low,d.high])
  const trns = kd.kline.map(d=>d.turnover)
  const idxOf = new Map(dates.map((d,i)=>[d,i]))
  const reindex = (arr, key) => { const m=new Map(arr.map(x=>[x.date,x[key]])); return dates.map(d=>m.has(d)?m.get(d):null) }
  const gl = {lineStyle:{color:'rgba(128,128,128,0.1)'}}
  const totalDays = dates.length
  const SHOW = totalDays<=250?0:((totalDays-250)/totalDays*100).toFixed(1)
  const dz = [
    {type:'inside', xAxisIndex:0, zoomOnMouseWheel:true, moveOnMouseWheel:'shift'},
    {type:'slider', xAxisIndex:0, start:SHOW, end:100, ...DZ_SLIDER}
  ]
  const xA = {type:'category',data:dates,axisLabel:{show:false},
    axisLine:{lineStyle:{color:'rgba(128,128,128,0.15)'}}, axisTick:{show:false}}
  const tt = {trigger:'axis',axisPointer:{type:'cross'}}
  const newCharts = []

  // c7 资金流：主力净流入柱（万元→亿）+ 累计线（右轴）
  if (mfData.value.length) {
    const nm = reindex(mfData.value, 'net_main')
    const cum = reindex(mfCum.value.map((v,i)=>({date:mfData.value[i].date, v})), 'v')
    const c7 = makeChart('c7', {
      tooltip:tt, grid:{...GRID_STD, top:8},
      xAxis:xA, yAxis:[
        {axisLabel:{fontSize:9,formatter:v=>(v/1e4).toFixed(1)+'亿'},splitLine:gl},
        {axisLabel:{fontSize:9,formatter:v=>(v/1e4).toFixed(0)+'亿'},splitLine:{show:false}}],
      dataZoom:dz,
      series:[
        {name:'主力净流入',type:'bar',data:nm,itemStyle:{color:p=>(nm[p.dataIndex]??0)>=0?'rgba(239,68,68,0.85)':'rgba(16,185,129,0.85)'}},
        {name:'累计(右轴)',type:'line',yAxisIndex:1,data:cum,lineStyle:{color:'#60a5fa',width:1.5},symbol:'none'}
      ]
    })
    if(c7) newCharts.push(c7)
  }

  // c8 相对强弱：个股 vs 沪深300 归一化（窗口内首个两者皆有数据处=1）+ 超额差
  if (benchData.value.length) {
    const bc = reindex(benchData.value, 'close')
    // 基准缺口前向填充（停牌日个股无行、指数有行 → reindex null）
    let last = null
    for(let i=0;i<bc.length;i++){ if(bc[i]!=null) last=bc[i]; else if(last!=null) bc[i]=last }
    let i0 = 0
    while(i0<dates.length && (closes[i0]==null||bc[i0]==null)) i0++
    if (i0 < dates.length) {
      const b0 = closes[i0], m0 = bc[i0]
      const rel = closes.map(c=>c!=null?+(c/b0).toFixed(4):null)
      const ben = bc.map(c=>c!=null?+(c/m0).toFixed(4):null)
      const exc = rel.map((v,i)=>v!=null&&ben[i]!=null?+(v-ben[i]).toFixed(4):null)
      const c8 = makeChart('c8', {
        tooltip:tt, grid:{...GRID_STD, top:18},
        legend:{show:true,top:0,itemWidth:14,itemHeight:8,itemGap:6,textStyle:{fontSize:9,color:'#9ca3af'}},
        xAxis:xA, yAxis:{scale:true,splitLine:gl,axisLabel:{fontSize:9}},
        dataZoom:dz,
        series:[
          {name:'个股',type:'line',data:rel,lineStyle:{color:'#60a5fa',width:1.5},symbol:'none'},
          {name:'沪深300',type:'line',data:ben,lineStyle:{color:'#94a3b8',width:1},symbol:'none'},
          {name:'超额',type:'line',data:exc,lineStyle:{color:'#f59e0b',width:1,type:'dashed'},symbol:'none'}
        ]
      })
      if(c8) newCharts.push(c8)
    }
  }

  // c9 换手率：柱 + P20/50/80 分位线（与成交量图同思路，缩放跟随重算）
  {
    const pctl = (arr,q)=>{ const s=arr.filter(v=>v!=null&&v>0).sort((a,b)=>a-b); if(!s.length) return 0
      const pos=(s.length-1)*q, lo=Math.floor(pos), hi=Math.ceil(pos); return s[lo]+(s[hi]-s[lo])*(pos-lo) }
    const marks = (i0,i1)=>{ const w=trns.slice(i0,i1+1); return {silent:true,symbol:'none',animation:false,
      data:[{yAxis:pctl(w,0.2),label:{formatter:`P20 ${pctl(w,0.2).toFixed(1)}%`,position:'insideEndTop',fontSize:9,color:'rgba(156,163,175,0.8)'},lineStyle:{color:'rgba(156,163,175,0.8)',type:'dashed',width:1}},
            {yAxis:pctl(w,0.5),label:{formatter:`P50 ${pctl(w,0.5).toFixed(1)}%`,position:'insideEndTop',fontSize:9,color:'#60a5fa'},lineStyle:{color:'#60a5fa',type:'dashed',width:1}},
            {yAxis:pctl(w,0.8),label:{formatter:`P80 ${pctl(w,0.8).toFixed(1)}%`,position:'insideEndTop',fontSize:9,color:'rgba(156,163,175,0.8)'},lineStyle:{color:'rgba(156,163,175,0.8)',type:'dashed',width:1}}]} }
    const initI0 = Math.max(0, Math.round((totalDays-1)*(parseFloat(SHOW)||0)/100))
    const c9 = makeChart('c9', {
      tooltip:tt, grid:{...GRID_STD, top:8},
      xAxis:xA, yAxis:{axisLabel:{fontSize:9,formatter:'{value}%'},splitLine:gl},
      dataZoom:dz,
      series:[{name:'换手率',type:'bar',data:trns,itemStyle:{color:'rgba(96,165,250,0.6)'},markLine:marks(initI0,totalDays-1)}]
    })
    if(c9){
      newCharts.push(c9)
      let _tLast = 0
      c9.off('datazoom')
      c9.on('datazoom', ()=>{
        const now=Date.now(); if(now-_tLast<120) return; _tLast=now
        try{
          const d=c9.getOption().dataZoom?.[0]; if(!d) return
          const a=Math.max(0,Math.round((totalDays-1)*(d.start??0)/100)), b=Math.min(totalDays-1,Math.round((totalDays-1)*(d.end??100)/100))
          c9.setOption({series:[{markLine:marks(a,b)}]})
        }catch(e){}
      })
    }
  }

  // c10 两融余额：融资/合计（元→亿），数据稀疏处 connect:'none' 断开不假连线
  if (marginData.value.length > 2) {
    const fin = reindex(marginData.value,'fin'), tot = reindex(marginData.value,'total')
    const c10 = makeChart('c10', {
      tooltip:tt, grid:{...GRID_STD, top:8},
      xAxis:xA, yAxis:{axisLabel:{fontSize:9,formatter:v=>(v/1e8).toFixed(0)+'亿'},splitLine:gl},
      dataZoom:dz,
      series:[
        {name:'融资余额',type:'line',data:fin,lineStyle:{color:'#f59e0b',width:1.5},symbol:'none',connect:'none'},
        {name:'两融合计',type:'line',data:tot,lineStyle:{color:'#8b5cf6',width:1},symbol:'none',connect:'none'}
      ]
    })
    if(c10) newCharts.push(c10)
  }

  // c11 ATR(14)：真实波幅均线 + 占价比%（右轴）——仓位管理视角
  {
    const tr = ohlc.map((o,i)=>{ if(i===0||!o||!ohlc[i-1]) return null
      const pc=ohlc[i-1][1], h=o[3], l=o[2]
      return Math.max(h-l, Math.abs(h-pc), Math.abs(l-pc)) })
    const atr = tr.map((_,i)=>{ if(i<14) return null; let s=0; for(let j=i-13;j<=i;j++) s+=tr[j]; return +(s/14).toFixed(3) })
    const atrPct = atr.map((v,i)=>v!=null&&closes[i]?+(v/closes[i]*100).toFixed(2):null)
    const c11 = makeChart('c11', {
      tooltip:tt, grid:{...GRID_STD, top:8},
      xAxis:xA, yAxis:[
        {axisLabel:{fontSize:9},splitLine:gl,scale:true},
        {axisLabel:{fontSize:9,formatter:'{value}%'},splitLine:{show:false}}],
      dataZoom:dz,
      series:[
        {name:'ATR14',type:'line',data:atr,lineStyle:{color:'#8b5cf6',width:1.5},symbol:'none'},
        {name:'占价比(右轴)',type:'line',yAxisIndex:1,data:atrPct,lineStyle:{color:'#f59e0b',width:1,type:'dashed'},symbol:'none'}
      ]
    })
    if(c11) newCharts.push(c11)
  }

  // c1 叠加层：模型信号（▲买/▼卖，挂当日低/高点附近——与复权口径无关）+ 龙虎榜（◆橙）
  const c1El = document.getElementById('c1'); const c1 = c1El && c1El._echart
  if (c1) {
    const sigPts = sigData.value.map(s=>{
      const i = idxOf.get(s.date); if(i==null) return null
      const y = s.direction==='buy' ? ohlc[i][2]*0.985 : ohlc[i][3]*1.015
      return {value:[i,y], symbolRotate: s.direction==='buy'?0:180,
              itemStyle:{color: s.direction==='buy'?'#ef4444':'#10b981'}}
    }).filter(Boolean)
    const topPts = toplistData.value.map(t=>{
      const i = idxOf.get(t.date); if(i==null) return null
      return {value:[i, ohlc[i][2]*0.96], itemStyle:{color:'#f59e0b'}}
    }).filter(Boolean)
    // 按 id 合并追加（notMerge=false）：不触碰 K 线/BOLL 既有系列，重绘时按 id 原位替换
    c1.setOption({series:[
      {id:'sig_marks', name:'信号', type:'scatter', data:sigPts, symbol:'triangle', symbolSize:9, z:5, tooltip:{show:false}},
      {id:'top_marks', name:'龙虎榜', type:'scatter', data:topPts, symbol:'diamond', symbolSize:7, z:5, tooltip:{show:false}}
    ]}, {notMerge:false})
  }

  // 入联动组（connect 组按组名广播，重复 connect 安全）
  if (newCharts.length) { newCharts.forEach(c=>c.group='s'); echarts.connect('s') }
}

// 侧栏筹码分布（现价口径的前复权价，非时间轴图、不进联动组）
function drawChip(){
  const el = document.getElementById('c12'); const d = chipData.value
  if(!el || !d || !d.buckets || !d.buckets.length) return
  let c = el._echart
  if(!c){ c = echarts.init(el); el._echart = c }
  const prices = d.buckets.map(b=>b.price)
  const vols = d.buckets.map(b=>b.vol/1e8)   // 亿股
  const nearest = prices.reduce((best,p,i)=>Math.abs(p-d.current)<Math.abs(prices[best]-d.current)?i:best,0)
  c.setOption({
    tooltip:{trigger:'axis',axisPointer:{type:'shadow'},textStyle:{fontSize:10},
      formatter: ps=>{ const p=ps[0]; return `¥${p.name}<br/>成交 ${p.value} 亿股` }},
    grid:{left:6,right:34,top:6,bottom:20,containLabel:true},
    xAxis:{type:'value',axisLabel:{fontSize:9,formatter:'{value}亿'},splitLine:{lineStyle:{color:'rgba(128,128,128,0.1)'}}},
    yAxis:{type:'category',data:prices.map(p=>p.toFixed(2)),axisLabel:{fontSize:9}},
    series:[{type:'bar',barWidth:'72%',
      data:vols.map((v,i)=>({value:v,itemStyle:{color: prices[i]<=d.current?'rgba(16,185,129,0.7)':'rgba(239,68,68,0.45)'}})),
      markLine:{silent:true,symbol:'none',
        data:[{yAxis:nearest,label:{formatter:'现价',fontSize:9,color:'#60a5fa'},lineStyle:{color:'#60a5fa',type:'dashed',width:1}}]}}
    ]
  }, {notMerge:true})
}

// 侧栏信号摘要（近 3 年）
function computeSigStats(){
  const d = sigData.value
  if(!d.length){ sigStats.value=null; return }
  const avg = k=>{ const xs=d.filter(x=>x[k]!=null).map(x=>x[k]); return xs.length?xs.reduce((a,b)=>a+b,0)/xs.length:0 }
  sigStats.value = { n:d.length, years:3,
    buys:d.filter(x=>x.direction==='buy').length, sells:d.filter(x=>x.direction==='sell').length,
    f5:avg('f5d'), f10:avg('f10d'), f20:avg('f20d') }
}

function drawPeChart(){
  const peEl = document.getElementById('c5')
  if(!peEl || !peData.value.length) return
  // 实例复用
  let c5 = peEl._echart
  if(!c5){ c5 = echarts.init(peEl); peEl._echart = c5 }
  const peDates = peData.value.map(d=>d.date)
  const peVals = peData.value.map(d=>d.pe_ttm)
  const pbVals = peData.value.map(d=>d.pb_mrq)
  const pctl = peData.value.map(d=>d.pe_percentile)
  peRange.value = peDates[0]+' ~ '+peDates[peDates.length-1]
  c5.setOption({
    tooltip:{trigger:'axis',axisPointer:{type:'cross'}},
    legend:{show:true, top:0, left:'center', itemWidth:14, itemHeight:8, itemGap:6,
            textStyle:{fontSize:9, color:'#9ca3af'}},
    grid:{...GRID_STD, top:26},
    xAxis:{type:'category',data:peDates,axisLabel:{show:false},
      axisLine:{lineStyle:{color:'rgba(128,128,128,0.15)'}},axisTick:{show:false}},
    yAxis:[
      {type:'value',name:'PE',splitLine:{lineStyle:{color:'rgba(128,128,128,0.1)'}}},
      {type:'value',name:'%',min:0,max:100,splitLine:{show:false}}
    ],
    dataZoom:[
      // 滚轮独立缩放保留；窗口与 K 线组经「日历翻译」双向联动（见 syncPe/syncKline），
      // 不直接进 connect 组——两轴日期集不同，百分比联动会对到不同日历时段
      {type:'inside', zoomOnMouseWheel:true, moveOnMouseWheel:'shift'},
      {type:'slider',start:0,end:100,...DZ_SLIDER}
    ],
    series:[
      {name:'PE(TTM)',type:'line',data:peVals,lineStyle:{color:'#60a5fa',width:1.5},symbol:'none',smooth:true,areaStyle:{color:'rgba(96,165,250,0.1)'}},
      {name:'PB',type:'line',data:pbVals,lineStyle:{color:'#ec4899',width:1},symbol:'none',smooth:true},
      {name:'分位%',type:'line',yAxisIndex:1,data:pctl,lineStyle:{color:'#f59e0b',width:1,type:'dashed'},symbol:'none',smooth:true,
        markLine:{silent:true,symbol:'none',data:[
          {yAxis:30,label:{formatter:'低估30',fontSize:9,position:'insideEndTop',color:'#10b981'},lineStyle:{color:'#10b981',type:'dashed',width:1}},
          {yAxis:70,label:{formatter:'高估70',fontSize:9,position:'insideEndBottom',color:'#ef4444'},lineStyle:{color:'#ef4444',type:'dashed',width:1}}]}}
    ]
  })
  // 反向联动：拖 PE 滑块 → 日历窗翻译 → K 线组；绘制完成即按当前 K 线窗口对齐一次
  c5.off('datazoom')
  c5.on('datazoom', () => { if (!syncingAx) syncKlineFromPe() })
  syncPeFromKline()
}

async function reloadChart(){
  localStorage.setItem('detail_adj', adj.value)
  try{
    const r = await axios.get(API+'/api/stock/'+code.value+'/kline?days='+range.value+'&period='+period.value+'&adjust='+adj.value)
    if(r.data.kline) lastKline = r.data
    // 相对强弱的基准与 K 线同窗同周期，换窗必须重取（其余扩展序列是长窗 reindex）
    try{
      const rb = await axios.get(API+'/api/stock/000300/kline?days='+range.value+'&period='+period.value+'&adjust=none&type=index')
      benchData.value = rb.data.kline||[]
    }catch(e){}
  }catch(e){} finally {
    if(lastKline){ await nextTick(); drawCharts(lastKline); drawAuxCharts(lastKline); drawChip() }
  }
}

// 窗口 resize 时自适应所有图表
function resizeAll(){ ['c1','c2','c3','c4','c5','c6','c7','c8','c9','c10','c11','c12'].forEach(id => { const el = document.getElementById(id); if (el && el._echart) el._echart.resize() }) }
let _resizeHandler = null
onMounted(() => {
  _resizeHandler = window.addEventListener ? window.addEventListener('resize', resizeAll) : null
  if(code.value) load()
})
onUnmounted(() => { if (_resizeHandler && window.removeEventListener) window.removeEventListener('resize', resizeAll) })

watch(()=>props.code, v=>{if(v && v!==code.value){code.value=v;load()}})
</script>

<style>
/* 详情页分组标签：✕ 平时隐去，hover 才出现（naive-ui n-tag closable 默认常显） */
.dt-grp-tag .n-tag__close { opacity: 0; transition: opacity .15s; }
.dt-grp-tag:hover .n-tag__close { opacity: 1; }
</style>
