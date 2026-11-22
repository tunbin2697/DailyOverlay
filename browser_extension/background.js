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
  if (msg && msg.type === "dom_context") {
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
