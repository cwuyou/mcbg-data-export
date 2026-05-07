import { loadSettings, saveSettings, getActiveProfile } from '../lib/storage.js';
import {
  fetchSchema,
  fetchPartitions,
  createExportTask,
  subscribeTask,
  streamCsvUrl,
  cancelTask,
  authHeaders,
} from '../lib/api.js';

let settings = null;
let mode = 'table';
let schema = null;
let currentTask = null;
let currentUnsub = null;
let downloadedTasks = new Set();

const $ = (id) => document.getElementById(id);

function showErr(msg) {
  const box = $('errBox');
  box.textContent = msg || '';
  box.classList.toggle('hidden', !msg);
}

function formatBytes(n) {
  if (!n) return '';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let i = 0;
  while (n >= 1024 && i < units.length - 1) { n /= 1024; i++; }
  return `${n.toFixed(i === 0 ? 0 : 1)} ${units[i]}`;
}

function setActiveTab(m) {
  mode = m;
  document.querySelectorAll('.tab').forEach((t) => t.classList.toggle('active', t.dataset.mode === m));
  document.querySelectorAll('.pane').forEach((p) => p.classList.toggle('hidden', p.dataset.pane !== m));
}

function renderProfileSelect() {
  const sel = $('profileSel');
  sel.innerHTML = '';
  settings.profiles.forEach((p) => {
    const opt = document.createElement('option');
    opt.value = p.name;
    opt.textContent = p.name;
    sel.appendChild(opt);
  });
  if (settings.activeProfile) sel.value = settings.activeProfile;

  const hasProfile = settings.profiles.length > 0;
  $('noProfile').classList.toggle('hidden', hasProfile);
  $('mainPane').classList.toggle('hidden', !hasProfile);
}

function renderColumns() {
  const list = $('columnList');
  list.innerHTML = '';
  schema.columns.forEach((col) => {
    const row = document.createElement('label');
    row.className = 'column-item';
    row.innerHTML = `
      <input type="checkbox" data-col="${escapeAttr(col.name)}" checked>
      <span>${escapeAttr(col.name)}</span>
      <span class="type">${escapeAttr(col.type)}</span>
    `;
    list.appendChild(row);
  });
  $('columnArea').classList.remove('hidden');
}

function escapeAttr(s) {
  return (s || '').replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;');
}

function selectedColumns() {
  return Array.from(document.querySelectorAll('#columnList input[type="checkbox"]:checked'))
    .map((el) => el.dataset.col);
}

async function loadSchema() {
  showErr('');
  const table = $('tableName').value.trim();
  if (!table) { showErr('请输入表名'); return; }

  const profile = getActiveProfile(settings);
  try {
    $('btnLoadSchema').disabled = true;
    $('btnLoadSchema').textContent = '加载中...';
    schema = await fetchSchema(settings.serverUrl, profile, { table });
    renderColumns();

    if (schema.is_partitioned) {
      $('partitionArea').classList.remove('hidden');
      await loadPartitions(table);
    } else {
      $('partitionArea').classList.add('hidden');
    }

    const est = $('estimate');
    const parts = [];
    if (schema.row_count != null) parts.push(`行数 ≈ ${schema.row_count.toLocaleString()}`);
    if (schema.size_bytes) parts.push(`大小 ≈ ${formatBytes(schema.size_bytes)}`);
    if (parts.length) {
      est.textContent = parts.join('  ·  ');
      est.classList.remove('hidden');
    } else {
      est.classList.add('hidden');
    }
  } catch (e) {
    showErr(`加载 schema 失败: ${e.message}`);
  } finally {
    $('btnLoadSchema').disabled = false;
    $('btnLoadSchema').textContent = '加载 schema';
  }
}

async function loadPartitions(table) {
  const profile = getActiveProfile(settings);
  try {
    const r = await fetchPartitions(settings.serverUrl, profile, { table });
    const sel = $('partitionSel');
    sel.innerHTML = '<option value="">（全部分区）</option>';
    r.partitions.forEach((p) => {
      const opt = document.createElement('option');
      opt.value = p;
      opt.textContent = p;
      sel.appendChild(opt);
    });
  } catch (e) {
    showErr(`加载分区失败: ${e.message}`);
  }
}

function buildExportPayload(format) {
  if (mode === 'sql') {
    const sql = $('sqlInput').value.trim();
    if (!sql) { throw new Error('请输入 SQL'); }
    return {
      sql,
      format,
      filename: $('filename').value.trim() || 'query_result',
    };
  }
  const table = $('tableName').value.trim();
  if (!table) { throw new Error('请输入表名'); }
  const partition = $('partitionSel').value || null;
  const where = $('whereInput').value.trim() || null;
  const columns = schema ? selectedColumns() : null;
  if (schema && columns.length === 0) { throw new Error('请至少勾选一个字段'); }
  return {
    table,
    partition,
    columns,
    where,
    format,
    filename: $('filename').value.trim() || table.split('.').pop(),
  };
}

