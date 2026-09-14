// app.js — 入口：健康状态初始化 + SSE 实时推送（降级轮询）
// 初始化：拉模式与健康状态
async function initHealth() {
  try {
    const m = await api('/api/mode');
    window.__mode = m.mode;
    modeNote = m.note || '';
    document.getElementById('modeTag').textContent = MODE_NOTE[m.mode] + (modeNote && modeNote !== '后端已配置' ? `（${modeNote}）` : '');
    const h = document.getElementById('health');
    const items = [
      ['分镜 LLM', m.llm_up], ['文生图', m.image_up], ['图生视频', m.wan_up],
      ['图生图', m.edit_up], ['语音合成', m.tts_up]
    ];
    h.innerHTML = items.map(([name, up]) =>
      '<span class="chip ' + (up ? 'up' : 'down') + '"><i></i>' + name + (up ? '' : ' 不可达') + '</span>'
    ).join('');
  } catch (e) {
    document.getElementById('health').innerHTML = '<span class="chip down"><i></i>后端状态未知</span>';
  }
}

// ---------------- SSE 实时推送 ----------------
// 后端数据变更时推 {"v": 版本号, "what": 类型}，前端收到后拉一次 /api/projects 做差分渲染。
// SSE 断连或不可用时自动退回 3 秒轮询（体验不降级，只是稍慢）。
let sse = null;
let sseOk = false;
let pollTimer = null;

function startPolling() {
  if (pollTimer) return;
  console.warn('[sse] 不可用，退回 3s 轮询');
  pollTimer = setInterval(() => { if (!document.hidden) refresh(); }, 3000);
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
    refresh();                                 // 轻量事件 -> 拉一次全量做差分
    if (ev.data && ev.data.includes('"task"')) initHealth();  // 任务事件顺带刷新健康徽章
  };
  sse.onerror = () => {
    // EventSource 自动重连；重连期间退回轮询保底
    sseOk = false;
    startPolling();
  };
}

// 页面可见性变化：SSE 正常时回来对齐拉一次；轮询模式下确保轮询在跑
document.addEventListener('visibilitychange', () => {
  if (!document.hidden) {
    refresh();
    if (!sseOk) startPolling();
  }
});

initHealth();
refresh();
connectSSE();
