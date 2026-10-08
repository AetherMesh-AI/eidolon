import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'
import os from 'node:os'
import http from 'node:http'
import { createRequire } from 'node:module'
import { _electron } from '@playwright/test'

const require = createRequire(path.resolve('apps/desktop/package.json'))
const executablePath = require('electron')
const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'eidolon-error-ui-'))
const html = fs.readFileSync('scripts/desktop-update/ui.html')
const message = 'The new app was built, but could not replace the installed app. The previous app was kept. To finish, quit Eidolon and use Finder to replace /Applications/Eidolon.app with /Users/example/.eidolon/desktop-source/checkout-example/apps/desktop/release/mac-arm64/Eidolon.app, approving any macOS prompt.'
const server = http.createServer((req, res) => {
  if (req.url === '/progress') {
    res.setHeader('Content-Type', 'application/json')
    res.end(JSON.stringify({status: 'error', message}))
  } else { res.setHeader('Content-Type', 'text/html'); res.end(html) }
})
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve))
const url = `http://127.0.0.1:${server.address().port}/`
fs.writeFileSync(path.join(tmp, 'package.json'), JSON.stringify({name:'eidolon-error-ui-fixture',main:'main.cjs'}))
fs.writeFileSync(path.join(tmp, 'main.cjs'), `const {app,BrowserWindow}=require('electron'); app.whenReady().then(()=>{let win=new BrowserWindow({width:280,height:320,frame:false});win.loadURL(${JSON.stringify(url)});});app.on('window-all-closed',()=>app.quit());`)
fs.mkdirSync('replacement-evidence', {recursive:true})
let app
try {
  app = await _electron.launch({executablePath,args:[tmp],env:{...process.env},timeout:30000})
  const page = await app.firstWindow()
  await page.waitForFunction(() => document.body.className === 'error')
  assert.ok((await page.locator('#line').textContent()).startsWith(message))
  assert.equal(await page.locator('#line').locator('*').count(), 0)
  const geometry = await page.locator('#line').evaluate(el => {
    const rect = el.getBoundingClientRect()
    const style = getComputedStyle(el)
    el.scrollTop = el.scrollHeight
    return {x:rect.x,y:rect.y,right:rect.right,bottom:rect.bottom,width:innerWidth,height:innerHeight,
      clientWidth:el.clientWidth,scrollWidth:el.scrollWidth,clientHeight:el.clientHeight,scrollHeight:el.scrollHeight,
      scrollTop:el.scrollTop,userSelect:style.userSelect}
  })
  assert.ok(geometry.x >= 0 && geometry.y >= 0)
  assert.ok(geometry.right <= geometry.width && geometry.bottom <= geometry.height)
  assert.equal(geometry.scrollWidth, geometry.clientWidth)
  assert.ok(geometry.scrollTop > 0, 'Long recovery details must be scrollable')
  assert.equal(geometry.userSelect, 'text')
  await page.screenshot({path:'replacement-evidence/error-recovery-ui.png'})
  fs.writeFileSync('replacement-evidence/error-recovery-ui.json',JSON.stringify(geometry,null,2))
  console.log(JSON.stringify(geometry))
} finally {
  await app?.close().catch(()=>{})
  await new Promise(resolve => server.close(resolve))
  fs.rmSync(tmp,{recursive:true,force:true})
}
