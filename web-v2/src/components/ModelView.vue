<template>
<div>
  <n-spin v-if="loading" style="padding:60px" />
  <template v-else>
    <div style="display:flex;gap:0;height:calc(100vh - 110px)">
      <!-- Left: Version List -->
      <div style="width:280px;flex-shrink:0;border-right:1px solid var(--c-border);display:flex;flex-direction:column;overflow:hidden">
        <div style="padding:10px 16px;display:flex;align-items:center;justify-content:space-between">
          <span style="font-size:13px;font-weight:600;color:var(--c-text)">模型版本</span>
          <n-button size="tiny" type="primary" ghost @click="showCreate=true">+ 创建</n-button>
        </div>
        <div style="flex:1;overflow-y:auto;padding:0 8px">
          <div v-for="v in store.versions" :key="v.version"
            :style="{padding:'12px',marginBottom:'4px',borderRadius:'8px',border:'1px solid '+(store.selectedId===v.version?'var(--c-border)':'transparent'),cursor:'pointer',background:store.selectedId===v.version?'var(--c-card-bg-hover)':'transparent'}"
            @click="store.selectVersion(v.version)">
            <div style="display:flex;align-items:center;gap:8px">
              <span style="font-size:15px;font-weight:700;color:var(--c-text)">{{v.version}}</span>
              <n-tag :type="store.statusBadge(v.status)" size="tiny" :bordered="false">{{store.statusLabel(v.status)}}</n-tag>
            </div>
            <div style="font-size:11px;color:var(--c-text-dim);margin-top:4px">{{v.model_name}}</div>
            <div style="display:flex;gap:12px;margin-top:6px;font-size:10px;color:var(--c-text-faint)">
              <span v-if="v.sharpe!=null">📈 夏普 {{v.sharpe}}</span>
              <span v-if="v.win_rate!=null">✅ {{(v.win_rate*100).toFixed(0)}}%</span>
              <span>📅 {{(v.created_at||'').slice(5)}}</span>
            </div>
          </div>
          <n-empty v-if="!store.versions.length" description="暂无模型版本" style="padding:40px 0" />
        </div>
      </div>

      <!-- Right: Detail -->
      <div style="flex:1;overflow-y:auto;padding:16px 24px">
        <template v-if="store.selected">
          <div style="font-size:16px;font-weight:700;color:var(--c-text);margin-bottom:14px">
            {{store.selected.version}} · {{store.selected.model_name}}
          </div>

          <div style="display:flex;gap:2px;margin-bottom:16px;border-bottom:1px solid var(--c-border)">
            <button v-for="t in tabs" :key="t.key"
              :style="{padding:'8px 16px',border:'none',background:'transparent',color:store.detailTab===t.key?'var(--c-text)':'var(--c-text-dim)',fontSize:'12px',cursor:'pointer',borderBottom:store.detailTab===t.key?'2px solid #2080f0':'2px solid transparent',marginBottom:'-1px'}"
              @click="store.switchTab(t.key)">{{t.label}}</button>
          </div>

          <div v-if="store.detailTab==='basic'">
            <div style="color:var(--c-text-dim);font-size:13px;padding:20px 0">基本信息 — 待开发（四层配置只读展示）</div>
          </div>
          <ModelTraining v-else-if="store.detailTab==='train'" :version="store.selected" />
          <div v-else-if="store.detailTab==='eval'">
            <div style="color:var(--c-text-dim);font-size:13px;padding:20px 0">评估 — 待开发（核心指标卡片+权益曲线+版本对比）</div>
          </div>
          <div v-else-if="store.detailTab==='live'">
            <div style="color:var(--c-text-dim);font-size:13px;padding:20px 0">实盘 — 待开发（信号胜率+健康度雷达图+信号列表）</div>
          </div>
          <div v-else-if="store.detailTab==='indicators'">
            <div style="color:var(--c-text-dim);font-size:13px;padding:20px 0">指标 — 待开发（6张表状态+全量/增量操作）</div>
          </div>
        </template>
        <n-empty v-else description="选择一个模型版本" style="padding:60px 0" />
      </div>
    </div>
  </template>
</div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { NButton, NTag, NSpin, NEmpty } from 'naive-ui'
import { useModelStore } from '../stores/model'
import ModelTraining from './ModelTraining.vue'

const store = useModelStore()
const loading = ref(true)
const showCreate = ref(false)

const tabs = [
  { key:'basic', label:'基本信息' },
  { key:'train', label:'训练' },
  { key:'eval', label:'评估' },
  { key:'live', label:'实盘' },
  { key:'indicators', label:'指标' },
]

onMounted(async () => {
  await store.loadVersions()
  if (store.versions.length) store.selectVersion(store.versions[0].version)
  loading.value = false
})
</script>