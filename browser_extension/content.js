(() => {
  if (window.__dailyOverlayInstalled) {
    return;
  }
  window.__dailyOverlayInstalled = true;

  let rafScheduled = false;
  let scrollEndTimer = null;
  let isScrolling = false;

  function getScreenOffsets() {
    const chromeHeight = Math.max(0, window.outerHeight - window.innerHeight);
    const chromeLeft = Math.max(0, Math.round((window.outerWidth - window.innerWidth) / 2));
    return {
      offsetX: window.screenX + chromeLeft,
      offsetY: window.screenY + chromeHeight,
    };
  }

  function collectMediaCards(offsetX, offsetY) {
    const selectors = [
      "ytd-thumbnail a#thumbnail",
      "yt-thumbnail-view-model",
      "yt-lockup-view-model img",
      "ytd-rich-item-renderer img.ytCoreImageHost",
      "ytd-rich-item-renderer img.yt-core-image",
      "ytd-compact-video-renderer img",
      "ytd-video-renderer img",
      "a#thumbnail img",
      "article img",
      "[data-testid*='card'] img",
      ".card img",
    ];
    const elements = [];

    for (const sel of selectors) {
      for (const el of document.querySelectorAll(sel)) {
        const r = el.getBoundingClientRect();
        if (
          r.width < 130 ||
          r.height < 72 ||
          r.width > 700 ||
          r.height > 420 ||
          r.bottom < 70 ||
          r.top > window.innerHeight - 30 ||
          r.right < 0 ||
          r.left > window.innerWidth
        ) {
          continue;
        }
        const x = Math.round(r.left + offsetX);
        const y = Math.round(r.top + offsetY);
        const w = Math.round(r.width);
        const h = Math.round(r.height);

        const duplicate = elements.some(
          (e) => Math.abs(e.x - x) < 20 && Math.abs(e.y - y) < 20
        );
        if (duplicate) continue;

        elements.push({ kind: "thumbnail", x, y, w, h });
        if (elements.length >= 14) return elements;
      }
    }
    return elements;
  }

  function collectDocumentMargins(offsetX, offsetY) {
    const pageEl =
      document.querySelector(".kix-page-paginated") ||
      document.querySelector(".kix-appview-editor") ||
      document.querySelector(".notion-page-content") ||
      document.querySelector("main article") ||
      document.querySelector("main");

    if (!pageEl) return [];
    const r = pageEl.getBoundingClientRect();
    const topY = Math.max(120, Math.round(r.top + offsetY));
    const usableH = Math.max(200, Math.round(window.innerHeight - 150));
    const margins = [];

    const leftW = Math.round(r.left - 16);
    if (leftW >= 70) {
      margins.push({
        x: Math.round(offsetX + 8),
        y: topY,
        w: Math.min(260, leftW),
        h: usableH,
      });
    }

    const rightStart = Math.round(r.right + 12);
    const rightW = Math.round(window.innerWidth - r.right - 20);
    if (rightW >= 70) {
      margins.push({
        x: Math.round(offsetX + rightStart),
        y: topY,
        w: Math.min(260, rightW),
        h: usableH,
      });
    }
    return margins;
  }

  // Strictly Zero-PII geometric wireframe: only roles and viewport boxes, NEVER reads text or URL path
  function buildZeroPiiWireframe(mediaCards, margins, offsetX, offsetY) {
    const vw = Math.max(1, window.innerWidth);
    const vh = Math.max(1, window.innerHeight);
    const blocks = [];

    const addRect = (role, rect) => {
      if (!rect || rect.width < 60 || rect.height < 28) return;
      if (rect.bottom <= 0 || rect.top >= vh || rect.right <= 0 || rect.left >= vw) return;
      blocks.push({
        role,
        box: [
          Math.max(0, Math.round(rect.left)),
          Math.max(0, Math.round(rect.top)),
          Math.min(vw, Math.round(rect.width)),
          Math.min(vh, Math.round(rect.height)),
        ],
      });
    };

    const headerEl =
      document.querySelector("header") ||
      document.querySelector("[role='banner']") ||
      document.querySelector("ytd-masthead");
    if (headerEl) addRect("header", headerEl.getBoundingClientRect());

    const sidebarEl =
      document.querySelector("aside") ||
      document.querySelector("[role='navigation']") ||
      document.querySelector("ytd-mini-guide-renderer") ||
      document.querySelector("#guide");
    if (sidebarEl) {
      const sr = sidebarEl.getBoundingClientRect();
      if (sr.width <= vw * 0.35 && sr.height >= vh * 0.35) {
        addRect("sidebar", sr);
      }
    }

    const mainEl =
      document.querySelector(".kix-page-paginated") ||
      document.querySelector(".notion-page-content") ||
      document.querySelector("main") ||
      document.querySelector("[role='main']") ||
      document.querySelector("article");
    if (mainEl) addRect("main_content", mainEl.getBoundingClientRect());

    if (mediaCards && mediaCards.length > 0) {
      let minX = vw, minY = vh, maxX = 0, maxY = 0;
      for (const c of mediaCards) {
        minX = Math.min(minX, c.x - window.screenX);
        minY = Math.min(minY, c.y - window.screenY);
        maxX = Math.max(maxX, c.x - window.screenX + c.w);
        maxY = Math.max(maxY, c.y - window.screenY + c.h);
      }
      if (maxX > minX && maxY > minY) {
        blocks.push({
          role: "media_cards",
          box: [
            Math.max(0, Math.round(minX)),
            Math.max(0, Math.round(minY)),
            Math.round(maxX - minX),
            Math.round(maxY - minY),
          ],
        });
      }
    }

    if (margins && margins.length > 0) {
      for (const m of margins) {
        blocks.push({
          role: "empty_space",
          box: [
            Math.max(0, Math.round(m.x - offsetX)),
            Math.max(0, Math.round(m.y - offsetY)),
            Math.round(m.w),
            Math.round(m.h),
          ],
        });
      }
    }

    return {
      source: "web",
      viewport: [vw, vh],
      blocks: blocks.slice(0, 12),
    };
  }

  function sendSnapshot(msgType = "dom_context", includeWireframe = false) {
    rafScheduled = false;
    if (msgType === "dom_context" && document.visibilityState !== "visible") return;
    if (!chrome || !chrome.runtime || !chrome.runtime.sendMessage) return;

    const host = window.location.hostname.toLowerCase();
    const { offsetX, offsetY } = getScreenOffsets();

    const elements = collectMediaCards(offsetX, offsetY);
    const margins = collectDocumentMargins(offsetX, offsetY);
    const wireframe = includeWireframe ? buildZeroPiiWireframe(elements, margins, offsetX, offsetY) : null;

    try {
      chrome.runtime.sendMessage(
        {
          type: msgType,
          host,
          url: window.location.origin, // Origin only, never send query/path
          title: "",
          is_scrolling: isScrolling,
          elements,
          margins,
          wireframe,
        },
        () => {
          void chrome.runtime.lastError;
        }
      );
    } catch (_) {}
  }

  function scheduleRafSend() {
    if (rafScheduled) return;
    rafScheduled = true;
    requestAnimationFrame(() => sendSnapshot("dom_context", false));
  }

  chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
    if (msg && msg.action === "scan_layout_now") {
      sendSnapshot("layout_scan", true);
      sendResponse({ ok: true });
    }
    return false;
  });

  window.addEventListener(
    "scroll",
    () => {
      isScrolling = true;
      scheduleRafSend();
      if (scrollEndTimer) clearTimeout(scrollEndTimer);
      scrollEndTimer = setTimeout(() => {
        isScrolling = false;
        scheduleRafSend();
      }, 220);
    },
    { passive: true }
  );

  window.addEventListener("resize", scheduleRafSend, { passive: true });
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") scheduleRafSend();
  });

  setInterval(() => {
    if (document.visibilityState === "visible") {
      sendSnapshot("dom_context", false);
    }
  }, 1500);

  setTimeout(() => sendSnapshot("dom_context", true), 350);
})();
