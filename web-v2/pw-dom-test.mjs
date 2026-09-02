import { createRequire } from 'module'
import fs from 'fs'
const require = createRequire('/home/bnbnyu/projects/stone/web-v2/package.json')
const { chromium } = require('playwright-core')
// 凭据
const env = fs.readFileSync('/home/bnbnyu/projects/stone/.env', 'utf8')
const get = k => (env.match(new RegExp('^' + k + '=(.*)$', 'm')) || [])[1]
const EXE = process.env.HOME + '/.cache/ms-playwright/chromium-1234/chrome-linux64/chrome'
const browser = await chromium.launch({ executablePath: EXE, headless: true, args: ['--no-sandbox'] })
const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } })
// 登录拿 token
const resp = await ctx.request.post('http://127.0.0.1:3000/api/login', { data: { username: get('LOGIN_USERNAME'), password: get('LOGIN_PASSWORD') } })
const { token } = await resp.json()
await ctx.addInitScript(t => localStorage.setItem('token', t), token)
const page = await ctx.newPage()
const errors = []
page.on('pageerror', e => errors.push('pageerror: ' + String(e).slice(0, 150)))
page.on('console', m => { if (m.type() === 'error') errors.push('console: ' + m.text().slice(0, 150)) })
await page.goto('http://127.0.0.1:3000/#/detail/688068')
try { await page.waitForSelector('.n-button-group', { timeout: 8000 }) } catch(e) { console.log('⚠ 组未出现') }
await page.waitForTimeout(500)
console.log('URL:', page.url())
console.log('token长度:', await page.evaluate(() => (localStorage.getItem('token')||'').length))
console.log('body前200字:', await page.evaluate(() => document.body.innerText.replace(/\n/g,'|').slice(0, 200)))
console.log('app内HTML前300字:', await page.evaluate(() => (document.getElementById('app')?.innerHTML || '').slice(0, 300)))
const info = await page.evaluate(() => {
  const groups = [...document.querySelectorAll('.n-button-group')]
  return groups.map(g => ({
    cls: g.className,
    kids: [...g.children].map(c => ({
      text: c.textContent.trim().slice(0, 6),
      radius: getComputedStyle(c).borderRadius,
    })),
  }))
})
console.log('页面组数量:', info.length)
info.forEach((g, i) => console.log(`组${i} [${g.cls}]:`, g.kids.map(k => `${k.text}(radius=${k.radius})`).join(' | ')))
console.log('页面错误:', errors.slice(0, 4))
await browser.close()
