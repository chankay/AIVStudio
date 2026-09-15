// settings.js — 系统配置页：模型后端连通性配置（原弹窗改为独立页面）
const CFG_LABELS = {
  llm: '剧本分镜（LLM）', image: '文生图（首帧/定妆）', edit: '图生图（角色一致性）',
  video: '图生视频（I2V）', tts: '语音合成（TTS）'
};

function pageSettings() {
  pageRefreshers.settings = null;
  renderConfig();
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
    initHealth();  // 立刻按新配置刷新健康状态
  } catch (e) { toast('保存失败：' + e.message, true); }
}

async function testAllBackends() {
  const btn = document.getElementById('btnTestCfg');
  btn.disabled = true; btn.textContent = '测试中…';
  const state = {};
  await Promise.all(Object.keys(CFG_LABELS).map(async g => {
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

// ---------- 账号安全：修改密码 ----------
async function changePassword() {
  const oldPw = document.getElementById('pwOld').value;
  const newPw = document.getElementById('pwNew').value;
  const newPw2 = document.getElementById('pwNew2').value;
  if (!oldPw || !newPw) { toast('旧密码和新密码都要填', true); return; }
  if (newPw !== newPw2) { toast('两次输入的新密码不一致', true); return; }
  const btn = document.getElementById('btnChangePw');
  btn.disabled = true;
  try {
    await api('/api/auth/change_password', {method:'POST', body: JSON.stringify({
      old_password: oldPw, new_password: newPw
    })});
    toast('密码已更新，请重新登录');
    setTimeout(() => { location.href = '/login'; }, 1200);   // 会话已被服务端吊销
  } catch (e) {
    toast(e.message.replace(/^"|"$/g, ''), true);
    btn.disabled = false;
  }
}
