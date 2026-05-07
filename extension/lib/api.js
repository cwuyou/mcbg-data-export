export function authHeaders(profile) {
  return {
    'X-ODPS-Access-Id': profile.access_id,
    'X-ODPS-Access-Key': profile.access_key,
    'X-ODPS-Endpoint': profile.endpoint,
    'X-ODPS-Project': profile.project,
  };
}

async function handleResp(resp) {
  if (!resp.ok) {
    let msg = `${resp.status} ${resp.statusText}`;
    try {
      const body = await resp.json();
      if (body && body.detail) msg = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail);
    } catch {}
    throw new Error(msg);
  }
  return resp.json();
}

export async function health(serverUrl) {
  const r = await fetch(`${serverUrl}/api/health`);
  return handleResp(r);
}

export async function fetchSchema(serverUrl, profile, { table, partition }) {
  const r = await fetch(`${serverUrl}/api/schema`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...authHeaders(profile) },
    body: JSON.stringify({ table, partition: partition || null }),
  });
  return handleResp(r);
}

export async function fetchPartitions(serverUrl, profile, { table }) {
  const r = await fetch(`${serverUrl}/api/partitions`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...authHeaders(profile) },
    body: JSON.stringify({ table }),
  });
  return handleResp(r);
}

export async function createExportTask(serverUrl, profile, payload) {
  const r = await fetch(`${serverUrl}/api/export`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...authHeaders(profile) },
    body: JSON.stringify(payload),
  });
  return handleResp(r);
}

export function streamCsvUrl(serverUrl) {
  return `${serverUrl}/api/export/stream`;
}

export function taskEventsUrl(serverUrl, taskId) {
  return `${serverUrl}/api/task/${taskId}/events`;
}

export function downloadUrl(serverUrl, taskId) {
  return `${serverUrl}/api/download/${taskId}`;
}

export async function cancelTask(serverUrl, taskId) {
  const r = await fetch(`${serverUrl}/api/task/${taskId}`, { method: 'DELETE' });
  return handleResp(r);
}

export function subscribeTask(serverUrl, taskId, onUpdate, onError) {
  const es = new EventSource(taskEventsUrl(serverUrl, taskId));
  const close = () => es.close();
  es.addEventListener('update', (ev) => {
    try {
      const snap = JSON.parse(ev.data);
      onUpdate(snap);
      if (snap && ['done', 'failed', 'cancelled'].includes(snap.status)) {
        close();
      }
    } catch (e) {
      onError && onError(e);
    }
  });
  es.addEventListener('error', (e) => {
    onError && onError(e);
  });
  return close;
}
