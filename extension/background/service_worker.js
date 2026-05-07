// 点击扩展图标时打开侧边栏（替代原 popup）
chrome.runtime.onInstalled.addListener(() => {
  chrome.sidePanel
    .setPanelBehavior({ openPanelOnActionClick: true })
    .catch(() => {});
});

// 兜底：某些 Chrome 版本需要显式 open
chrome.action.onClicked.addListener(async (tab) => {
  try {
    if (tab && tab.windowId != null) {
      await chrome.sidePanel.open({ windowId: tab.windowId });
    }
  } catch (e) {
    // setPanelBehavior 已生效的版本此处会报错，忽略即可
  }
});

// 接收 sidepanel 发来的下载请求
chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (msg && msg.type === 'download') {
    chrome.downloads.download(
      {
        url: msg.url,
        filename: msg.filename || undefined,
        saveAs: false,
      },
      (downloadId) => {
        if (chrome.runtime.lastError) {
          sendResponse({ ok: false, error: chrome.runtime.lastError.message });
        } else {
          sendResponse({ ok: true, downloadId });
        }
      }
    );
    return true;
  }
});
