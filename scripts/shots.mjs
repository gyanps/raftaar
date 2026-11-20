// Screenshots of the running app, taken by a headless Chrome of its own (never the browser you are using).
//
//   node scripts/shots.mjs out/plan.png "http://localhost:8000/?from=ISBT Kashmere Gate&to=Nehru Place Terminal&time=18:30" \
//        --wait "document.querySelector('.opt')" --settle 2500 --size 1440x900
//
// Needs Node 22+ (built-in WebSocket and fetch) and Google Chrome.
import { spawn } from 'node:child_process';
import { mkdirSync, mkdtempSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';

const args = process.argv.slice(2);
const flag = (name, dflt) => { const i = args.indexOf('--' + name); return i >= 0 ? args.splice(i, 2)[1] : dflt; };
const waitFor = flag('wait', 'true');
const settle = +flag('settle', '1500');
const [w, h] = flag('size', '1440x900').split('x').map(Number);
const scale = +flag('scale', '1');
const dark = flag('dark', '0') === '1';
const chromePath = flag('chrome', '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome');
const [out, url] = args;
if (!out || !url) { console.error('usage: node scripts/shots.mjs <out.png> <url> [--wait js] [--settle ms] [--size WxH] [--dark 1]'); process.exit(2); }

const port = 9300 + Math.floor(Math.random() * 400);
const chrome = spawn(chromePath, ['--headless=new', '--remote-debugging-port=' + port, '--user-data-dir=' + mkdtempSync(join(tmpdir(), 'raftaar-shots-')),
  '--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--hide-scrollbars', '--no-first-run', '--window-size=' + w + ',' + h, 'about:blank'], { stdio: 'ignore' });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

try {
  let target;
  for (let i = 0; i < 50 && !target; i++) {
    await sleep(200);
    target = await fetch('http://127.0.0.1:' + port + '/json/list').then((r) => r.json()).then((l) => l.find((t) => t.type === 'page')).catch(() => null);
  }
  if (!target) throw new Error('Chrome did not start');
  const ws = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
  let id = 0;
  const pending = new Map();
  ws.onmessage = (m) => { const d = JSON.parse(m.data); if (d.id && pending.has(d.id)) { pending.get(d.id)(d); pending.delete(d.id); } };
  const send = (method, params = {}) => new Promise((res) => { pending.set(++id, res); ws.send(JSON.stringify({ id, method, params })); });
  await send('Page.enable');
  await send('Emulation.setDeviceMetricsOverride', { width: w, height: h, deviceScaleFactor: scale, mobile: w < 700 });
  if (dark) await send('Emulation.setEmulatedMedia', { features: [{ name: 'prefers-color-scheme', value: 'dark' }] });
  await send('Page.navigate', { url });
  const deadline = Date.now() + 45000;
  for (;;) {
    const r = await send('Runtime.evaluate', { expression: '!!(' + waitFor + ')', returnByValue: true });
    if (r.result?.result?.value === true) break;
    if (Date.now() > deadline) { console.error('timed out waiting for: ' + waitFor); break; }
    await sleep(250);
  }
  await sleep(settle);
  const shot = await send('Page.captureScreenshot', { format: 'png' });
  mkdirSync(dirname(out), { recursive: true });
  writeFileSync(out, Buffer.from(shot.result.data, 'base64'));
  const errs = await send('Runtime.evaluate', { expression: 'window.__errors ? window.__errors.join(" | ") : ""', returnByValue: true });
  console.log('wrote ' + out + (errs.result?.result?.value ? '  page errors: ' + errs.result.result.value : ''));
  ws.close();
} finally {
  chrome.kill();
}
