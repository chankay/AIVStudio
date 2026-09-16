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
    // 火山定制版·多模态分镜：选了参考图就先上传（分镜前生效）
    const files = document.getElementById('refFiles').files;
    if (files.length) {
      const fd = new FormData();
      for (const f of [...files].slice(0, 5)) fd.append('files', f);
      try {
        const up = await apiForm(`/api/projects/${p.id}/storyboard_refs`, fd);
        toast(`已传 ${up.refs.length} 张参考图，将启用多模态分镜`);
      } catch (e) {
        toast('参考图上传失败，退回纯文本分镜：' + e.message, true);
      }
    }
    // v2 流程：先只跑「分镜 + 角色定妆」，确认角色形象后再进首帧 → 视频
    await api(`/api/projects/${p.id}/run?stage=design`, {method:'POST'});
    toast('任务已创建，分镜 + 定妆生成中');
    document.getElementById('title').value = '';
    document.getElementById('idea').value = '';
    document.getElementById('refFiles').value = '';
    nav('project', p.id);   // 直接跳详情页看进度
  } catch (e) {
    toast('创建失败：' + e.message, true);
  } finally { btn.disabled = false; }
}
