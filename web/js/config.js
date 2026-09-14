// config.js — 模型后端配置面板
// ---------------- 模型后端配置面板 ----------------
const CFG_LABELS = {
  llm: '剧本分镜（LLM）', image: '文生图（首帧/定妆）', edit: '图生图（角色一致性）',
  video: '图生视频（I2V）', tts: '语音合成（TTS）'
};

async function openConfig() {
  document.getElementById('configModal').classList.add('on');
  await renderConfig();
}

async function renderConfig(testState) {
  let cfg;
  try { cfg = await api('/api/config'); } catch (e) { toast('读取配置失败：' + e.message, true); return; }
  const body = document.getElementById('configBody');
  body.innerHTML = Object.entries(CFG_LABELS).map(([g, label]) => {
    const c = cfg[g] || {};
    const dot = testState && testState[g] !== undefined
      ? `<span class="cfg-dot ${testState[g].ok ? 'up' : 'down'}" title="${testState[g].ok ? '连通' : (testState[g].error || '不可达')}"></span>`
      : '';
    return `<div class="cfg-group">
      <div class="cfg-head"><b>${dot}${label}</b></div>
      <div class="cfg-grid">
        <label>服务地址</label><input id="cfg-${g}-url" value="${(c.url || '').replace(/"/g, '&quot;')}" placeholder="http://host:port">
        <label>模型名</label><input id="cfg-${g}-model" value="${(c.model || '').replace(/"/g, '&quot;')}" placeholder="模型名称">
        <label>API Key</label>
        <div class="cfg-key-wrap">
          <input id="cfg-${g}-key" type="password" placeholder="${c.key_set ? '已设置，留空不修改' : '未设置'}">
          <span class="cfg-key-mask">${c.key_mask || ''}</span>
        </div>
      </div>
    </div>`;
  }).join('');
}

async function saveConfig() {
  const data = {};
  for (const g of Object.keys(CFG_LABELS)) {
    data[g] = {
      url: document.getElementById(`cfg-${g}-url`).value.trim(),
      model: document.getElementById(`cfg-${g}-model`).value.trim(),
      key: document.getElementById(`cfg-${g}-key`).value.trim(),  // 空 = 不修改
    };
  }
  try {
    await api('/api/config', { method: 'PUT', body: JSON.stringify(data) });
    toast('配置已保存并生效');
    document.getElementById('configModal').classList.remove('on');
    initHealth();  // 立刻按新配置刷新健康状态
  } catch (e) { toast('保存失败：' + e.message, true); }
}

async function testAllBackends() {
  const btn = document.getElementById('btnTestCfg');
  btn.disabled = true; btn.textContent = '测试中…';
  const state = {};
  await Promise.all(Object.keys(CFG_LABELS).map(async g => {
    // 先把当前输入框的值临时保存再测（不保存 key 为空时的语义冲突：测试用已存配置）
    try {
      const r = await api('/api/config/test', { method: 'POST', body: JSON.stringify({ group: g }) });
      state[g] = r;
    } catch (e) { state[g] = { ok: false, error: e.message }; }
  }));
  btn.disabled = false; btn.textContent = '测试连通性';
  await renderConfig(state);
  const nOk = Object.values(state).filter(s => s.ok).length;
  toast(`连通性测试：${nOk}/${Object.keys(CFG_LABELS).length} 个后端正常`, nOk < Object.keys(CFG_LABELS).length);
}
