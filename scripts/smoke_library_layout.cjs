// Render only a synthetic, offline library page in an isolated offscreen window.
const { app, BrowserWindow } = require('electron');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const root = path.resolve(__dirname, '..');
const home = path.join(root, 'build', 'library-layout-smoke-' + Date.now());
fs.mkdirSync(home, { recursive: true });
app.setPath('userData', home);
app.whenReady().then(async () => {
  const win = new BrowserWindow({ width: 1440, height: 900, useContentSize: true, show: false,
    webPreferences: { offscreen: true, contextIsolation: true, nodeIntegration: false, sandbox: true } });
  try {
    let html = fs.readFileSync(path.join(root, 'easyread/web/library.html'), 'utf8')
      .replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi, '')
      .replace(/<link\b[^>]*href="(\/web\/css\/[^"?]+)"[^>]*>/gi, (_m, css) => '<style>' + fs.readFileSync(path.join(root, 'easyread', css.slice(1)), 'utf8') + '</style>')
      .replace(/<link\b[^>]*>/gi, '')
      .replace('<html lang="zh-CN">', '<html lang="zh-TW" data-theme="dark">');
    await win.loadURL('data:text/html;charset=utf-8,' + encodeURIComponent(html));
    await win.webContents.executeJavaScript(`
      document.getElementById('list').innerHTML = Array.from({length:80}, (_,i) => '<div class="row"><div class="thumb blank"></div><div><div class="t1">Synthetic academic paper about measurement precision ' + i + '</div><div class="t2">用於檢查畫面尺寸的合成論文</div></div><div class="side-info">Reading</div></div>').join('');
      document.getElementById('detail').innerHTML = '<div class="detail-inner"><h2>Paper details</h2><p>Independent detail scrolling.</p></div>';
    `);
    for (const [width, height] of [[1440,900], [960,680], [1920,1080]]) {
      win.setContentSize(width, height);
      for (const detail of [false, true]) {
        await win.webContents.executeJavaScript(`document.querySelector('.lib').classList.toggle('has-detail', ${detail}); new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)))`);
        const size = await win.webContents.executeJavaScript(`(() => {
          const r = document.documentElement, main = document.querySelector('.main');
          return {width:r.clientWidth,height:r.clientHeight,scrollWidth:r.scrollWidth,scrollHeight:r.scrollHeight, mainHeight:main.clientHeight,mainScroll:main.scrollHeight,colorScheme:getComputedStyle(r).colorScheme};
        })()`);
        console.log(JSON.stringify({viewport:[width,height],detail,...size}));
        assert.ok(size.scrollWidth <= size.width, 'library must not add a document horizontal scrollbar');
        assert.ok(size.scrollHeight <= size.height, 'library must not add a document vertical scrollbar');
        assert.ok(size.mainScroll > size.mainHeight, 'long paper lists must still scroll inside the main pane');
        assert.equal(size.colorScheme, 'dark', 'native scrollbars must follow the dark theme');
      }
    }
    console.log('PASS: isolated library rendering at three viewport sizes, details open/closed, with internal list scrolling');
    win.destroy(); app.exit(0);
  } catch (error) {
    console.error(error); win.destroy(); app.exit(1);
  }
});
