// LabRaceDriver.mjs — 零新增依赖的真实浏览器 Lab 导航/竞态冒烟。
// 使用 Chrome DevTools Protocol（Fetch 拦截）在「网络层」确定性地让 A 的详情响应
// 晚于 B 回到前端，验证 runRequestSeq 守卫丢弃陈旧 A。不依赖 Playwright 等任何新增包。
// 由 Test-WebUi.ps1 在已拉起 headless Chrome 后调用。
//
// 用法: node LabRaceDriver.mjs --debugPort <port> --frontend <url> --backend <url> --runA <id> --runB <id>
// 退出码: 0=全部通过, 1=存在未通过的检查, 2=运行异常

function sleep(ms){ return new Promise(r => setTimeout(r, ms)); }
function hostOf(u){ try { return new URL(u).host; } catch { return 'localhost:5173'; } }

const args = process.argv.slice(2);
const opt = {};
for (let i = 0; i < args.length; i += 2) opt[args[i]] = args[i + 1];
const DEBUG_PORT = opt['--debugPort'];
const FRONTEND = opt['--frontend'];
const BACKEND = opt['--backend'];
const RUN_A = opt['--runA'];
const RUN_B = opt['--runB'];
const DETAIL_PREFIX = '这次研究的问题：';

async function openTab(port, url){
  // 注意：含 '?' 的地址（如 /lab?run=1）不要放这里，请用 Page.navigate 触发，
  // 以避免 /json/new 把 '?' 当作端点自身的查询分隔符。
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
  function waitEvent(method, urlContains, timeoutMs){
    const deadline = Date.now() + timeoutMs;
    return new Promise((resolve) => {
      const tick = () => {
        for (let i = 0; i < events.length; i++){
          const ev = events[i];
          let ok = (!method || ev.method === method);
          if (ok && urlContains){
            const p = ev.params;
            const u = (p && p.request && p.request.url) || '';
            if (u.indexOf(urlContains) < 0) ok = false;
          }
          if (ok){ events.splice(i, 1); resolve(ev); return; }
        }
        if (Date.now() < deadline) setTimeout(tick, 25); else resolve(null);
      };
      tick();
    });
  }
  function evalExpr(expr){
    return send('Runtime.evaluate', { expression: expr, returnByValue: true, awaitPromise: true })
      .then(r => {
        if (r.result && r.result.exceptionDetails) throw new Error('eval exc ' + JSON.stringify(r.result.exceptionDetails));
        return r.result.result.value;
      });
  }
  async function waitDetail(marker, timeoutMs){
    const deadline = Date.now() + timeoutMs;
    while (Date.now() < deadline){
      try { const t = await evalExpr('document.body.innerText'); if (t && t.indexOf(marker) >= 0) return true; } catch {}
      await sleep(100);
    }
    return false;
  }
  async function waitUrl(runId, timeoutMs){
    const deadline = Date.now() + timeoutMs;
    while (Date.now() < deadline){
      try { const s = await evalExpr('location.search'); if (s && s.indexOf('run=' + runId) >= 0) return true; } catch {}
      await sleep(100);
    }
    return false;
  }
  return { ws, ready, send, waitEvent, evalExpr, waitDetail, waitUrl };
}

async function getQuestion(backend, runId){
  const r = await fetch(backend + '/api/v1/ai/research/' + runId, { method: 'GET' });
  if (!r.ok) throw new Error('后端 run ' + runId + ' 不可用: HTTP ' + r.status);
  const j = await r.json();
  if (!j.question) throw new Error('后端 run ' + runId + ' 缺少 question 字段');
  return j.question;
}

function log(s){ console.log(s); }

// ADR-221 场景：通过 history.pushState + popstate 触发 openRun(B)，绕过「打开」按钮的
// :disabled，模拟「你正在看 A 时收到别人发来的 /lab?run=B 链接」。
function popExpr(runB){
  return "(function(){ history.pushState({m:1}, '', '/lab?run=" + runB + "'); window.dispatchEvent(new PopStateEvent('popstate',{state:{m:1}})); return location.search; })()";
}

