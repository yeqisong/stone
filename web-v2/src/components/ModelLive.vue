<template>
<div>
  <div v-if="!health" style="color:var(--c-text-dim);padding:20px 0;text-align:center">暂无实盘数据</div>
  <template v-else>
    <div style="display:flex;gap:16px;margin-bottom:20px;flex-wrap:wrap">
      <div style="background:var(--c-card-bg);border:1px solid var(--c-border);border-radius:10px;padding:14px 18px;min-width:90px;text-align:center">
        <div style="font-size:10px;color:var(--c-text-faint)">健康度</div>
        <div :style="{fontSize:'20px',fontWeight:700,color:healthColor}">{{health.health_status}}</div>
      </div>
      <div style="background:var(--c-card-bg);border:1px solid var(--c-border);border-radius:10px;padding:14px 18px;min-width:90px;text-align:center">
        <div style="font-size:10px;color:var(--c-text-faint)">实盘胜率</div>
        <div style="fontSize:20px;fontWeight:700;color:var(--c-text)">{{ health.live_win_rate==null?'—':(health.live_win_rate*100).toFixed(0)+'%' }}</div>
      </div>
      <div style="background:var(--c-card-bg);border:1px solid var(--c-border);border-radius:10px;padding:14px 18px;min-width:90px;text-align:center">
        <div style="font-size:10px;color:var(--c-text-faint)">信号总数</div>
        <div style="fontSize:20px;fontWeight:700;color:var(--c-text)">{{health.signal_count}}</div>
      </div>
      <div style="background:var(--c-card-bg);border:1px solid var(--c-border);border-radius:10px;padding:14px 18px;min-width:90px;text-align:center">
        <div style="font-size:10px;color:var(--c-text-faint)">Avg Fwd 5D</div>
        <div :style="{fontSize:'20px',fontWeight:700,color:health.avg_forward_5d==null?'var(--c-text-faint)':(health.avg_forward_5d>=0?'#ef4444':'#10b981')}">{{ health.avg_forward_5d==null?'—':(health.avg_forward_5d*100).toFixed(2)+'%' }}</div>
      </div>
      <div v-if="health.rank_ic!=null" style="background:var(--c-card-bg);border:1px solid var(--c-border);border-radius:10px;padding:14px 18px;min-width:90px;text-align:center"
           title="模型预测与实现收益的逐日截面 RankIC 均值（近 20 个已成熟截面，10 日前瞻）；<0 说明模型选股能力已衰减">
        <div style="font-size:10px;color:var(--c-text-faint)">滚动 RankIC</div>
        <div :style="{fontSize:'20px',fontWeight:700,color:health.rank_ic>0.005?'#10b981':health.rank_ic>=0?'#f59e0b':'#ef4444'}">{{health.rank_ic>=0?'+':''}}{{health.rank_ic.toFixed(4)}}</div>
        <div style="font-size:9px;color:var(--c-text-faint);margin-top:2px">ICIR {{health.rank_icir!=null?(health.rank_icir>=0?'+':'')+health.rank_icir.toFixed(2):'—'}}</div>
      </div>
    </div>

    <!-- Signal Detail List -->
    <div style="background:var(--c-card-bg);border:1px solid var(--c-border);border-radius:10px;padding:16px">
      <div style="font-size:12px;font-weight:600;color:var(--c-text-dim);margin-bottom:10px">最近信号明细</div>
      <div v-if="signals.length" style="display:flex;flex-direction:column;gap:4px">
        <div v-for="s in signals" :key="s.id" style="display:flex;align-items:center;gap:10px;padding:6px 10px;background:var(--c-card-bg-hover);border-radius:6px;font-size:11px">
          <span style="color:var(--c-text-faint);min-width:75px">{{(s.signal_date||'').slice(0,10)}}</span>
          <span :style="{color:s.direction==='buy'?'#ef4444':'#10b981',fontWeight:600,minWidth:30}">{{s.direction==='buy'?'买':'卖'}}</span>
          <span style="color:var(--c-text);min-width:70px">{{s.stock_code}}</span>
          <span style="color:var(--c-text-dim);min-width:50px">¥{{(s.price||0).toFixed(2)}}</span>
          <span :style="{color:(s.forward_5d_return||0)>=0?'#ef4444':'#10b981',marginLeft:'auto'}">{{s.forward_5d_return ? (s.forward_5d_return*100).toFixed(2)+'%' : '—'}}</span>
          <span :style="{color:(s.actual_return||0)>=0?'#ef4444':'#10b981'}">{{s.actual_return ? '实'+(s.actual_return*100).toFixed(1)+'%' : ''}}</span>
        </div>
      </div>
      <div v-else style="font-size:11px;color:var(--c-text-faint);text-align:center;padding:10px">暂无信号</div>
    </div>

    <!-- 纸面组合（影子运行） -->
    <div style="background:var(--c-card-bg);border:1px solid var(--c-border);border-radius:10px;padding:16px;margin-top:16px">
      <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:10px">
        <div style="font-size:12px;font-weight:600;color:var(--c-text-dim)"><AppIcon name="e" :size="13" />  纸面组合（影子运行）<span style="font-weight:400;color:var(--c-text-faint);margin-left:8px">跟随 ACTIVE 模型信号每日模拟成交，与实盘互不干扰</span></div>
        <n-button size="tiny" quaternary @click="loadPaper"><AppIcon name="refresh" :size="13" /> </n-button>
      </div>
      <div v-if="!paper || !paper.equity.length" style="font-size:11px;color:var(--c-text-faint);text-align:center;padding:14px">
        暂无影子记录——信号生成时自动建立，或在 DAG 流程中加入 paper_portfolio 节点回放历史信号
      </div>
      <template v-else>
        <div style="display:flex;gap:10px;flex-wrap:wrap;margin-bottom:10px">
          <div style="flex:1;min-width:100px;text-align:center;padding:8px;background:var(--c-bg);border-radius:6px">
            <!-- 配色约定（全站统一）：涨跌类指标用「涨红跌绿」（A股惯例）；
                 质量类指标（胜率/IC/IR/健康度）用「绿=好」，两者不可混用 -->
            <div style="font-size:9px;color:var(--c-text-faint)">影子收益</div>
            <div :style="{fontSize:'18px',fontWeight:700,color:(paper.stats.total_return||0)>=0?'#ef4444':'#10b981'}">
              {{((paper.stats.total_return||0)*100).toFixed(2)}}%</div>
          </div>
          <div style="flex:1;min-width:100px;text-align:center;padding:8px;background:var(--c-bg);border-radius:6px">
            <div style="font-size:9px;color:var(--c-text-faint)">同期沪深300</div>
            <div :style="{fontSize:'18px',fontWeight:700,color:(paper.stats.benchmark_return||0)>=0?'#ef4444':'#10b981'}">
              {{paper.stats.benchmark_return!=null?((paper.stats.benchmark_return)*100).toFixed(2)+'%':'—'}}</div>
          </div>
          <div style="flex:1;min-width:100px;text-align:center;padding:8px;background:var(--c-bg);border-radius:6px">
            <div style="font-size:9px;color:var(--c-text-faint)">模拟卖出胜率</div>
            <div style="font-size:18px;font-weight:700;color:var(--c-text)">{{paper.stats.win_rate!=null?(paper.stats.win_rate*100).toFixed(0)+'%':'—'}}</div>
          </div>
          <div style="flex:1;min-width:100px;text-align:center;padding:8px;background:var(--c-bg);border-radius:6px">
            <div style="font-size:9px;color:var(--c-text-faint)">模拟成交 / 持仓</div>
            <div style="font-size:18px;font-weight:700;color:var(--c-text)">{{paper.stats.n_trades}} / {{paper.stats.n_positions}}</div>
          </div>
        </div>
        <div ref="paperChart" style="width:100%;height:220px"></div>
        <div v-if="paper.positions.length" style="margin-top:10px">
          <div style="font-size:11px;font-weight:600;color:var(--c-text-dim);margin-bottom:6px">影子持仓（{{paper.positions.length}}）</div>
          <div style="display:flex;flex-wrap:wrap;gap:4px">
            <n-tag v-for="p in paper.positions" :key="p.stock_code" size="small" :bordered="false">
              {{p.stock_code}} {{p.stock_name}} ×{{p.shares}} @{{p.buy_price?.toFixed(2)}}（{{p.buy_date?.slice(5)}}起）
            </n-tag>
          </div>
        </div>
        <div v-if="paper.trades?.length" style="margin-top:12px">
          <div style="font-size:11px;font-weight:600;color:var(--c-text-dim);margin-bottom:6px">最近模拟成交（{{paper.trades.length}} 笔）</div>
          <div style="max-height:240px;overflow-y:auto;border:1px solid var(--c-border);border-radius:8px">
            <table style="width:100%;border-collapse:collapse;font-size:11px">
              <thead>
                <tr style="position:sticky;top:0;background:var(--c-card-bg);color:var(--c-text-dim);text-align:left">
                  <th style="padding:6px 8px;font-weight:600">日期</th>
                  <th style="padding:6px 8px;font-weight:600">方向</th>
                  <th style="padding:6px 8px;font-weight:600">个股</th>
                  <th style="padding:6px 8px;font-weight:600;text-align:right">价格</th>
                  <th style="padding:6px 8px;font-weight:600;text-align:right">股数</th>
                  <th style="padding:6px 8px;font-weight:600;text-align:right">金额</th>
                  <th style="padding:6px 8px;font-weight:600;text-align:right">盈亏</th>
                  <th style="padding:6px 8px;font-weight:600">原因</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="(t,i) in paper.trades" :key="i" style="border-top:1px solid var(--c-border)">
                  <td style="padding:5px 8px;color:var(--c-text-dim)">{{t.date?.slice(5)}}</td>
                  <td :style="{padding:'5px 8px',color:t.action==='BUY'?'#ef4444':'#10b981',fontWeight:600}">{{t.action==='BUY'?'买入':'卖出'}}</td>
                  <td style="padding:5px 8px;color:var(--c-text)">{{t.stock_code}} {{t.stock_name}}</td>
                  <td style="padding:5px 8px;text-align:right;color:var(--c-text-dim)">{{t.price?.toFixed(2)}}</td>
                  <td style="padding:5px 8px;text-align:right;color:var(--c-text-dim)">{{t.shares?.toLocaleString()}}</td>
                  <td style="padding:5px 8px;text-align:right;color:var(--c-text-dim)">{{t.amount?.toLocaleString(undefined,{maximumFractionDigits:0})}}</td>
                  <td :style="{padding:'5px 8px',textAlign:'right',color:t.pnl==null?'var(--c-text-faint)':(t.pnl>=0?'#ef4444':'#10b981')}">{{t.pnl==null?'—':(t.pnl>=0?'+':'')+t.pnl.toFixed(0)}}</td>
                  <td style="padding:5px 8px;color:var(--c-text-dim)">{{reasonCn(t.reason)}}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>
      </template>
    </div>
  </template>
