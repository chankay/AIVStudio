// common.js — 跨页面共享：常量、工具函数、大图查看、删除项目
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

function phText(s) {
  const st = String(s.video_status);
  if (st === 'pending') return s.frame_url ? '首帧已确认，待视频' : '排队中';
  if (st.startsWith('frame')) return '首帧生成失败';
  if (st === 'failed') return '生成失败';
  if (st.startsWith('generating') || st.startsWith('retry') || st.startsWith('regen')) return '生成中…';
  return 'mock 模拟（无真实媒体）';
}

function badgeCls(st) {
  if (st === 'done') return 'b-done';
  if (st === 'failed' || st === 'frame_failed') return 'b-fail';
  if (st === 'pending') return 'b-pend';
  return 'b-run';
}

function fmtSec(s) {
  return s >= 3600 ? `${Math.floor(s/3600)}h${Math.round(s%3600/60)}m`
       : s >= 60 ? `${Math.floor(s/60)}m${Math.round(s%60)}s` : `${s}s`;
}

function toast(msg, err) {
  const t = document.createElement('div');
  t.className = 'toast' + (err ? ' err' : '');
  t.textContent = msg;
  document.getElementById('toasts').appendChild(t);
  setTimeout(() => t.remove(), err ? 5000 : 3000);
}

function openViewer(url, type) {
  if (!url) return;
  const v = document.getElementById('viewer');
  v.innerHTML = type === 'video' ? `<video src="${url}" controls autoplay loop></video>` : `<img src="${url}">`;
  v.classList.add('on');
}

async function delProject(pid, title) {
  if (!confirm(`删除项目「${title}」？其首帧和视频文件一并清除，不可恢复`)) return;
  try {
    const r = await api(`/api/projects/${pid}`, {method:'DELETE'});
    toast(r.msg || '已删除');
  } catch (e) {
    toast('删除失败：' + e.message, true);
  }
  if (location.hash.startsWith('#/project/')) location.hash = '#/projects';
  else if (typeof refreshCurrent === 'function') refreshCurrent();
}
