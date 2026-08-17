// 北京时间日期工具
// 不能用 Intl.DateTimeFormat('zh-CN')——其 format 输出中文"年月日"文本；
// 也不能用 new Date().toISOString()（UTC，北京 0-8 点会取到昨天）。
// 统一用 en-US + timeZone 转出北京时间字符串再解析。
export function bjDateStr(d = new Date()) {
  const t = new Date(d.toLocaleString('en-US', { timeZone: 'Asia/Shanghai' }))
  const m = String(t.getMonth() + 1).padStart(2, '0')
  const day = String(t.getDate()).padStart(2, '0')
  return `${t.getFullYear()}-${m}-${day}`
}

// 北京时间精确到日（YYYY-MM-DD）
export const bjToday = () => bjDateStr()
