// detail.js — 任务详情页：完整项目卡片（角色/镜头/正片），保留媒体节点复用
const voiceDrafts = {};   // pid -> 音色输入框草稿
let lastDetailSnap = {};
let currentDetailPid = '';

async function pageProjectDetail(pid) {
  currentDetailPid = pid;
  pageRefreshers.project = () => refreshDetail(currentDetailPid);
  document.getElementById('detailMissing').style.display = 'none';
  await refreshDetail(pid);
}

async function refreshDetail(pid) {
  if (!pid) return;
  let projects;
  try { projects = await api('/api/projects'); } catch { return; }
  const p = projects.find(x => x.id === pid);
  const box = document.getElementById('detailBox');
  if (!p) {
    box.innerHTML = '';
    document.getElementById('detailMissing').style.display = '';
    return;
  }
  const sig = JSON.stringify(p);
  if (lastDetailSnap[pid] === sig && box.dataset.pid === pid) return;   // 没变不动
  lastDetailSnap[pid] = sig;
  box.dataset.pid = pid;

  // 面包屑回填剧名
  const ct = document.getElementById('crumbTitle');
  if (ct) ct.textContent = p.title;

  // 收集现有媒体节点，重绘后原样移回（播放进度、缓冲都保留）
  const keeps = {};
  box.querySelectorAll('.shot').forEach(el => {
    const m = el.querySelector('.media img, .media video');
    if (m) keeps[el.dataset.sid + '|' + m.tagName + '|' + (m.getAttribute('src') || '')] = m;
  });

  box.innerHTML = buildDetail(p);

  box.querySelectorAll('.shot').forEach(el => {
    const m = el.querySelector('.media img, .media video');
    if (!m) return;
    const k = el.dataset.sid + '|' + m.tagName + '|' + (m.getAttribute('src') || '');
    if (keeps[k]) m.replaceWith(keeps[k]);
  });
  const inp = box.querySelector('.voice-input');
  if (inp && voiceDrafts[pid] !== undefined) inp.value = voiceDrafts[pid];
}