</div>
</template>

<script setup>
import AppIcon from './AppIcon.vue'
import { computed, ref, onMounted, nextTick } from 'vue'
import axios from 'axios'
import * as echarts from 'echarts'
import { NTag, NButton } from 'naive-ui'

const props = defineProps({ version: Object })
const health = ref(null)
const signals = ref([])
const paper = ref(null)
const paperChart = ref(null)
let paperChartInst = null

const healthColor = computed(() => {
  const s = health.value?.health_status
  return { HEALTHY:'#10b981', CAUTION:'#f59e0b', WARNING:'#f97316', CRITICAL:'#ef4444' }[s] || '#6b7280'
})

// 卖出原因中文化（与 _paper_meta/风控 reason 枚举对应；未知值原样显示）
const REASON_CN = { signal: '信号买入', stop_loss: '止损', take_profit: '止盈', trailing: '移动止盈',
                    hold_expire: '持有到期', expire: '持有到期', regime: '空仓闸门' }
const reasonCn = r => REASON_CN[r] || r || '—'

onMounted(async () => {
  try {
    const [hr, sr] = await Promise.all([
      axios.get(window.location.origin + `/api/v1/models/${props.version.version}/health`),
      axios.get(window.location.origin + `/api/v1/models/${props.version.version}/signals`),
    ])
    health.value = hr.data
    signals.value = sr.data?.signals || []
  } catch(e) {}
  loadPaper()
})

