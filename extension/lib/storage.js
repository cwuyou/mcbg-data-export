const STORAGE_KEY = 'mcbg_settings_v1';
const XOR_KEY = 'mcbg-export-obfuscation-key-not-for-security';

function xorObfuscate(plain) {
  if (!plain) return '';
  const out = [];
  for (let i = 0; i < plain.length; i++) {
    out.push(plain.charCodeAt(i) ^ XOR_KEY.charCodeAt(i % XOR_KEY.length));
  }
  return btoa(String.fromCharCode(...out));
}

function xorDeobfuscate(cipher) {
  if (!cipher) return '';
  try {
    const bytes = atob(cipher);
    const out = [];
    for (let i = 0; i < bytes.length; i++) {
      out.push(String.fromCharCode(bytes.charCodeAt(i) ^ XOR_KEY.charCodeAt(i % XOR_KEY.length)));
    }
    return out.join('');
  } catch {
    return '';
  }
}

export const DEFAULT_SETTINGS = {
  serverUrl: 'http://127.0.0.1:19527',
  activeProfile: '',
  profiles: [],
};

export async function loadSettings() {
  const { [STORAGE_KEY]: raw } = await chrome.storage.local.get(STORAGE_KEY);
  if (!raw) return { ...DEFAULT_SETTINGS };
  const settings = { ...DEFAULT_SETTINGS, ...raw };
  settings.profiles = (settings.profiles || []).map((p) => ({
    ...p,
    access_key: xorDeobfuscate(p.access_key_cipher || ''),
  }));
  return settings;
}

export async function saveSettings(settings) {
  const toStore = {
    serverUrl: settings.serverUrl,
    activeProfile: settings.activeProfile,
    profiles: (settings.profiles || []).map((p) => ({
      name: p.name,
      endpoint: p.endpoint,
      project: p.project,
      access_id: p.access_id,
      access_key_cipher: xorObfuscate(p.access_key || ''),
    })),
  };
  await chrome.storage.local.set({ [STORAGE_KEY]: toStore });
}

export function getActiveProfile(settings) {
  if (!settings.profiles || !settings.profiles.length) return null;
  const byName = settings.profiles.find((p) => p.name === settings.activeProfile);
  return byName || settings.profiles[0];
}
