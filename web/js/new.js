// new.js — 新建任务页：表单 + 提交后跳转详情
function pageNew() {
  pageRefreshers.new = null;   // 静态表单，无需 SSE 刷新
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
    toast('任务已创建，分镜 + 定妆生成中');
    document.getElementById('title').value = '';
    document.getElementById('idea').value = '';
    nav('project', p.id);   // 直接跳详情页看进度
  } catch (e) {
    toast('创建失败：' + e.message, true);
  } finally { btn.disabled = false; }
}
