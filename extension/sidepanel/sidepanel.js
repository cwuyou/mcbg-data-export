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
let currentAbort = null;
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

function resetTaskUI() {
  $('taskArea').classList.add('hidden');
  const bar = $('progressBar');
  bar.classList.remove('indeterminate');
  bar.style.width = '0%';
  $('taskStatus').textContent = '';
  $('btnExport').disabled = false;
  $('btnCancel').classList.add('hidden');
  showErr('');
}

function setActiveTab(m) {
  mode = m;
  document.querySelectorAll('.tab').forEach((t) => t.classList.toggle('active', t.dataset.mode === m));
  document.querySelectorAll('.pane').forEach((p) => p.classList.toggle('hidden', p.dataset.pane !== m));
  if (currentAbort) { try { currentAbort.abort(); } catch {} currentAbort = null; }
  if (currentUnsub) { try { currentUnsub(); } catch {} currentUnsub = null; }
  // SQL 模式下大表提示无意义
  if (m === 'sql') {
    $('largeTableHint').classList.add('hidden');
  } else {
    updateLargeTableHint();
  }
  resetTaskUI();
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

// xlsx 单 sheet 上限 1,048,575 行；接近这个数量时建议改 CSV
const XLSX_HINT_ROWS = 1_000_000;

function updateLargeTableHint() {
  const hint = $('largeTableHint');
  if (!hint) return;
  const fmt = document.querySelector('input[name="fmt"]:checked')?.value;
  const rows = schema && schema.row_count;
  const shouldShow = fmt === 'xlsx' && rows && rows >= XLSX_HINT_ROWS;
  hint.classList.toggle('hidden', !shouldShow);
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

    updateLargeTableHint();
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
  resetTaskUI();
  if (currentUnsub) { try { currentUnsub(); } catch {} currentUnsub = null; }
  if (currentAbort) { try { currentAbort.abort(); } catch {} currentAbort = null; }
  currentTask = null;

  const profile = getActiveProfile(settings);
  if (!profile) { showErr('请先配置 profile'); return; }
  const format = document.querySelector('input[name="fmt"]:checked').value;

  try {
    const payload = buildExportPayload(format);

    // CSV 统一走零落盘流式路径（服务端会按行数/数据量自动决定是否 gzip）
    if (format === 'csv') {
      await directDownloadCsv(profile, payload);
      return;
    }

    // xlsx 需要临时磁盘和最终文件，只能走任务模式
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

function showStreamingUI(text) {
  $('taskArea').classList.remove('hidden');
  $('taskStatus').textContent = text;
  $('btnExport').disabled = true;
  $('btnCancel').classList.remove('hidden');
}

async function directDownloadCsv(profile, payload) {
  const url = streamCsvUrl(settings.serverUrl);
  const headers = { 'Content-Type': 'application/json', ...authHeaders(profile) };
  const abort = new AbortController();
  currentAbort = abort;

  // 立即亮 UI，让用户看到"在干活"
  showStreamingUI('连接服务端...');
  // 没有 total，用不确定进度条（stripes 动画）
  const bar = $('progressBar');
  bar.classList.add('indeterminate');
  bar.style.width = '100%';

  try {
    const resp = await fetch(url, {
      method: 'POST', headers,
      body: JSON.stringify(payload),
      signal: abort.signal,
    });
    if (!resp.ok) {
      let msg = `${resp.status} ${resp.statusText}`;
      try { msg = (await resp.json()).detail || msg; } catch {}
      throw new Error(msg);
    }

    // 后端告知的压缩状态 + 真实文件名
    const compressed = resp.headers.get('X-MCBG-Compressed') === '1';
    const totalRows = resp.headers.get('X-MCBG-Total-Rows') || '';
    const sourceBytes = resp.headers.get('X-MCBG-Source-Bytes') || '';
    const serverFilename = parseFilenameFromDisposition(resp.headers.get('Content-Disposition'));
    const fallbackExt = compressed ? 'csv.gz' : 'csv';
    const outFilename = serverFilename || `${payload.filename}.${fallbackExt}`;

    if (compressed) {
      const meta = [];
      if (totalRows) meta.push(`${Number(totalRows).toLocaleString()} 行`);
      if (sourceBytes) meta.push(`存储 ${formatBytes(Number(sourceBytes))}`);
      const metaStr = meta.length ? `，${meta.join('，')}` : '';
      showStreamingUI(`导出中（已启用 gzip 压缩${metaStr}）`);
    }

    // 流式读取，边收边累积 + 更新字节计数
    const reader = resp.body.getReader();
    const chunks = [];
    let received = 0;
    let lastRender = 0;

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      chunks.push(value);
      received += value.length;
      const now = Date.now();
      if (now - lastRender > 200) {
        const suffix = compressed ? '（已压缩）' : '';
        $('taskStatus').textContent = `导出中 ${formatBytes(received)}${suffix}`;
        lastRender = now;
      }
    }

    const mime = compressed ? 'application/gzip' : 'text/csv';
    const blob = new Blob(chunks, { type: mime });
    const objUrl = URL.createObjectURL(blob);
    chrome.runtime.sendMessage({
      type: 'download',
      url: objUrl,
      filename: outFilename,
    });

    bar.classList.remove('indeterminate');
    bar.style.width = '100%';
    const finalText = compressed
      ? `完成 ${formatBytes(received)}（已压缩 ${outFilename.endsWith('.gz') ? '.gz' : ''}）`
      : `完成 ${formatBytes(received)}（CSV 直传）`;
    $('taskStatus').textContent = finalText;
    $('btnExport').disabled = false;
    $('btnCancel').classList.add('hidden');
  } catch (e) {
    bar.classList.remove('indeterminate');
    bar.style.width = '0%';
    if (e.name === 'AbortError') {
      $('taskStatus').textContent = '已取消';
    } else {
      showErr(`CSV 导出失败: ${e.message}`);
    }
    $('btnExport').disabled = false;
    $('btnCancel').classList.add('hidden');
  } finally {
    currentAbort = null;
  }
}

function parseFilenameFromDisposition(cd) {
  if (!cd) return null;
  // RFC 5987 带编码的优先：filename*=UTF-8''xxx
  const mStar = cd.match(/filename\*=UTF-8''([^;]+)/i);
  if (mStar) {
    try { return decodeURIComponent(mStar[1]); } catch { /* fallthrough */ }
  }
  const m = cd.match(/filename="?([^";]+)"?/i);
  return m ? m[1] : null;
}

async function cancelCurrent() {
  if (currentAbort) {
    try { currentAbort.abort(); } catch {}
    return;
  }
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
// 格式切换：刷新大表提示
document.querySelectorAll('input[name="fmt"]').forEach((r) => {
  r.addEventListener('change', updateLargeTableHint);
});
// 点提示里的"切到 CSV"链接
$('switchToCsv').addEventListener('click', (e) => {
  e.preventDefault();
  const csvRadio = document.querySelector('input[name="fmt"][value="csv"]');
  if (csvRadio) { csvRadio.checked = true; }
  updateLargeTableHint();
});
$('profileSel').addEventListener('change', async (e) => {
  settings.activeProfile = e.target.value;
  await saveSettings(settings);
  schema = null;
  $('columnArea').classList.add('hidden');
  $('partitionArea').classList.add('hidden');
  $('estimate').classList.add('hidden');
  $('largeTableHint').classList.add('hidden');
});

(async () => {
  settings = await loadSettings();
  if (!settings.activeProfile && settings.profiles[0]) {
    settings.activeProfile = settings.profiles[0].name;
  }
  renderProfileSelect();
})();
