// api.js — 网络层：fetch 封装 + toast 提示
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
