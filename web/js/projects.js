// projects.js — 任务列表页：概要卡片 + 差分渲染
let lastListSnap = {};   // pid -> 签名

async function pageProjects() {
  pageRefreshers.projects = pageProjects;
  await refreshProjects();
}

async function refreshProjects() {
  let projects;
  try { projects = await api('/api/projects'); } catch { return; }
  const container = document.getElementById('projectList');
  const pids = new Set();

  for (const p of projects) {
    pids.add(p.id);
    const sig = JSON.stringify(p);
    const card = document.getElementById('plist-' + p.id);
    if (card && lastListSnap[p.id] === sig) continue;
    lastListSnap[p.id] = sig;

    const html = buildListCard(p);
    if (!card) {
      const nc = document.createElement('div');
      nc.id = 'plist-' + p.id; nc.className = 'card plist-card'; nc.dataset.pid = p.id;
      nc.innerHTML = html;
      nc.addEventListener('click', e => {
        if (e.target.closest('button')) return;   // 卡片内按钮不触发跳转
        nav('project', p.id);
      });
      container.appendChild(nc);
    } else {
      card.innerHTML = html;
    }
  }
  container.querySelectorAll('.card[data-pid]').forEach(c => {
    if (!pids.has(c.dataset.pid)) { delete lastListSnap[c.dataset.pid]; c.remove(); }
  });
  document.getElementById('listEmpty').style.display = projects.length ? 'none' : '';
}

function buildListCard(p) {
  const shots = p.shots || [];
  const doneN = shots.filter(s => s.video_status === 'done').length;
  const total = shots.length;
  const running = p.status === 'running' || p.status === 'dubbing';
  const elapsed = running ? Math.round(Date.now() / 1000 - p.pipeline_started_at) : (p.elapsed_total || 0);
  // 阶段提示
  const stageTip = {
    created: '分镜排队中', running: shots.some(s => String(s.video_status).startsWith('generating'))
      ? '视频生成中' : '流水线运行中',
    design_ready: '👉 待确认定妆照', frames_ready: '👉 待确认首帧',
    done: p.final_url ? '正片已出' : '可生成配音', error: '❌ 出错，看日志'
  }[p.status] || '';
  return `
    <div class="proj">
      <div>
        <strong>${p.title}</strong>
        <span class="status ${p.status}">${PROJ_STATUS[p.status] || p.status}</span>
        ${stageTip ? `<span class="stage-tip">${stageTip}</span>` : ''}
        <div class="meta" style="margin-top:4px">
          ${total ? `${doneN}/${total} 镜头完成 · ` : ''}耗时 ${fmtSec(elapsed)}${running ? '（计时中）' : ''}
        </div>
        <div class="meta">创意：${p.idea.length > 50 ? p.idea.slice(0, 50) + '…' : p.idea}　·　画风：${p.style}　·　${p.created_at}</div>
      </div>
      <div style="display:flex;gap:8px;align-items:center;">
        <button class="ghost" onclick="event.stopPropagation();nav('project','${p.id}')">进入详情 →</button>
        <button class="ghost" style="color:var(--red);border-color:rgba(248,113,113,.3);" onclick="event.stopPropagation();delProject('${p.id}', '${p.title.replace(/'/g, '')}')">删除</button>
      </div>
    </div>
    ${p.log && p.log.length ? `<div class="log">${p.log.slice(-3).join('<br>')}</div>` : ''}`;
}
