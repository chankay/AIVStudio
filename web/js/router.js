// router.js — hash 路由：#/projects | #/new | #/project/{pid} | #/settings
const PAGES = ['projects', 'new', 'project', 'settings'];
let __pageInit = {};   // 页面首次进入标记，onEnter 只跑一次

const NAV_ITEMS = [
  { hash: '#/projects', page: 'projects', icon: '📋', label: '任务列表' },
  { hash: '#/new', page: 'new', icon: '✨', label: '新建任务' },
  { hash: '#/settings', page: 'settings', icon: '⚙️', label: '系统配置' },
];

function currentRoute() {
  const h = location.hash || '#/projects';
  const parts = h.replace(/^#\//, '').split('/');
  return { page: parts[0] || 'projects', arg: parts[1] || '' };
}

function nav(page, arg) {
  location.hash = arg ? `#/${page}/${arg}` : `#/${page}`;
}

async function renderRoute() {
  const { page, arg } = currentRoute();
  const active = PAGES.includes(page) ? page : 'projects';

  // 高亮导航
  document.querySelectorAll('.nav a').forEach(a =>
    a.classList.toggle('on', a.dataset.page === active));

  // 切显隐
  PAGES.forEach(p => {
    const el = document.getElementById('page-' + p);
    if (el) el.style.display = p === active ? '' : 'none';
  });

  // 各页面进入钩子
  if (active === 'projects') await pageProjects();
  else if (active === 'new') pageNew();
  else if (active === 'project') await pageProjectDetail(arg);
  else if (active === 'settings') pageSettings();
}

// 各页面模块向这里注册刷新函数，SSE 事件只刷新当前页
const pageRefreshers = {};
function refreshCurrent() {
  const r = pageRefreshers[currentRoute().page];
  if (r) r();
}

window.addEventListener('hashchange', renderRoute);
