/**
 * 状态映射与数字格式 — 全站单点维护。
 *
 * 用法：import { STATUS, fmtMoney, fmtPct } from '../utils/ui'
 * 颜色一律引用主题语义变量（App.vue 注入的 --c-*），深浅色自动适配。
 */

// 任务/节点状态 → { 文案, 色 }；未知状态灰色兜底
const STATUS_DEF = {
  running:   { label: '进行中', color: 'var(--c-info)' },
  success:   { label: '成功',   color: 'var(--c-success)' },
  completed: { label: '完成',   color: 'var(--c-success)' },
  failed:    { label: '失败',   color: 'var(--c-error)' },
  pending:   { label: '等待中', color: 'var(--c-text-faint)' },
  cancelled: { label: '已取消', color: 'var(--c-text-faint)' },
  terminated:{ label: '已终止', color: 'var(--c-text-faint)' },
}
export function statusOf(s) {
  return STATUS_DEF[s] || { label: s || '未知', color: 'var(--c-text-faint)' }
}

// A 股行情语义：涨红跌绿（质量评价用 success/error，勿混用）
export const UP_COLOR = '#ef4444'
export const DOWN_COLOR = '#10b981'

// 金额缩写：≥1e8 → X.XX亿；≥1e4 → X.XX万；否则千分位
export function fmtMoney(v, d = 2) {
  if (v == null || isNaN(v)) return '—'
  const n = Number(v)
  const abs = Math.abs(n)
  if (abs >= 1e8) return (n / 1e8).toFixed(d) + '亿'
  if (abs >= 1e4) return (n / 1e4).toFixed(d) + '万'
  return n.toLocaleString('zh-CN', { maximumFractionDigits: d })
}

// 百分比：0.0523 → '5.23%'（传入已是比例值）
export function fmtPct(v, d = 2) {
  if (v == null || isNaN(v)) return '—'
  return (Number(v) * 100).toFixed(d) + '%'
}

// 数字千分位（保留 d 位小数）
export function fmtNum(v, d = 2) {
  if (v == null || isNaN(v)) return '—'
  return Number(v).toLocaleString('zh-CN', { minimumFractionDigits: d, maximumFractionDigits: d })
}
