// api.js — 网络层：fetch 封装 + toast 提示 + 401 自动跳登录
function toast(msg, err) {
  const t = document.createElement('div');
  t.className = 'toast' + (err ? ' err' : '');
  t.textContent = msg;
  document.getElementById('toasts').appendChild(t);
  setTimeout(() => t.remove(), err ? 5000 : 3000);
}

async function api(path, opts) {
  const r = await fetch(path, opts ? {headers:{'Content-Type':'application/json'}, ...opts} : undefined);
  if (r.status === 401) {
    // 会话过期/未登录：跳登录页（带返回地址）
    location.href = '/login?next=' + encodeURIComponent(location.pathname + location.hash);
    throw new Error('未登录');
  }
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

async function logout() {
  try { await api('/api/auth/logout', {method:'POST'}); } catch {}
  location.href = '/login';
}