function updateProgressUI(snap) {
  $('taskArea').classList.remove('hidden');
  const bar = $('progressBar');
  const status = $('taskStatus');
  let pct = 0;
  if (snap.total && snap.total > 0) pct = Math.min(100, (snap.processed / snap.total) * 100);
  bar.style.width = `${pct}%`;

  const processedStr = snap.processed.toLocaleString();
  const totalStr = snap.total ? snap.total.toLocaleString() : '?';
  const statusMap = {
    pending: '等待中',
    running: `导出中 ${processedStr} / ${totalStr}`,
    done: `完成 ${processedStr} 行`,
    failed: `失败: ${snap.message || '未知错误'}`,
    cancelled: '已取消',
  };
  status.textContent = statusMap[snap.status] || snap.status;

  const active = snap.status === 'pending' || snap.status === 'running';
  $('btnExport').disabled = active;
  $('btnCancel').classList.toggle('hidden', !active);

  if (snap.status === 'done' && snap.download_url && !downloadedTasks.has(snap.task_id)) {
    downloadedTasks.add(snap.task_id);
    const url = `${settings.serverUrl}${snap.download_url}`;
    chrome.runtime.sendMessage({ type: 'download', url, filename: snap.filename }, (resp) => {
      if (chrome.runtime.lastError || !resp || !resp.ok) {
        const err = (resp && resp.error) || (chrome.runtime.lastError && chrome.runtime.lastError.message);
        showErr(`下载失败: ${err || '未知错误'}`);
      }
    });
  }
}

async function startExport() {
  showErr('');
  const profile = getActiveProfile(settings);
  if (!profile) { showErr('请先配置 profile'); return; }
  const format = document.querySelector('input[name="fmt"]:checked').value;

  try {
    const payload = buildExportPayload(format);

    if (format === 'csv' && mode === 'sql') {
      await directDownloadCsv(profile, payload);
      return;
    }
    if (format === 'csv' && mode === 'table' && !schema && !payload.where) {
      await directDownloadCsv(profile, payload);
      return;
    }

    const r = await createExportTask(settings.serverUrl, profile, payload);
    currentTask = r.task_id;
    if (currentUnsub) currentUnsub();
    currentUnsub = subscribeTask(
      settings.serverUrl,
      r.task_id,
      updateProgressUI,
      (e) => showErr(`进度订阅异常: ${e.message || e}`)
    );
  } catch (e) {
    showErr(e.message);
  }
}

async function directDownloadCsv(profile, payload) {
  const url = streamCsvUrl(settings.serverUrl);
  const headers = { 'Content-Type': 'application/json', ...authHeaders(profile) };
  try {
    const resp = await fetch(url, { method: 'POST', headers, body: JSON.stringify(payload) });
    if (!resp.ok) {
      let msg = `${resp.status} ${resp.statusText}`;
      try { msg = (await resp.json()).detail || msg; } catch {}
      throw new Error(msg);
    }
    const blob = await resp.blob();
    const objUrl = URL.createObjectURL(blob);
    chrome.runtime.sendMessage({
      type: 'download',
      url: objUrl,
      filename: `${payload.filename}.csv`,
    });
    $('taskArea').classList.remove('hidden');
    $('progressBar').style.width = '100%';
    $('taskStatus').textContent = '完成（CSV 直传）';
  } catch (e) {
    showErr(`CSV 导出失败: ${e.message}`);
  }
}

async function cancelCurrent() {
  if (!currentTask) return;
  try {
    await cancelTask(settings.serverUrl, currentTask);
  } catch (e) {
    showErr(`取消失败: ${e.message}`);
  }
}

document.querySelectorAll('.tab').forEach((t) => {
  t.addEventListener('click', () => setActiveTab(t.dataset.mode));
});
$('btnLoadSchema').addEventListener('click', loadSchema);
$('btnLoadParts').addEventListener('click', () => {
  const table = $('tableName').value.trim();
  if (table) loadPartitions(table);
});
$('selectAll').addEventListener('click', (e) => {
  e.preventDefault();
  document.querySelectorAll('#columnList input[type="checkbox"]').forEach((el) => (el.checked = true));
});
$('selectNone').addEventListener('click', (e) => {
  e.preventDefault();
  document.querySelectorAll('#columnList input[type="checkbox"]').forEach((el) => (el.checked = false));
});
$('btnExport').addEventListener('click', startExport);
$('btnCancel').addEventListener('click', cancelCurrent);
$('btnOptions').addEventListener('click', () => chrome.runtime.openOptionsPage());
$('goOptions').addEventListener('click', (e) => {
  e.preventDefault();
  chrome.runtime.openOptionsPage();
});
$('profileSel').addEventListener('change', async (e) => {
  settings.activeProfile = e.target.value;
  await saveSettings(settings);
  schema = null;
  $('columnArea').classList.add('hidden');
  $('partitionArea').classList.add('hidden');
  $('estimate').classList.add('hidden');
});

(async () => {
  settings = await loadSettings();
  if (!settings.activeProfile && settings.profiles[0]) {
    settings.activeProfile = settings.profiles[0].name;
  }
  renderProfileSelect();
})();
