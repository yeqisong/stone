const { JSDOM } = require('jsdom')
const dom = new JSDOM('<!DOCTYPE html><html><body><div id="app"></div></body></html>', { url: 'http://localhost/' })
global.window = dom.window
global.document = dom.window.document
global.navigator = dom.window.navigator
global.SVGElement = dom.window.SVGElement
global.requestAnimationFrame = cb => setTimeout(cb, 0)
global.cancelAnimationFrame = id => clearTimeout(id)
global.ResizeObserver = class { observe(){} unobserve(){} disconnect(){} }
global.Image = dom.window.Image
global.CustomEvent = dom.window.CustomEvent
global.Element = dom.window.Element
global.HTMLElement = dom.window.HTMLElement
global.Node = dom.window.Node
global.Text = dom.window.Text
global.SVGElement = dom.window.SVGElement
global.MutationObserver = dom.window.MutationObserver
global.getComputedStyle = dom.window.getComputedStyle
global.matchMedia = () => ({ matches: false, media: '', addListener(){}, removeListener(){}, addEventListener(){}, removeEventListener(){} })

const { createApp, h, nextTick } = require('vue')
const { NConfigProvider, NSpace, NButtonGroup, NButton } = require('naive-ui')

const PERIODS = [{v:'day',t:'日'},{v:'week',t:'周'},{v:'month',t:'月'}]
const RANGES = [{t:'1月'},{t:'2年'},{t:'全部'}]
const app = createApp(() => h(NConfigProvider, null, () => h(NSpace, { align: 'center', wrap: false }, () => [
  h(NButtonGroup, { size: 'tiny' }, () => PERIODS.map(p =>
    h(NButton, { size: 'tiny', type: p.v==='day'?'primary':'default' }, () => p.t))),
  h(NButtonGroup, { size: 'tiny' }, () => RANGES.map((r,i) =>
    h(NButton, { size: 'tiny', type: i===0?'primary':'default' }, () => r.t))),
  h(NButtonGroup, { size: 'tiny' }, () => [
    h(NButton, { size: 'tiny' }, () => '上一只'),
    h(NButton, { size: 'tiny' }, () => '下一只'),
  ]),
])))
app.mount(document.getElementById('app'))
nextTick(() => {
  const groups = document.querySelectorAll('.n-button-group')
  console.log('组数量:', groups.length)
  groups.forEach((g, i) => {
    const kids = [...g.children]
    console.log(`组${i}: 子元素=${kids.length}, 圆角类检查:`)
    kids.forEach((c, j) => {
      const br = getComputedStyle ? null : null
      console.log(`   [${j}] ${c.textContent} firstChild=${c===g.firstElementChild} lastChild=${c===g.lastElementChild}`)
    })
  })
  // naive 挂载的塌角样式文本
  const styles = [...document.querySelectorAll('style')].map(s => s.textContent).join('\n')
  const idx = styles.indexOf('n-button-group')
  console.log('塌角规则挂载(含空格版):', styles.includes('border-top-right-radius: 0!important'))
  console.log('button-group 样式片段:', idx >= 0 ? styles.slice(idx, idx + 400) : '【未找到 button-group 样式】')
  console.log('全部 style 标签数:', document.querySelectorAll('style').length)
  process.exit(0)
})
