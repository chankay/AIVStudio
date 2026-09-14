// ui.js — 渲染与操作：项目卡片差分渲染、镜头操作、大图查看
const MODE_NOTE = {
  mock: '当前为 mock 模式（无 GPU 演示，含模拟抽卡失败）',
  real: '当前为 real 模式（对接真实生成服务）'
};
const PROJ_STATUS = {
  created: '已创建', running: '生成中', design_ready: '待确认定妆',
  frames_ready: '待确认首帧', done: '已完成', error: '出错',
  dubbing: '配音合成中', tts_running: '配音合成中'
};
function shotStatusText(st) {
  st = String(st);
  if (st === 'done') return '完成';
  if (st === 'pending') return '待视频';
  if (st === 'generating_frame') return '首帧生成中';
  if (st === 'frame_failed') return '首帧失败';
  if (st === 'generating_video') return '视频生成中';
  if (st === 'failed') return '失败';
  if (st.startsWith('retry') || st.startsWith('regen')) return '重抽中';
  return st;
}

function toast(msg, err) {
  const t = document.createElement('div');
  t.className = 'toast' + (err ? ' err' : '');
  t.textContent = msg;
  document.getElementById('toasts').appendChild(t);
  setTimeout(() => t.remove(), err ? 5000 : 3000);
}

