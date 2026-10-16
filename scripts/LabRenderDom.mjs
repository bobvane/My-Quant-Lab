// LabRenderDom.mjs — 零新增依赖地抓取「执行后」的 DOM（供 Test-WebUi.ps1 渲染检查）。
// 复用 Test-WebUi.ps1 已启动的 headless Chrome 调试端口，CDP(Runtime) 等待应用挂载后
// 取 document.documentElement.outerHTML。Node 内置全局 WebSocket，无需任何新增包。
const args = process.argv.slice(2);
const opt = {};
for (let i = 0; i < args.length; i += 2) opt[args[i]] = args[i + 1];
const PORT = opt['--debugPort'];
const URL = opt['--url'];
const WAIT_MS = parseInt(opt['--waitMs'] || '15000', 10);
const WAIT_FOR = opt['--waitFor'] || 'My Quant Lab';

function sleep(ms){ return new Promise(r => setTimeout(r, ms)); }

async function openTab(port, url){
  const r = await fetch('http://127.0.0.1:' + port + '/json/new?' + url, { method: 'PUT' });
  const j = await r.json();
  return j.webSocketDebuggerUrl;
}

function makeCdp(wsUrl){
  const ws = new WebSocket(wsUrl);
  let nextId = 0;
  const pending = new Map();
  const events = [];
  ws.addEventListener('message', (ev) => {
    let obj; try { obj = JSON.parse(ev.data.toString()); } catch { return; }
    if (obj.id !== undefined && obj.id !== null) {
      const tcs = pending.get(obj.id);
      if (tcs) { pending.delete(obj.id); tcs.resolve(obj); }
    } else { events.push(obj); }
  });
  const ready = new Promise((res, rej) => {
    ws.addEventListener('open', () => res());
    ws.addEventListener('error', () => rej(new Error('ws error')));
  });
  function send(method, params){
    const id = ++nextId;
    return new Promise((resolve, reject) => {
      pending.set(id, { resolve, reject });
      ws.send(JSON.stringify({ id, method, params: params || {} }));
      setTimeout(() => { if (pending.has(id)) { pending.delete(id); reject(new Error('timeout ' + method)); } }, 30000);
    });
  }
  function evalExpr(expr){
    return send('Runtime.evaluate', { expression: expr, returnByValue: true, awaitPromise: true })
      .then(r => {
        if (r.result && r.result.exceptionDetails) throw new Error('eval exc ' + JSON.stringify(r.result.exceptionDetails));
        return r.result.result.value;
      });
  }
  return { ws, ready, send, evalExpr };
}

(async () => {
  if (!PORT || !URL) throw new Error('缺少 --debugPort 或 --url');
  const ws = await openTab(PORT, 'about:blank');
  const cdp = makeCdp(ws);
  await cdp.ready;
  await cdp.send('Page.enable', {});
  await cdp.send('Runtime.enable', {});
  await cdp.send('Page.navigate', { url: URL });
  const deadline = Date.now() + WAIT_MS;
  let ok = false;
  while (Date.now() < deadline) {
    try {
      const t = await cdp.evalExpr('document.body.innerText');
      if (t && typeof t === 'string' && t.indexOf(WAIT_FOR) >= 0) { ok = true; break; }
    } catch {}
    await sleep(150);
  }
  if (!ok) throw new Error('渲染超时：' + WAIT_MS + 'ms 内未出现「' + WAIT_FOR + '」');
  await sleep(600);
  const html = await cdp.evalExpr('document.documentElement.outerHTML');
  process.stdout.write(html);
  process.exit(0);
})().catch(e => { console.error('RENDER-ERROR: ' + e.message); process.exit(2); });
