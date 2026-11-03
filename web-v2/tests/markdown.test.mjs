/**
 * Markdown 渲染单元测试（Node 内置 test runner，无额外依赖）：
 *   node --test web-v2/tests/
 */
import test from 'node:test'
import assert from 'node:assert/strict'
import { renderMarkdown, escapeHtml } from '../src/utils/markdown.js'

test('表格渲染（有外框）', () => {
  const md = '| 项目 | 值 |\n| --- | --- |\n| 收盘 | 1355.9 |'
  const html = renderMarkdown(md)
  assert.ok(html.includes('<table class="agent-table"'))
  assert.ok(html.includes('<th>项目</th>'))
  assert.ok(html.includes('<td>1355.9</td>'))
  assert.ok(html.includes('agent-table-wrap'))
})

test('表格渲染（无外框竖线）', () => {
  const md = '项目 | 值\n--- | ---\na | 1'
  assert.ok(renderMarkdown(md).includes('<table'))
})

test('标题 / 列表 / 代码块 / 粗体', () => {
  const md = '### 小标题\n\n- a\n- b\n\n1. one\n2. two\n\n```sql\nSELECT 1;\n```\n\n**重点**'
  const html = renderMarkdown(md)
  assert.ok(html.includes('agent-h3'))
  assert.ok(html.includes('<ul class="agent-list"><li>a</li><li>b</li></ul>'))
  assert.ok(html.includes('<ol class="agent-list"><li>one</li><li>two</li></ol>'))
  assert.ok(html.includes('<pre class="agent-code">SELECT 1;</pre>'))
  assert.ok(html.includes('<b>重点</b>'))
})

test('HTML 注入被转义', () => {
  const html = renderMarkdown('<script>alert(1)</script>\n\n<img src=x onerror=1>')
  assert.ok(!html.includes('<script>'))
  assert.ok(!html.includes('<img'))
  assert.ok(html.includes('&lt;script&gt;'))
})

test('escapeHtml 处理引号与尖括号', () => {
  assert.equal(escapeHtml('<a href="x">'), '&lt;a href=&quot;x&quot;&gt;')
})

test('空输入不炸', () => {
  assert.equal(renderMarkdown(''), '')
  assert.equal(renderMarkdown(null), '')
})

// ── 股票代码链接化 ──
import { linkifyStockCodes, sqlResultToMarkdown } from '../src/utils/markdown.js'

test('代码链接化：普通文本中的 6 位代码', () => {
  const html = linkifyStockCodes('<div>关注 600519 和 000858 两只</div>')
  assert.ok(html.includes('data-code="600519"'))
  assert.ok(html.includes('data-code="000858"'))
  assert.equal((html.match(/agent-stock-link/g) || []).length, 2)
})

test('代码链接化：跳过 pre/code 内部', () => {
  const html = linkifyStockCodes('<pre>600519</pre><code>000858</code><div>600519</div>')
  assert.equal((html.match(/agent-stock-link/g) || []).length, 1)
})

test('代码链接化：7 位数字不误伤', () => {
  const html = linkifyStockCodes('<div>编号 6005199 不是代码</div>')
  assert.equal((html.match(/agent-stock-link/g) || []).length, 0)
})

// ── SQL 结果表格化 ──
test('SQL 结果转表格', () => {
  const preview = JSON.stringify({ columns: ['trade_date', 'close'], rows: [['2026-11-03', 1355.9], ['2026-10-31', 1340.2]], row_count: 2 })
  const md = sqlResultToMarkdown(preview)
  assert.ok(md.includes('| trade_date | close |'))
  assert.ok(md.includes('| 2026-11-03 | 1355.9 |'))
})

test('SQL 结果超过上限加提示', () => {
  const rows = Array.from({ length: 20 }, (_, i) => [i, i * 2])
  const md = sqlResultToMarkdown(JSON.stringify({ columns: ['a', 'b'], rows }), 5)
  assert.ok(md.includes('共 20 行'))
  assert.equal((md.match(/\| \d+ \|/g) || []).length, 5)
})

test('SQL 结果非表格返回 null', () => {
  assert.equal(sqlResultToMarkdown('{"error":"x"}'), null)
  assert.equal(sqlResultToMarkdown('not json'), null)
})
