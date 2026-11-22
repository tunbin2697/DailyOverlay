(() => {
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

  function collectYouTubeThumbnails(offsetX, offsetY) {
    const selectors = [
      "ytd-thumbnail a#thumbnail",
      "yt-thumbnail-view-model",
      "yt-lockup-view-model img",
      "ytd-rich-item-renderer img.ytCoreImageHost",
      "ytd-rich-item-renderer img.yt-core-image",
      "ytd-compact-video-renderer img",
      "ytd-video-renderer img",
      "a#thumbnail img",
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
      document.querySelector(".notion-page-content");

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

  function sendSnapshot() {
    rafScheduled = false;
    if (document.visibilityState !== "visible") return;
    if (!chrome || !chrome.runtime || !chrome.runtime.sendMessage) return;

    const host = window.location.hostname.toLowerCase();
    const { offsetX, offsetY } = getScreenOffsets();

    let elements = [];
    let margins = [];

    if (host.includes("youtube.com")) {
      elements = collectYouTubeThumbnails(offsetX, offsetY);
    } else if (host.includes("docs.google.com") || host.includes("notion.so")) {
      margins = collectDocumentMargins(offsetX, offsetY);
    }

    try {
      chrome.runtime.sendMessage(
        {
          type: "dom_context",
          host,
          url: window.location.href,
          title: document.title,
          is_scrolling: isScrolling,
          elements,
          margins,
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
    requestAnimationFrame(sendSnapshot);
  }

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
      sendSnapshot();
    }
  }, 1500);

  setTimeout(sendSnapshot, 350);
})();