async function loadPaper() {
  try {
    // 按模型隔离：每个版本查看自己的影子账户（legacy=共享账户时代的存档）
    const r = await axios.get(window.location.origin + '/api/v1/models/paper-portfolio',
                              { params: { version: props.version?.version } })
    paper.value = r.data
    if (paper.value?.equity?.length) nextTick(() => renderPaper())
  } catch(e) { console.error(e) }
}

function renderPaper() {
  if (!paperChart.value) return
  paperChartInst?.dispose()
  paperChartInst = echarts.init(paperChart.value)
  const eq = paper.value.equity
  const hasBm = eq.some(x => x.benchmark)
  paperChartInst.setOption({
    tooltip: { trigger: 'axis' },
    legend: { top: 0, textStyle: { fontSize: 9 }, itemWidth: 12 },
    grid: { left: 60, right: 14, top: 24, bottom: 22 },
    xAxis: { type: 'category', data: eq.map(x => x.date), axisLabel: { fontSize: 8, interval: Math.max(1, Math.floor(eq.length / 6)) } },
    yAxis: { type: 'value', scale: true, axisLabel: { fontSize: 8, formatter: v => (v / 10000).toFixed(0) + '万' } },
    series: [
      { type: 'line', name: '纸面组合净值', data: eq.map(x => x.equity), showSymbol: false,
        lineStyle: { width: 2, color: '#10b981' } },
      ...(hasBm ? [{ type: 'line', name: '沪深300等额', data: eq.map(x => x.benchmark), showSymbol: false,
        lineStyle: { width: 1.5, type: 'dashed', color: '#9ca3af' } }] : []),
    ],
  })
}
</script>