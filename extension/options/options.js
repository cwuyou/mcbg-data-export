import { loadSettings, saveSettings, DEFAULT_SETTINGS } from '../lib/storage.js';
import { health } from '../lib/api.js';

let state = null;

const $ = (id) => document.getElementById(id);

function render() {
  $('serverUrl').value = state.serverUrl || DEFAULT_SETTINGS.serverUrl;

  const activeSel = $('activeProfile');
  activeSel.innerHTML = '';
  state.profiles.forEach((p) => {
    const opt = document.createElement('option');
    opt.value = p.name;
    opt.textContent = p.name;
    activeSel.appendChild(opt);
  });
  if (state.activeProfile) activeSel.value = state.activeProfile;

  const list = $('profileList');
  list.innerHTML = '';
  state.profiles.forEach((p, idx) => list.appendChild(renderProfile(p, idx)));
}

function renderProfile(p, idx) {
  const wrap = document.createElement('div');
  wrap.className = 'profile-item';
  wrap.innerHTML = `
    <div class="profile-head">
      <span class="profile-title">Profile #${idx + 1}</span>
      <button class="btn btn-danger" data-role="del">删除</button>
    </div>
    <label>名称 (用于切换标识)</label>
    <input type="text" data-field="name" value="${escapeAttr(p.name)}">
    <div class="row">
      <div>
        <label>Endpoint</label>
        <input type="text" data-field="endpoint" value="${escapeAttr(p.endpoint)}" placeholder="http://service.cn-hangzhou.maxcompute.aliyun.com/api">
      </div>
      <div>
        <label>Project</label>
        <input type="text" data-field="project" value="${escapeAttr(p.project)}">
      </div>
    </div>
    <div class="row">
      <div>
        <label>AccessKey ID</label>
        <input type="text" data-field="access_id" value="${escapeAttr(p.access_id)}">
      </div>
      <div>
        <label>AccessKey Secret</label>
        <input type="password" data-field="access_key" value="${escapeAttr(p.access_key)}">
      </div>
    </div>
  `;
  wrap.querySelectorAll('input').forEach((el) => {
    el.addEventListener('input', () => {
      p[el.dataset.field] = el.value;
      if (el.dataset.field === 'name') render();
    });
  });
  wrap.querySelector('[data-role="del"]').addEventListener('click', () => {
    if (!confirm('删除该 profile？')) return;
    state.profiles.splice(idx, 1);
    render();
  });
  return wrap;
}

function escapeAttr(s) {
  return (s || '').replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;');
}

$('btnAdd').addEventListener('click', () => {
  const n = state.profiles.length + 1;
  state.profiles.push({
    name: `profile-${n}`,
    endpoint: '',
    project: '',
    access_id: '',
    access_key: '',
  });
  if (!state.activeProfile) state.activeProfile = state.profiles[state.profiles.length - 1].name;
  render();
});

$('activeProfile').addEventListener('change', (e) => {
  state.activeProfile = e.target.value;
});

$('btnSave').addEventListener('click', async () => {
  state.serverUrl = $('serverUrl').value.trim() || DEFAULT_SETTINGS.serverUrl;
  if (state.profiles.length && !state.profiles.find((p) => p.name === state.activeProfile)) {
    state.activeProfile = state.profiles[0].name;
  }
  await saveSettings(state);
  const b = $('savedBanner');
  b.classList.add('show');
  setTimeout(() => b.classList.remove('show'), 1500);
});

$('btnPing').addEventListener('click', async () => {
  const url = $('serverUrl').value.trim() || DEFAULT_SETTINGS.serverUrl;
  const result = $('pingResult');
  result.textContent = '测试中...';
  try {
    const r = await health(url);
    result.textContent = r.ok ? '✓ 连接正常' : '✗ 响应异常';
    result.style.color = r.ok ? '#389e0d' : '#d00';
  } catch (e) {
    result.textContent = `✗ ${e.message}`;
    result.style.color = '#d00';
  }
});

(async () => {
  state = await loadSettings();
  render();
})();
