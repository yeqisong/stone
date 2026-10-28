/**
 * 极简 Markdown 渲染（Agent 回答专用）——先转义再还原结构，防 HTML 注入。
 *
 * 支持：表格 / 标题(#~####) / 有序无序列表 / 代码块 / 行内代码 / 粗体 / 段落。
 * 表格兼容有外框与无外框（`| a | b |` 与 `a | b`）两种写法——模型输出不总是规范。
 */

export function escapeHtml(s) {
  return String(s ?? '')
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
}

function inline(s) {
  return escapeHtml(s)
    .replace(/`([^`\n]+)`/g, '<code class="agent-inline">$1</code>')
    .replace(/\*\*([^*\n]+)\*\*/g, '<b>$1</b>')
}

function splitRow(line) {
  let s = line.trim()
  if (s.startsWith('|')) s = s.slice(1)
  if (s.endsWith('|')) s = s.slice(0, -1)
  return s.split('|').map(c => c.trim())
}

const isTableRow = (l) => l.includes('|') && l.trim().length > 1
const isTableSep = (l) => {
  if (!l.includes('|') || !l.includes('-')) return false
  const cells = splitRow(l)
  return cells.length > 0 && cells.every(c => /^:?-{2,}:?$/.test(c.replace(/\s+/g, '')))
}

export function renderMarkdown(text) {
  const lines = String(text ?? '').split('\n')
  const out = []
  let i = 0
  while (i < lines.length) {
    const line = lines[i]

    // 代码块
    if (/^\s*```/.test(line)) {
      const buf = []
      i++
      while (i < lines.length && !/^\s*```/.test(lines[i])) buf.push(lines[i++])
      i++
      out.push(`<pre class="agent-code">${escapeHtml(buf.join('\n'))}</pre>`)
      continue
    }

    // 表格（当前行有竖线 + 下一行是分隔行）
    if (isTableRow(line) && i + 1 < lines.length && isTableSep(lines[i + 1])) {
      const head = splitRow(line)
      i += 2
      const rows = []
      while (i < lines.length && isTableRow(lines[i]) && !isTableSep(lines[i])) {
        rows.push(splitRow(lines[i])); i++
      }
      out.push('<div class="agent-table-wrap"><table class="agent-table"><thead><tr>' +
        head.map(h => `<th>${inline(h)}</th>`).join('') + '</tr></thead><tbody>' +
        rows.map(r => '<tr>' + r.map(c => `<td>${inline(c)}</td>`).join('') + '</tr>').join('') +
        '</tbody></table></div>')
      continue
    }

    // 标题
    const hm = line.match(/^(#{1,4})\s+(.*)$/)
    if (hm) {
      const lv = Math.min(hm[1].length, 4)
      out.push(`<div class="agent-h agent-h${lv}">${inline(hm[2])}</div>`)
      i++
      continue
    }

    // 列表（含缩进续行）
    if (/^\s*[-*]\s+/.test(line) || /^\s*\d+\.\s+/.test(line)) {
      const ordered = /^\s*\d+\.\s+/.test(line)
      const re = ordered ? /^\s*\d+\.\s+(.*)$/ : /^\s*[-*]\s+(.*)$/
      const items = []
      while (i < lines.length && re.test(lines[i])) {
        items.push(inline(lines[i].match(re)[1])); i++
        while (i < lines.length && /^\s{2,}\S/.test(lines[i]) && !re.test(lines[i])) {
          items[items.length - 1] += ' ' + inline(lines[i].trim()); i++
        }
      }
      out.push(`<${ordered ? 'ol' : 'ul'} class="agent-list">` +
        items.map(x => `<li>${x}</li>`).join('') + `</${ordered ? 'ol' : 'ul'}>`)
      continue
    }

    // 空行
    if (!line.trim()) { i++; continue }

    // 段落（连续普通行合并）
    const para = []
    while (i < lines.length && lines[i].trim() &&
           !/^\s*```/.test(lines[i]) && !isTableRow(lines[i]) &&
           !/^(#{1,4})\s+/.test(lines[i]) && !/^\s*[-*]\s+/.test(lines[i]) &&
           !/^\s*\d+\.\s+/.test(lines[i])) {
      para.push(lines[i]); i++
    }
    out.push(`<div class="agent-p">${inline(para.join('\n')).replace(/\n/g, '<br/>')}</div>`)
  }
  return out.join('')
}
