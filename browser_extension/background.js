const WS_URL = "ws://127.0.0.1:18765";
let ws = null;
let lastPayload = null;

function ensureConnected() {
  if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) {
    return;
  }
  try {
    ws = new WebSocket(WS_URL);
    ws.addEventListener("open", () => {
      if (lastPayload) {
        try {
          ws.send(lastPayload);
        } catch (_) {}
      }
    });
    ws.addEventListener("close", () => {
      ws = null;
    });
    ws.addEventListener("error", () => {
      try {
        ws.close();
      } catch (_) {}
      ws = null;
    });
  } catch (_) {
    ws = null;
  }
}

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (msg && (msg.type === "dom_context" || msg.type === "layout_scan")) {
    lastPayload = JSON.stringify(msg);
    ensureConnected();
    if (ws && ws.readyState === WebSocket.OPEN) {
      try {
        ws.send(lastPayload);
        sendResponse({ ok: true });
        return false;
      } catch (_) {}
    }
    sendResponse({ ok: false });
  }
  return false;
});

// Clicking extension icon scans ANY active website's Zero-PII geometric wireframe
chrome.action.onClicked.addListener(async (tab) => {
  if (!tab || !tab.id || !tab.url || !tab.url.startsWith("http")) return;
  try {
    await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      files: ["content.js"],
    });
    await chrome.tabs.sendMessage(tab.id, { action: "scan_layout_now" });
    await chrome.action.setBadgeText({ tabId: tab.id, text: "OK" });
    await chrome.action.setBadgeBackgroundColor({ tabId: tab.id, color: "#27AE60" });
    setTimeout(() => {
      chrome.action.setBadgeText({ tabId: tab.id, text: "" }).catch(() => {});
    }, 2200);
  } catch (_) {
    try {
      await chrome.action.setBadgeText({ tabId: tab.id, text: "ERR" });
      await chrome.action.setBadgeBackgroundColor({ tabId: tab.id, color: "#E74C3C" });
      setTimeout(() => {
        chrome.action.setBadgeText({ tabId: tab.id, text: "" }).catch(() => {});
      }, 2200);
    } catch (_) {}
  }
});