async function api(path, opts) {
  const r = await fetch(path, opts ? {headers:{'Content-Type':'application/json'}, ...opts} : undefined);
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

async function createAndRun() {
  const title = document.getElementById('title').value.trim();
  const idea = document.getElementById('idea').value.trim();
  if (!title || !idea) { toast('剧名和创意都要填', true); return; }
  const btn = document.getElementById('btnCreate');
  btn.disabled = true;
  try {
    const p = await api('/api/projects', {method:'POST', body: JSON.stringify({
      title, idea,
      n_shots: parseInt(document.getElementById('nshots').value) || 6,
      style: document.getElementById('style').value || '写实电影感'
    })});
    // v2 流程：先只跑「分镜 + 角色定妆」，确认角色形象后再进首帧 → 视频
    await api(`/api/projects/${p.id}/run?stage=design`, {method:'POST'});
    document.getElementById('title').value = '';
    document.getElementById('idea').value = '';
    await refresh();
    const el = document.getElementById('proj-' + p.id);
    if (el) el.scrollIntoView({behavior:'smooth', block:'start'});
  } catch (e) {
    toast('创建失败：' + e.message, true);
  } finally { btn.disabled = false; }
}

// ---------- 渲染（按项目差分更新；媒体节点跨刷新复用，避免视频/图片被重置） ----------
let lastSnap = {};        // pid -> 上次渲染的数据签名
const voiceDrafts = {};   // pid -> 音色输入框草稿（重渲染后回填）
let modeNote = '';        // 后端健康说明（由 /api/mode 维护，refresh 不覆盖）

async function refresh() {
  let projects;
  try { projects = await api('/api/projects'); } catch { return; }
  document.getElementById('modeTag').textContent = MODE_NOTE[window.__mode || 'mock'];
  const container = document.getElementById('projects');
  const pids = new Set();

  for (const p of projects) {
    pids.add(p.id);
    const sig = JSON.stringify(p);
    const card = document.getElementById('proj-' + p.id);
    // 数据没变就完全不动这个项目的 DOM（输入框、正在看的视频都不受打扰）
    if (card && lastSnap[p.id] === sig) continue;
    lastSnap[p.id] = sig;

    // 收集现有媒体节点（按 镜头id+类型+src 匹配），重绘后原样移回来——播放进度、缓冲都保留
    const keeps = {};
    if (card) card.querySelectorAll('.shot').forEach(el => {
      const m = el.querySelector('.media img, .media video');
      if (m) keeps[el.dataset.sid + '|' + m.tagName + '|' + (m.getAttribute('src') || '')] = m;
    });

    if (!card) {
      const nc = document.createElement('div');
      nc.id = 'proj-' + p.id; nc.className = 'card'; nc.dataset.pid = p.id;
      container.appendChild(nc);
    }
    document.getElementById('proj-' + p.id).innerHTML = buildCard(p);

    // 把保留的媒体节点移回对应镜头
    const fresh = document.getElementById('proj-' + p.id);
    fresh.querySelectorAll('.shot').forEach(el => {
      const m = el.querySelector('.media img, .media video');
      if (!m) return;
      const k = el.dataset.sid + '|' + m.tagName + '|' + (m.getAttribute('src') || '');
      if (keeps[k]) m.replaceWith(keeps[k]);
    });
    // 回填音色草稿
    const inp = fresh.querySelector('.voice-input');
    if (inp && voiceDrafts[p.id] !== undefined) inp.value = voiceDrafts[p.id];
  }
  // 清掉已删除项目的 DOM
  container.querySelectorAll('.card[data-pid]').forEach(c => {
    if (!pids.has(c.dataset.pid)) { delete lastSnap[c.dataset.pid]; c.remove(); }
  });
}

function fmtSec(s) {
  return s >= 3600 ? `${Math.floor(s/3600)}h${Math.round(s%3600/60)}m`
       : s >= 60 ? `${Math.floor(s/60)}m${Math.round(s%60)}s` : `${s}s`;
}

function buildCard(p) {
  const doneN = (p.shots || []).filter(s => s.video_status === 'done').length;
  const total = (p.shots || []).length;
  const elapsed = p.status === 'running' || p.status === 'dubbing'
    ? Math.round(Date.now() / 1000 - p.pipeline_started_at)
    : (p.elapsed_total || 0);
  return `
    <div class="proj">
      <div>
        <strong>${p.title}</strong>
        <span class="status ${p.status}">${PROJ_STATUS[p.status] || p.status}</span>
        ${total ? `<div class="meta" style="margin-top:4px">${doneN}/${total} 成片 · 耗时 ${fmtSec(elapsed)}${p.status === 'running' || p.status === 'dubbing' ? '（计时中）' : ''}</div>` : ''}
        <div class="meta">创意：${p.idea}　·　画风：${p.style}　·　${p.created_at}</div>
      </div>
      <button class="ghost" style="color:var(--red);border-color:rgba(248,113,113,.3);" onclick="delProject('${p.id}', '${p.title.replace(/'/g, '')}')">删除</button>
    </div>
    ${p.log && p.log.length ? `<div class="log">${p.log.slice(-6).join('<br>')}</div>` : ''}
    ${(p.characters || []).length ? `<div style="margin-top:16px;font-size:13px;font-weight:600">🧑‍🎭 角色档案</div>
    <div class="chars">
      ${p.characters.map(c => `<div class="char" data-cname="${c.name}">
        <div class="media" onclick="openViewer('${c.design_url || ''}','image')">
          ${c.design_url ? `<img src="${c.design_url}" loading="lazy">` : `<div class="ph">定妆生成中…</div>`}
        </div>
        <div class="info">
          <div class="name">${c.name}</div>
          <div class="app">${c.appearance || ''}</div>
        </div>
        <div class="ops">${c.design_url ? `<button class="ghost" onclick="regenChar('${p.id}','${c.name.replace(/'/g, '')}')">换定妆</button>` : ''}</div>
      </div>`).join('')}
    </div>` : ''}
    ${p.status === 'design_ready' && (p.characters || []).some(c => c.design_url) ? `<div class="banner ok">
      <span>✅ 角色定妆已出，检查形象是否满意（此时重抽只花几秒，确认后才开始烧首帧）</span>
      <button style="margin-top:0;white-space:nowrap;" onclick="runFrames('${p.id}')">确认定妆，生成首帧</button>
    </div>` : ''}
    ${p.status === 'design_ready' && !(p.characters || []).some(c => c.design_url) ? `<div class="banner bad">
      <span>❌ 定妆全部失败，查看日志排查后重试</span>
      <button style="margin-top:0;background:linear-gradient(135deg,#f87171,#dc2626);" onclick="retryDesign('${p.id}')">重试定妆</button>
    </div>` : ''}
    ${p.status === 'frames_ready' && (p.shots || []).some(s => s.frame_url) ? `<div class="banner ok">
      <span>✅ 首帧已出完，检查画面和人物形象，满意后开跑视频（视频生成慢，确认后再跑）</span>
      <button style="margin-top:0;white-space:nowrap;" onclick="runVideos('${p.id}')">开始生成视频</button>
    </div>` : ''}
    ${p.status === 'done' && (p.shots || []).some(s => s.video_url) ? `<div class="banner info">
      <input class="voice-input" placeholder="音色描述（如：年轻男性，声音低沉温暖），留空用默认" style="flex:1;padding:8px 12px;font-size:13px;" oninput="voiceDrafts['${p.id}']=this.value">
      <button style="margin-top:0;white-space:nowrap;" onclick="genTts('${p.id}')">${p.final_url ? '重新配音+合成正片' : '生成配音+合成正片'}</button>
    </div>` : ''}
    ${p.final_url ? `<div class="banner final-box" style="display:block;">
      <div style="margin-bottom:8px;font-weight:600">🎬 正片<span style="font-weight:400;color:var(--txt-3);font-size:12px;margin-left:8px">含配音，按镜头顺序拼接</span></div>
      <video src="${p.final_url}" controls></video>
      <div style="margin-top:8px"><a href="${p.final_url}" download="${p.title}.mp4">下载正片 ↓</a></div>
    </div>` : ''}
    ${p.status === 'frames_ready' && !(p.shots || []).some(s => s.frame_url) ? `<div class="banner bad">
      <span>❌ 首帧全部失败（0/${(p.shots || []).length}），查看日志排查后重试</span>
      <button style="margin-top:0;background:linear-gradient(135deg,#f87171,#dc2626);" onclick="retryFrames('${p.id}')">重试首帧</button>
    </div>` : ''}
    ${p.char_ref && p.shots && p.shots.some(s => s.char_unified) ? `<div class="meta" style="margin-top:8px">🧑 角色参考图：第一张成功首帧，其余镜头已经 Edit 模型统一人物形象</div>` : ''}
    ${p.shots && p.shots.length ? `<div class="shots">
      ${p.shots.map(s => {
        const vid = s.video_status === 'done' && s.video_url;
        const showBar = ['generating_video', 'generating_frame', 'pending'].includes(s.video_status) || String(s.video_status).startsWith('retry') || String(s.video_status).startsWith('regen');
        const pct = s.video_status === 'generating_video' ? (s.progress || 0) : showBar ? 0 : null;
        const metaBits = [s.camera, `${s.duration}s`];
        if (s.attempts) metaBits.push(`抽卡${s.attempts}次`);
        if (s.frame_elapsed) metaBits.push(`首帧${fmtSec(s.frame_elapsed)}`);
        if (s.video_elapsed) metaBits.push(`视频${fmtSec(s.video_elapsed)}`);
        if (s.elapsed && s.video_status !== 'done') metaBits.push(`已进行${fmtSec(s.elapsed)}`);
        return `<div class="shot" data-sid="${s.shot_id}">
          <div class="media" onclick="openViewer('${vid ? s.video_url : (s.frame_url || '')}', ${vid ? "'video'" : "'image'"})">
            ${vid
              ? `<video src="${s.video_url}" muted loop preload="metadata" onmouseover="this.play()" onmouseout="this.pause()"></video>`
              : s.frame_url
                ? `<img src="${s.frame_url}" loading="lazy">`
                : `<div class="ph">${phText(s)}</div>`}
            ${showBar && s.video_status !== 'pending' ? `<div class="pbar"><div style="width:${pct || (s.video_status === 'generating_frame' ? 5 : 15)}%"></div></div>` : ''}
          </div>
          <div class="info">
            <div class="sid">${s.shot_id} <span class="badge ${badgeCls(s.video_status)}">${s.video_status === 'generating_video' ? `生成中 ${pct || 0}%` : shotStatusText(s.video_status)}</span></div>
            <div class="desc">${s.description}</div>
            ${s.dialogue ? `<div class="dlg">「${s.dialogue}」</div>` : ''}
            <div style="color:var(--txt-3)">${metaBits.join(' · ')}</div>
          </div>
          <div class="ops">
            ${s.frame_url ? `<button class="ghost" onclick="openViewer('${s.frame_url}','image')">看首帧</button>` : ''}
            ${s.frame_url && !vid ? `<button class="ghost" onclick="regenFrame('${s.shot_id}')">换首帧</button>` : ''}
            ${!s.frame_url && String(s.video_status).startsWith('frame') ? `<button class="ghost" onclick="regenFrame('${s.shot_id}')">重试首帧</button>` : ''}
            ${vid || s.video_status === 'failed' ? `<button class="ghost" onclick="regen('${s.shot_id}')">重抽视频</button>` : ''}
            ${vid && !p.final_url ? `<button class="ghost" onclick="editDialogue('${s.shot_id}', '${(s.dialogue || '').replace(/\\/g, '\\\\').replace(/'/g, "\\'").replace(/\n/g, ' ')}')">改台词</button>` : ''}
          </div>
        </div>`;
      }).join('')}
    </div>` : ''}`;
}

function phText(s) {
  const st = String(s.video_status);
  if (st === 'pending') return s.frame_url ? '首帧已确认，待视频' : '排队中';
  if (st.startsWith('frame')) return '首帧生成失败';
  if (st === 'failed') return '生成失败';
  if (st.startsWith('generating') || st.startsWith('retry') || st.startsWith('regen')) return '生成中…';
  return 'mock 模拟（无真实媒体）';
}

function openViewer(url, type) {
  if (!url) return;
  const v = document.getElementById('viewer');
  v.innerHTML = type === 'video' ? `<video src="${url}" controls autoplay loop></video>` : `<img src="${url}">`;
  v.classList.add('on');
}

function badgeCls(st) {
  if (st === 'done') return 'b-done';
  if (st === 'failed' || st === 'frame_failed') return 'b-fail';
  if (st === 'pending') return 'b-pend';
  return 'b-run';
}

async function regen(sid) {
  try {
    await api(`/api/shots/${sid}/regenerate?mode=video`, {method:'POST'});
    toast('镜头 ' + sid + ' 视频重抽中');
  } catch (e) { toast('重抽失败：' + e.message, true); }
  refresh();
}

async function regenFrame(sid) {
  try {
    await api(`/api/shots/${sid}/regenerate?mode=frame`, {method:'POST'});
    toast('镜头 ' + sid + ' 首帧重抽中');
  } catch (e) { toast('重抽失败：' + e.message, true); }
  refresh();
}

async function editDialogue(sid, cur) {
  const d = prompt('编辑台词（配音前可随时改，留空则删除台词）：', cur || '');
  if (d === null) return;   // 取消
  try {
    await api(`/api/shots/${sid}/dialogue`, {method:'POST', body: JSON.stringify({dialogue: d.trim()})});
    toast('台词已更新');
  } catch (e) { toast('台词更新失败：' + e.message, true); }
  refresh();
}

async function runFrames(pid) {
  try {
    await api(`/api/projects/${pid}/run_frames`, {method:'POST'});
    toast('定妆已确认，首帧生成中');
  } catch (e) { toast('启动失败：' + e.message, true); }
  refresh();
}

async function retryDesign(pid) {
  try {
    await api(`/api/projects/${pid}/run?stage=design`, {method:'POST'});
    toast('定妆重试中');
  } catch (e) { toast('重试失败：' + e.message, true); }
  refresh();
}

async function regenChar(pid, name) {
  try {
    await api(`/api/characters/${pid}/regenerate`, {method:'POST', body: JSON.stringify({name})});
    toast('角色「' + name + '」定妆重抽中');
  } catch (e) { toast('重抽失败：' + e.message, true); }
  refresh();
}

async function runVideos(pid) {
  if (!confirm('确认首帧都满意？视频阶段每条要跑较久')) return;
  try {
    await api(`/api/projects/${pid}/run_videos`, {method:'POST'});
    toast('视频阶段已启动');
  } catch (e) { toast('启动失败：' + e.message, true); }
  refresh();
}

async function retryFrames(pid) {
  try {
    await api(`/api/projects/${pid}/run?stage=frames`, {method:'POST'});
    toast('首帧重试中');
  } catch (e) { toast('重试失败：' + e.message, true); }
  refresh();
}

async function genTts(pid) {
  const inp = document.querySelector(`#proj-${pid} .voice-input`);
  const body = { voice_desc: inp ? inp.value.trim() : '' };
  try {
    await api(`/api/projects/${pid}/tts`, {method:'POST', body: JSON.stringify(body)});
    toast('配音+合成已启动');
  } catch (e) { toast('启动失败：' + e.message, true); }
  refresh();
}

async function delProject(pid, title) {
  if (!confirm(`删除项目「${title}」？其首帧和视频文件一并清除，不可恢复`)) return;
  try {
    const r = await api(`/api/projects/${pid}`, {method:'DELETE'});
    toast(r.msg || '已删除');
  } catch (e) {
    toast('删除失败：' + e.message, true);
  }
  refresh();
}
