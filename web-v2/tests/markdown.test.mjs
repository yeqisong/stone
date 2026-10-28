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
