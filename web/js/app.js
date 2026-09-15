// app.js — 入口：后端健康状态 + SSE 实时推送（降级轮询），SSE 只刷新当前页
async function initHealth() {
  try {
    const m = await api('/api/mode');
    window.__mode = m.mode;
    modeNote = m.note || '';
    const modeTag = document.getElementById('modeTag');
    if (modeTag) modeTag.textContent = MODE_NOTE[m.mode] + (modeNote && modeNote !== '后端已配置' ? `（${modeNote}）` : '');

    // 侧栏迷你状态灯：全部正常=绿，部分不可达=黄，全挂=红
    const items = [
      ['分镜 LLM', m.llm_up], ['文生图', m.image_up], ['图生视频', m.wan_up],
      ['图生图', m.edit_up], ['语音合成', m.tts_up]
    ];
    const upN = items.filter(([, up]) => up).length;
    const mini = document.getElementById('backendMini');
    const text = document.getElementById('backendText');
    if (mini && text) {
      mini.classList.remove('ok', 'bad', 'warn');
      if (upN === items.length) { mini.classList.add('ok'); text.textContent = '后端正常'; }
      else if (upN === 0) { mini.classList.add('bad'); text.textContent = '后端全部离线'; }
      else { mini.classList.add('warn'); text.textContent = `后端 ${upN}/${items.length}`; }
    }

    // 配置页内的详细徽章（仅当该页渲染过时存在）
    const h = document.getElementById('health');
    if (h) h.innerHTML = items.map(([name, up]) =>
      '<span class="chip ' + (up ? 'up' : 'down') + '"><i></i>' + name + (up ? '' : ' 不可达') + '</span>'
    ).join('');
  } catch (e) {
    const mini = document.getElementById('backendMini');
    const text = document.getElementById('backendText');
    if (mini && text) {
      mini.classList.remove('ok', 'warn');
      mini.classList.add('bad');
      text.textContent = '状态未知';
    }
  }
}

// ---------------- SSE 实时推送 ----------------
// 后端数据变更时推 {"v": 版本号, "what": 类型}，前端收到后只刷新当前路由页。
// SSE 断连或不可用时自动退回 3 秒轮询（体验不降级，只是稍慢）。
let sse = null;
let sseOk = false;
let pollTimer = null;

function startPolling() {
  if (pollTimer) return;
  console.warn('[sse] 不可用，退回 3s 轮询');
  pollTimer = setInterval(() => { if (!document.hidden) refreshCurrent(); }, 3000);
}

function stopPolling() {
  if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
}

function connectSSE() {
  if (!window.EventSource) { startPolling(); return; }
  sse = new EventSource('/api/events');
  sse.onopen = () => { sseOk = true; stopPolling(); };
  sse.onmessage = (ev) => {
    if (document.hidden) return;              // 后台标签页不刷新，回来靠 visibilitychange 对齐
    refreshCurrent();                          // 轻量事件 -> 只刷当前页
    if (ev.data && ev.data.includes('"task"')) initHealth();  // 任务事件顺带刷新健康状态
  };
  sse.onerror = () => {
    // EventSource 自动重连；重连期间退回轮询保底
    sseOk = false;
    startPolling();
  };
}

// 页面可见性变化：SSE 正常时回来对齐刷一次；轮询模式下确保轮询在跑
document.addEventListener('visibilitychange', () => {
  if (!document.hidden) {
    refreshCurrent();
    if (!sseOk) startPolling();
  }
});

initHealth();
renderRoute();
connectSSE();