function buildDetail(p) {
  const shots = p.shots || [];
  const doneN = shots.filter(s => s.video_status === 'done').length;
  const total = shots.length;
  const running = p.status === 'running' || p.status === 'dubbing';
  const elapsed = running ? Math.round(Date.now() / 1000 - p.pipeline_started_at) : (p.elapsed_total || 0);
  return `
    <div class="proj">
      <div>
        <strong>${p.title}</strong>
        <span class="status ${p.status}">${PROJ_STATUS[p.status] || p.status}</span>
        ${total ? `<div class="meta" style="margin-top:4px">${doneN}/${total} 成片 · 耗时 ${fmtSec(elapsed)}${running ? '（计时中）' : ''}</div>` : ''}
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
    ${p.status === 'frames_ready' && shots.some(s => s.frame_url) ? `<div class="banner ok">
      <span>✅ 首帧已出完，检查画面和人物形象，满意后开跑视频（视频生成慢，确认后再跑）</span>
      <button style="margin-top:0;white-space:nowrap;" onclick="runVideos('${p.id}')">开始生成视频</button>
    </div>` : ''}
    ${p.status === 'done' && shots.some(s => s.video_url) ? `<div class="banner info">
      <input class="voice-input" placeholder="音色描述（如：年轻男性，声音低沉温暖），留空用默认" style="flex:1;padding:8px 12px;font-size:13px;" oninput="voiceDrafts['${p.id}']=this.value">
      <button style="margin-top:0;white-space:nowrap;" onclick="genTts('${p.id}')">${p.final_url ? '重新配音+合成正片' : '生成配音+合成正片'}</button>
    </div>` : ''}
    ${p.final_url ? `<div class="banner final-box" style="display:block;">
      <div style="margin-bottom:8px;font-weight:600">🎬 正片<span style="font-weight:400;color:var(--txt-3);font-size:12px;margin-left:8px">含配音，按镜头顺序拼接</span></div>
      <video src="${p.final_url}" controls></video>
      <div style="margin-top:8px"><a href="${p.final_url}" download="${p.title}.mp4">下载正片 ↓</a></div>
    </div>` : ''}
    ${p.status === 'frames_ready' && !shots.some(s => s.frame_url) ? `<div class="banner bad">
      <span>❌ 首帧全部失败（0/${total}），查看日志排查后重试</span>
      <button style="margin-top:0;background:linear-gradient(135deg,#f87171,#dc2626);" onclick="retryFrames('${p.id}')">重试首帧</button>
    </div>` : ''}
    ${p.char_ref && shots.some(s => s.char_unified) ? `<div class="meta" style="margin-top:8px">🧑 角色参考图：第一张成功首帧，其余镜头已经 Edit 模型统一人物形象</div>` : ''}
    ${shots.length ? `<div class="shots">
      ${shots.map(s => {
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

// ---------- 镜头/项目操作 ----------
async function regen(sid) {
  try {
    await api(`/api/shots/${sid}/regenerate?mode=video`, {method:'POST'});
    toast('镜头 ' + sid + ' 视频重抽中');
  } catch (e) { toast('重抽失败：' + e.message, true); }
  refreshDetail(currentDetailPid);
}

async function regenFrame(sid) {
  try {
    await api(`/api/shots/${sid}/regenerate?mode=frame`, {method:'POST'});
    toast('镜头 ' + sid + ' 首帧重抽中');
  } catch (e) { toast('重抽失败：' + e.message, true); }
  refreshDetail(currentDetailPid);
}

async function editDialogue(sid, cur) {
  const d = prompt('编辑台词（配音前可随时改，留空则删除台词）：', cur || '');
  if (d === null) return;   // 取消
  try {
    await api(`/api/shots/${sid}/dialogue`, {method:'POST', body: JSON.stringify({dialogue: d.trim()})});
    toast('台词已更新');
  } catch (e) { toast('台词更新失败：' + e.message, true); }
  refreshDetail(currentDetailPid);
}

async function runFrames(pid) {
  try {
    await api(`/api/projects/${pid}/run_frames`, {method:'POST'});
    toast('定妆已确认，首帧生成中');
  } catch (e) { toast('启动失败：' + e.message, true); }
  refreshDetail(pid);
}

async function retryDesign(pid) {
  try {
    await api(`/api/projects/${pid}/run?stage=design`, {method:'POST'});
    toast('定妆重试中');
  } catch (e) { toast('重试失败：' + e.message, true); }
  refreshDetail(pid);
}

async function regenChar(pid, name) {
  try {
    await api(`/api/characters/${pid}/regenerate`, {method:'POST', body: JSON.stringify({name})});
    toast('角色「' + name + '」定妆重抽中');
  } catch (e) { toast('重抽失败：' + e.message, true); }
  refreshDetail(pid);
}

async function runVideos(pid) {
  if (!confirm('确认首帧都满意？视频阶段每条要跑较久')) return;
  try {
    await api(`/api/projects/${pid}/run_videos`, {method:'POST'});
    toast('视频阶段已启动');
  } catch (e) { toast('启动失败：' + e.message, true); }
  refreshDetail(pid);
}

async function retryFrames(pid) {
  try {
    await api(`/api/projects/${pid}/run?stage=frames`, {method:'POST'});
    toast('首帧重试中');
  } catch (e) { toast('重试失败：' + e.message, true); }
  refreshDetail(pid);
}

async function genTts(pid) {
  const inp = document.querySelector(`#detailBox .voice-input`);
  const body = { voice_desc: inp ? inp.value.trim() : '' };
  try {
    await api(`/api/projects/${pid}/tts`, {method:'POST', body: JSON.stringify(body)});
    toast('配音+合成已启动');
  } catch (e) { toast('启动失败：' + e.message, true); }
  refreshDetail(pid);
}