(async () => {
  const markerA = await getQuestion(BACKEND, RUN_A);
  const markerB = await getQuestion(BACKEND, RUN_B);
  const detailA = DETAIL_PREFIX + markerA;
  const detailB = DETAIL_PREFIX + markerB;
  log('markers: A="' + markerA + '" B="' + markerB + '"');

  // ---- 测试 1：普通导航冒烟（先打开 A，再切到 B，无网络拦截）----
  const navWs = await openTab(DEBUG_PORT, FRONTEND);
  const nav = makeCdp(navWs);
  await nav.ready;
  await nav.send('Page.enable', {});
  await nav.send('Runtime.enable', {});
  await nav.send('Page.navigate', { url: FRONTEND + '/lab?run=' + RUN_A });
  let t1 = false;
  try {
    const aOk = await nav.waitDetail(detailA, 15000);
    if (!aOk) throw new Error('A 详情面板未渲染');
    const pop = await nav.evalExpr(popExpr(RUN_B));
    const bOk = await nav.waitDetail(detailB, 15000);
    const urlOk = await nav.waitUrl(RUN_B, 5000);
    t1 = bOk && urlOk;
    log('[导航冒烟] A渲染=' + aOk + ' popstate=' + pop + ' B渲染=' + bOk + ' URL含run=' + RUN_B + '=' + urlOk + ' => ' + (t1 ? 'PASS' : 'FAIL'));
  } catch (e) { log('[导航冒烟] FAIL: ' + e.message); }

  // ---- 测试 2：乱序竞态（确定性，靠 Fetch 拦截，非 sleep 伪造）----
  const HOST = hostOf(FRONTEND);
  const raceWs = await openTab(DEBUG_PORT, 'about:blank');
  const race = makeCdp(raceWs);
  await race.ready;
  await race.send('Page.enable', {});
  await race.send('Runtime.enable', {});
  await race.send('Fetch.enable', { patterns: [
    { urlPattern: '*://' + HOST + '/api/v1/ai/research/' + RUN_A }
  ] });
  // Fetch 已生效后再导航，确保 A 的详情请求被拦截（而非在加载前就完成）
  await race.send('Page.navigate', { url: FRONTEND + '/lab?run=' + RUN_A });
  const aPaused = await race.waitEvent('Fetch.requestPaused', '/api/v1/ai/research/' + RUN_A, 20000);
  let t2 = false;
  if (!aPaused) {
    log('[竞态] FAIL: A 的详情请求未被拦截（页面可能已加载完成，需调整时机）');
  } else {
    const aReqId = aPaused.params.requestId;
    log('[竞态] A 详情请求已暂停 reqId=' + aReqId + '（A 仍卡在网络层，尚未进入前端）');
    await sleep(500); // 让 openRun(A) 设好 seq，A 响应暂扣
    await race.evalExpr(popExpr(RUN_B)); // 触发 openRun(B)
    const bOk = await race.waitDetail(detailB, 15000); // B 先到达并应用
    const urlOk = await race.waitUrl(RUN_B, 5000);
    await race.send('Fetch.continueRequest', { requestId: aReqId }); // 现在才放行迟到的 A
    await sleep(2500); // 让陈旧 A 回前端后判定 runRequestSeq 是否丢弃
    const afterDetail = await race.evalExpr('document.body.innerText');
    const afterUrl = await race.evalExpr('location.search');
    const stillB = afterDetail.includes(detailB);
    const hasA = afterDetail.includes(detailA);
    const urlStillB = afterUrl.includes('run=' + RUN_B);
    t2 = bOk && urlOk && stillB && !hasA && urlStillB;
    log('[竞态] B渲染=' + bOk + ' URL含run=' + RUN_B + '=' + urlOk + ' 释放A后详情仍=B=' + stillB + ' 详情含A=' + hasA + ' URL仍=B=' + urlStillB + ' => ' + (t2 ? 'PASS' : 'FAIL'));
  }

  log('RESULT: ' + ((t1 && t2) ? 'PASS' : 'FAIL'));
  process.exit((t1 && t2) ? 0 : 1);
})().catch(e => { log('ERROR: ' + e.message); process.exit(2); });
