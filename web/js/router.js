// router.js — hash 路由：#/projects | #/new | #/project/{pid} | #/settings
const PAGES = ['projects', 'new', 'project', 'settings'];

function currentRoute() {
  const h = location.hash || '#/projects';
  const parts = h.replace(/^#\//, '').split('/');
  return { page: parts[0] || 'projects', arg: parts[1] || '' };
}

function nav(page, arg) {
  location.hash = arg ? `#/${page}/${arg}` : `#/${page}`;
}

// 详情页归属「任务列表」高亮
function navKeyFor(page) {
  return page === 'project' ? 'projects' : page;
}

async function renderRoute() {
  const { page, arg } = currentRoute();
  const active = PAGES.includes(page) ? page : 'projects';

  // 高亮导航（详情页高亮「任务列表」）
  const key = navKeyFor(active);
  document.querySelectorAll('.nav a').forEach(a =>
    a.classList.toggle('on', a.dataset.page === key));

  // 面包屑：仅详情页显示，标题由 detail.js 回填
  const crumbs = document.getElementById('crumbs');
  if (crumbs) {
    crumbs.style.display = active === 'project' ? '' : 'none';
    if (active !== 'project') document.getElementById('crumbTitle').textContent = '';
  }

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
