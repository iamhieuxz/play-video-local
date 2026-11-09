// Giao diện: danh sách phim, tìm kiếm, trình phát Plyr, lưu tiến độ xem.
// Quy tắc: dữ liệu từ server chỉ được đưa vào DOM qua textContent / setAttribute, không dùng innerHTML.
"use strict";

const PAGE_SIZE = 60;
const SAVE_EVERY_MS = 10_000;
const MIN_RESUME_SECONDS = 5;

const SVG_NS = "http://www.w3.org/2000/svg";

const $ = (id) => document.getElementById(id);
const els = {
  search: $("search"),
  searchClear: $("search-clear"),
  scanBtn: $("scan-btn"),
  status: $("status"),
  grid: $("grid"),
  count: $("count"),
  moreBtn: $("more-btn"),
  emptyState: $("empty-state"),
  emptyTitle: $("empty-title"),
  emptyText: $("empty-text"),
  continueSection: $("continue-section"),
  continueRow: $("continue-row"),
  modal: $("player-modal"),
  title: $("player-title"),
  sub: $("player-sub"),
  closeBtn: $("close-btn"),
  rotateBtn: $("rotate-btn"),
  playerFrame: $("player-frame"),
  playerStage: $("player-stage"),
  video: $("player"),
};

const state = { query: "", offset: 0, total: 0, loading: false, requestId: 0 };
const player = { plyr: null, movie: null, timer: null, seekTimer: null, lastSaved: -1, lastFocus: null };

// ----------------------------------------------------------------------------- helpers
async function api(path, options) {
  const res = await fetch(path, options);
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return res.status === 204 ? null : res.json();
}

function formatTime(seconds) {
  const s = Math.max(0, Math.floor(seconds || 0));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = String(s % 60).padStart(2, "0");
  return h ? `${h}:${String(m).padStart(2, "0")}:${sec}` : `${m}:${sec}`;
}

function setStatus(text) {
  els.status.textContent = text;
}

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

// Icon SVG dựng bằng DOM (không dùng innerHTML) để giữ nguyên CSP.
function icon(paths, { viewBox = "0 0 24 24", strokeWidth = 1.8, fill = "none" } = {}) {
  const svg = document.createElementNS(SVG_NS, "svg");
  svg.setAttribute("viewBox", viewBox);
  svg.setAttribute("fill", fill);
  svg.setAttribute("stroke", "currentColor");
  svg.setAttribute("stroke-width", String(strokeWidth));
  svg.setAttribute("stroke-linecap", "round");
  svg.setAttribute("stroke-linejoin", "round");
  svg.setAttribute("aria-hidden", "true");
  for (const d of paths) {
    const p = document.createElementNS(SVG_NS, "path");
    p.setAttribute("d", d);
    svg.append(p);
  }
  return svg;
}

const ICONS = {
  play: ["M8 5.5v13l11-6.5z"],
  film: ["M2.5 4.5h19v15h-19z", "M7 4.5v15M17 4.5v15M2.5 9.5h4.5M2.5 14.5h4.5M17 9.5h4.5M17 14.5h4.5"],
  captions: ["M3 4.5h18v15H3z", "M7.5 10.5a4 4 0 0 0 0 3M16.5 10.5a4 4 0 0 1 0 3"],
  refresh: ["M20 11a8 8 0 1 0-2.3 6.3", "M20 5v6h-6"],
};

// ------------------------------------------------------------------------------- cards
function createCard(movie, { showResume = false } = {}) {
  const card = el("button", "card");
  card.type = "button";
  card.setAttribute("aria-label", showResume
    ? `${movie.title} — xem tiếp từ ${formatTime(movie.position)}`
    : `Phát ${movie.title}`);
  card.addEventListener("click", () => openPlayer(movie.id, card));

  const fallbackIcon = () => icon(ICONS.film, { strokeWidth: 1.4, fill: "none" });

  const thumb = el("div", "card-thumb");
  if (movie.thumbnail_url) {
    const img = el("img");
    img.src = movie.thumbnail_url;
    img.alt = "";
    img.loading = "lazy";
    img.decoding = "async";
    img.width = 480;
    img.height = 270;
    img.addEventListener("error", () => {
      img.remove();
      const fb = fallbackIcon();
      fb.classList.add("card-fallback");
      thumb.append(fb);
    });
    thumb.append(img);
  } else {
    const fb = fallbackIcon();
    fb.classList.add("card-fallback");
    thumb.append(fb);
  }

  const play = el("span", "card-play");
  play.append(icon(ICONS.play, { strokeWidth: 0, fill: "currentColor" }));
  thumb.append(play);

  if (movie.resolution) thumb.append(el("span", "card-badge card-badge--res", movie.resolution));
  if (movie.has_subtitle) {
    const sub = el("span", "card-badge card-badge--sub");
    sub.append(icon(ICONS.captions, { strokeWidth: 2 }));
    sub.title = "Có phụ đề";
    thumb.append(sub);
  }
  if (movie.duration) thumb.append(el("span", "card-badge card-badge--time", formatTime(movie.duration)));

  if (showResume && movie.duration) {
    const track = el("div", "progress-track");
    const bar = el("div", "progress-bar");
    const pct = Math.min(100, Math.max(0, (movie.position / movie.duration) * 100));
    bar.style.width = `${pct}%`;
    track.append(bar);
    thumb.append(track);
  }

  const body = el("div", "card-body");
  body.append(el("p", "card-title", movie.title));

  const meta = el("div", "card-meta");
  const metaText = showResume
    ? `Xem tiếp từ ${formatTime(movie.position)}`
    : movie.folder_category || "";
  if (metaText) meta.append(el("span", null, metaText));
  if (meta.childElementCount) body.append(meta);

  card.append(thumb, body);
  card.dataset.title = movie.title;
  card.dataset.category = movie.folder_category || "";
  return card;
}

function createSkeletons(container, count) {
  const frag = document.createDocumentFragment();
  for (let i = 0; i < count; i++) {
    const card = el("div", "skeleton-card");
    card.append(el("div", "skeleton-thumb"), el("div", "skeleton-line"), el("div", "skeleton-line"));
    frag.append(card);
  }
  container.append(frag);
}

// ------------------------------------------------------------------------------- lists
function showEmpty(query) {
  const searching = Boolean(query);
  els.emptyTitle.textContent = searching ? "Không tìm thấy phim phù hợp" : "Chưa có phim nào";
  els.emptyText.replaceChildren();
  if (searching) {
    els.emptyText.append(document.createTextNode("Thử từ khoá khác, hoặc "));
    const btn = el("button", "btn btn-ghost", "xoá tìm kiếm");
    btn.type = "button";
    btn.style.minHeight = "2rem";
    btn.style.padding = "0 0.75rem";
    btn.addEventListener("click", () => {
      els.search.value = "";
      els.searchClear.hidden = true;
      state.query = "";
      loadMovies(true);
      els.search.focus();
    });
    els.emptyText.append(btn);
  } else {
    els.emptyText.append(document.createTextNode("Bỏ file phim vào thư mục "));
    els.emptyText.append(el("code", null, "media/"));
    els.emptyText.append(document.createTextNode(" (hoặc đặt "));
    els.emptyText.append(el("code", null, "MEDIA_DIRS"));
    els.emptyText.append(document.createTextNode("), rồi bấm “Quét ổ cứng”."));
  }
  els.emptyState.classList.remove("hidden");
}

async function loadMovies(reset) {
  if (state.loading && !reset) return;
  if (reset) {
    state.offset = 0;
    els.grid.replaceChildren();
    els.emptyState.classList.add("hidden");
    createSkeletons(els.grid, 10);
  }
  const requestId = ++state.requestId;
  state.loading = true;
  try {
    const params = new URLSearchParams({ q: state.query, limit: PAGE_SIZE, offset: state.offset });
    const data = await api(`/api/movies?${params}`);
    if (requestId !== state.requestId) return; // đã có yêu cầu mới hơn (gõ tìm kiếm nhanh)
    if (reset) els.grid.replaceChildren();
    els.grid.append(...data.items.map((m) => createCard(m)));
    state.offset += data.items.length;
    state.total = data.total;
    els.moreBtn.classList.toggle("hidden", state.offset >= state.total);
    els.count.textContent = data.total ? `${data.total} phim` : "";
    setStatus(data.total === 0 ? "" : `Đang hiển thị ${state.offset}/${data.total} phim`);
    if (data.total === 0) showEmpty(state.query);
  } catch (err) {
    if (requestId !== state.requestId) return;
    if (reset) els.grid.replaceChildren();
    setStatus("Không tải được danh sách phim. Kiểm tra server rồi thử lại.");
    console.error(err);
  } finally {
    if (requestId === state.requestId) state.loading = false;
  }
}

async function loadContinue() {
  try {
    const items = await api("/api/continue");
    els.continueRow.replaceChildren(...items.map((m) => createCard(m, { showResume: true })));
    els.continueSection.classList.toggle("hidden", items.length === 0);
  } catch (err) {
    console.error(err);
  }
}

// ------------------------------------------------------------------------------ player
function saveProgress({ keepalive = false } = {}) {
  const { plyr, movie } = player;
  if (!plyr || !movie) return;
  const position = plyr.currentTime;
  if (!Number.isFinite(position) || position < 0 || Math.abs(position - player.lastSaved) < 0.5) return;
  player.lastSaved = position;
  const duration = Number.isFinite(plyr.duration) && plyr.duration > 0 ? plyr.duration : null;
  fetch("/api/progress", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ movie_id: movie.id, position, duration }),
    keepalive,
  }).catch(() => {});
}

function ensurePlyr() {
  if (player.plyr) return player.plyr;
  player.plyr = new Plyr(els.video, {
    // Phím tắt do handleShortcuts() bên dưới xử lý: Plyr không gắn tabindex vào khung hình
    // nên tuỳ chọn `focused` của nó không bao giờ kích hoạt, để cả hai bắt phím sẽ bị nhân đôi.
    keyboard: { focused: false, global: false },
    seekTime: 10,
    speed: { selected: 1, options: [0.5, 0.75, 1, 1.25, 1.5, 2] },
    controls: ["play-large", "play", "progress", "current-time", "duration", "mute", "volume", "captions", "settings", "pip", "airplay", "fullscreen"],
    // Mặc định bật tiếng Việt; người dùng chọn ngôn ngữ khác thì Plyr nhớ cho các phim sau.
    captions: { active: true, language: "vi", update: true },
    i18n: { restart: "Phát lại", rewind: "Lùi {seektime}s", play: "Phát", pause: "Tạm dừng", fastForward: "Tua {seektime}s", seek: "Tua", speed: "Tốc độ", normal: "Bình thường", captions: "Phụ đề", settings: "Cài đặt", mute: "Tắt tiếng", unmute: "Bật tiếng", enterFullscreen: "Toàn màn hình", exitFullscreen: "Thoát toàn màn hình" },
  });
  player.plyr.on("pause", () => saveProgress());
  player.plyr.on("ended", () => saveProgress());
  // Gắn tap-to-seek SAU KHI Plyr đã tạo xong DOM wrapper
  attachTapToSeek();
  return player.plyr;
}

function updatePlayerSub() {
  if (!player.movie) {
    els.sub.textContent = "";
    return;
  }
  const langCount = (player.movie.subtitles || []).length;
  const parts = [
    player.movie.folder_category,
    player.movie.resolution,
    player.movie.duration ? formatTime(player.movie.duration) : null,
    langCount ? `${langCount} phụ đề` : null,
  ].filter(Boolean);
  els.sub.textContent = parts.join(" · ");
}

async function openPlayer(movieId, trigger) {
  let movie;
  try {
    movie = await api(`/api/movies/${movieId}`);
  } catch (err) {
    setStatus("Không mở được phim này.");
    console.error(err);
    return;
  }

  player.movie = movie;
  player.lastSaved = -1;
  player.lastFocus = trigger || null;
  els.title.textContent = movie.title;
  updatePlayerSub();
  els.modal.classList.remove("hidden");
  document.body.classList.add("modal-open");

  const plyr = ensurePlyr();
  const resumeAt = movie.position >= MIN_RESUME_SECONDS ? movie.position : 0;
  const sources = {
    type: "video",
    title: movie.title,
    sources: [{ src: `/stream/${movie.id}` }],
    // Mỗi ngôn ngữ một track; track mặc định (tiếng Việt nếu có) tự bật.
    tracks: (movie.subtitles || []).map((s) => ({
      kind: "captions",
      label: s.label,
      srclang: s.lang,
      src: s.url,
      default: s.default,
    })),
  };
  plyr.source = sources;

  // Plyr có thể bắn loadedmetadata khi duration còn 0 (chưa có dữ liệu media),
  // lúc đó gán currentTime bị bỏ qua nên phải chờ tới khi seek được.
  if (resumeAt > 0) {
    const seekToResume = () => {
      if (!Number.isFinite(plyr.duration) || plyr.duration <= 0) return false;
      if (resumeAt < plyr.duration) plyr.currentTime = resumeAt;
      return true;
    };
    if (!seekToResume()) {
      let attempts = 0;
      player.seekTimer = setInterval(() => {
        attempts += 1;
        if (seekToResume() || attempts > 40) {
          clearInterval(player.seekTimer);
          player.seekTimer = null;
        }
      }, 150);
    }
  }

  clearInterval(player.timer);
  player.timer = setInterval(() => {
    if (player.plyr && player.plyr.playing) saveProgress();
  }, SAVE_EVERY_MS);

  // Đưa focus ra khỏi thẻ phim (đang là <button>) để phím tắt bắt được, và vào đúng hộp thoại.
  els.modal.focus({ preventScroll: true });
}

function closePlayer() {
  if (els.modal.classList.contains("hidden")) return;
  // Đóng trước khi media kịp nạp: giữ nguyên vị trí cũ thay vì ghi đè bằng 0.
  if (player.plyr && player.movie && (player.plyr.duration || 0) > 0) saveProgress({ keepalive: true });
  clearInterval(player.timer);
  player.timer = null;
  clearInterval(player.seekTimer);
  player.seekTimer = null;
  if (player.plyr) {
    player.plyr.stop();
  }
  player.movie = null;
  resetRotation();
  els.modal.classList.add("hidden");
  document.body.classList.remove("modal-open");
  if (player.lastFocus && document.contains(player.lastFocus)) player.lastFocus.focus();
  // Chờ server ghi xong tiến độ rồi mới làm mới "Xem tiếp".
  setTimeout(loadContinue, 400);
}

// ----------------------------------------------------------------------------- events
let searchTimer = null;
els.search.addEventListener("input", () => {
  els.searchClear.hidden = !els.search.value;
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => {
    state.query = els.search.value.trim();
    loadMovies(true);
  }, 250);
});

els.searchClear.addEventListener("click", () => {
  els.search.value = "";
  els.searchClear.hidden = true;
  state.query = "";
  loadMovies(true);
  els.search.focus();
});

els.moreBtn.addEventListener("click", () => loadMovies(false));
els.closeBtn.addEventListener("click", closePlayer);

// Phím tắt trình phát, chỉ có hiệu lực lúc modal đang mở.
function handleShortcuts(event) {
  const plyr = player.plyr;
  if (!plyr || els.modal.classList.contains("hidden")) return;
  if (event.ctrlKey || event.metaKey || event.altKey || event.shiftKey) return;

  const target = event.target;
  if (target instanceof Element) {
    // Không cướp phím lúc đang gõ, và để Space kích hoạt nút như mặc định.
    if (target.closest("input, textarea, select, [contenteditable]")) return;
    if (event.key === " " && target.closest("button, a[href]")) return;
  }

  const time = plyr.currentTime;
  const volume = plyr.volume;

  switch (event.key) {
    case "ArrowLeft":
    case "ArrowRight": {
      const step = plyr.config.seekTime;
      const next = event.key === "ArrowLeft" ? time - step : time + step;
      plyr.currentTime = Math.max(0, Math.min(plyr.duration || time, next));
      break;
    }
    case "ArrowUp":
    case "ArrowDown":
      plyr.muted = false;
      plyr.volume = Math.max(0, Math.min(1, event.key === "ArrowDown" ? volume - 0.1 : volume + 0.1));
      break;
    case " ":
      plyr.togglePlay();
      break;
    case "m":
    case "M":
      plyr.muted = !plyr.muted;
      break;
    case "c":
    case "C":
      // Đi qua API của Plyr để nút phụ đề và trạng thái bên trong nó không bị lệch.
      plyr.toggleCaptions();
      break;
    default:
      return;
  }
  event.preventDefault();
}

// ----------------------------------------------------------------------- mobile: rotate video
const ROTATE_STEPS = [0, 90, 180, 270];
let rotateIndex = 0;

function applyRotation() {
  const deg = ROTATE_STEPS[rotateIndex];
  if (deg === 0) {
    els.playerFrame.removeAttribute("data-rotate");
  } else {
    els.playerFrame.setAttribute("data-rotate", String(deg));
  }
}

function resetRotation() {
  rotateIndex = 0;
  applyRotation();
}

els.rotateBtn.addEventListener("click", () => {
  rotateIndex = (rotateIndex + 1) % ROTATE_STEPS.length;
  applyRotation();
});

// ----------------------------------------------------------------------------- events
document.addEventListener("keydown", handleShortcuts);

document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && !els.modal.classList.contains("hidden")) {
    // Esc trong chế độ toàn màn hình chỉ thoát fullscreen, không đóng trình phát.
    if (document.fullscreenElement) return;
    resetRotation();
    closePlayer();
  }
});

// Đóng tab / chuyển app trên điện thoại: lưu vị trí lần cuối.
window.addEventListener("pagehide", () => saveProgress({ keepalive: true }));
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "hidden") saveProgress({ keepalive: true });
});

els.scanBtn.addEventListener("click", async () => {
  els.scanBtn.disabled = true;
  els.scanBtn.setAttribute("aria-busy", "true");
  els.scanBtn.querySelector("span").textContent = "Đang quét…";
  setStatus("Đang quét ổ cứng…");
  try {
    await api("/api/scan", { method: "POST" });
    for (let i = 0; i < 600; i++) {
      await new Promise((r) => setTimeout(r, 1500));
      const { running } = await api("/api/scan/status");
      if (!running) break;
      await loadMovies(true); // hiển thị dần phim mới tìm thấy
    }
    await Promise.all([loadMovies(true), loadContinue()]);
  } catch (err) {
    setStatus("Quét thất bại. Kiểm tra server rồi thử lại.");
    console.error(err);
  } finally {
    els.scanBtn.disabled = false;
    els.scanBtn.removeAttribute("aria-busy");
    els.scanBtn.querySelector("span").textContent = "Quét ổ cứng";
  }
});

// ----------------------------------------------------------------------- mobile: tap-to-seek
// Gắn listener TRỰC TIẾP lên video element — hoạt động cả trong fullscreen
// vì video element luôn tồn tại trong DOM dù ở chế độ nào.
// Dùng click event (không phải pointerup) vì click luôn fire trong fullscreen.
const TAP_SEEK_SECONDS = 10;
const TAP_ZONE_RATIO = 0.4; // 40% mỗi bên, 20% giữa là vùng chết
const TAP_ACCUMULATE_MS = 1200; // Reset bộ đếm cộng dồn sau 1.2s không tap
let tapFeedbackTimer = null;
let tapAccumulated = { side: null, total: 0, timer: null };

/**
 * Hiển thị feedback cộng dồn bên trong Plyr container.
 * Tap liên tục cùng bên: 10s → 20s → 30s...
 * Đổi bên hoặc hết thời gian → reset.
 */
function showTapFeedbackInPlyr(side, cumulativeSeconds) {
  const plyrEl = els.video.closest(".plyr");
  if (!plyrEl) return;

  // Overlay nền tối nhẹ
  let overlay = plyrEl.querySelector(".tap-overlay");
  if (!overlay) {
    overlay = document.createElement("div");
    overlay.className = "tap-overlay";
    overlay.setAttribute("aria-hidden", "true");
    plyrEl.appendChild(overlay);
  }
  overlay.classList.add("visible");

  // Feedback text
  let fb = plyrEl.querySelector(`.tap-feedback--${side}`);
  if (!fb) {
    fb = document.createElement("div");
    fb.className = `tap-feedback tap-feedback--${side}`;
    fb.setAttribute("aria-hidden", "true");
    plyrEl.appendChild(fb);
  }

  fb.replaceChildren();
  const arrow = icon(
    side === "left" ? ["M15 18l-6-6 6-6"] : ["M9 18l6-6-6-6"],
    { strokeWidth: 2.5 }
  );
  fb.append(arrow, document.createTextNode(`${cumulativeSeconds}s`));
  fb.classList.add("visible");

  // Ẩn feedback phía đối diện nếu đang hiện
  const otherSide = side === "left" ? "right" : "left";
  const otherFb = plyrEl.querySelector(`.tap-feedback--${otherSide}`);
  if (otherFb) otherFb.classList.remove("visible");

  // Ripple
  const ripple = document.createElement("div");
  ripple.className = "tap-ripple";
  ripple.style.left = side === "left" ? "20%" : "80%";
  ripple.style.top = "50%";
  plyrEl.appendChild(ripple);
  ripple.addEventListener("animationend", () => ripple.remove());

  // Reset timer
  clearTimeout(tapFeedbackTimer);
  tapFeedbackTimer = setTimeout(() => {
    plyrEl.querySelectorAll(".tap-feedback").forEach((el) => el.classList.remove("visible"));
    if (overlay) overlay.classList.remove("visible");
  }, 800);
}

function handleTapSeek(side) {
  const plyr = player.plyr;
  if (!plyr || els.modal.classList.contains("hidden")) return;
  if (!Number.isFinite(plyr.duration) || plyr.duration <= 0) return;

  // Cộng dồn nếu cùng bên, reset nếu đổi bên
  if (tapAccumulated.side === side) {
    tapAccumulated.total += TAP_SEEK_SECONDS;
  } else {
    tapAccumulated.side = side;
    tapAccumulated.total = TAP_SEEK_SECONDS;
  }

  // Reset timer cộng dồn
  clearTimeout(tapAccumulated.timer);
  tapAccumulated.timer = setTimeout(() => {
    tapAccumulated.side = null;
    tapAccumulated.total = 0;
  }, TAP_ACCUMULATE_MS);

  const step = TAP_SEEK_SECONDS;
  const next = side === "left" ? plyr.currentTime - step : plyr.currentTime + step;
  plyr.currentTime = Math.max(0, Math.min(plyr.duration, next));
  showTapFeedbackInPlyr(side, tapAccumulated.total);
}

/**
 * Tap-to-seek: gắn touch listener TRÊN DOCUMENT với capture phase.
 *
 * Lý do không gắn trên .plyr hay video:
 * - Plyr fullscreen di chuyển DOM ra khỏi cây ban đầu
 * - Video native controls có thể nuốt touch events
 * - Document capture phase BẮT MỌI touch trước khi bất kỳ element nào xử lý
 *
 * preventDefault() trên touchend (capture, non-passive) ngăn browser tạo
 * synthetic click → Plyr không toggle play/pause.
 */
let touchStartX = 0;
let touchStartY = 0;
let touchStartTime = 0;

// Gắn MỘT LẦN duy nhất trên document — không phụ thuộc vào Plyr lifecycle
document.addEventListener("touchstart", (e) => {
  if (els.modal.classList.contains("hidden")) return;
  if (!player.plyr) return;
  if (e.touches.length !== 1) return;

  // Chỉ xử lý nếu touch nằm trong vùng player stage hoặc Plyr container
  const target = e.target;
  const plyrEl = player.plyr.elements?.container;
  const inPlayer = (target && els.playerStage.contains(target)) ||
    (plyrEl && plyrEl.contains(target));
  if (!inPlayer) return;

  touchStartX = e.touches[0].clientX;
  touchStartY = e.touches[0].clientY;
  touchStartTime = Date.now();
  console.log("[TAP] touchstart", { x: touchStartX, y: touchStartY, target: target?.className || target?.tagName });
}, { passive: true });

document.addEventListener("touchend", (e) => {
  if (els.modal.classList.contains("hidden")) { console.log("[TAP] modal hidden"); return; }
  const plyr = player.plyr;
  if (!plyr) { console.log("[TAP] no plyr"); return; }
  if (!Number.isFinite(plyr.duration) || plyr.duration <= 0) { console.log("[TAP] bad duration:", plyr.duration); return; }

  const touch = e.changedTouches[0];
  if (!touch) return;

  // Kiểm tra target thuộc về player
  const target = e.target;
  const plyrEl = plyr.elements?.container;
  const inPlayer = (target && els.playerStage.contains(target)) ||
    (plyrEl && plyrEl.contains(target));
  if (!inPlayer) { console.log("[TAP] not in player, target:", target?.className); return; }

  const dx = Math.abs(touch.clientX - touchStartX);
  const dy = Math.abs(touch.clientY - touchStartY);
  const dt = Date.now() - touchStartTime;

  console.log("[TAP] touchend", { dx, dy, dt, clientX: touch.clientX, clientY: touch.clientY });

  // Không phải tap nếu kéo quá xa hoặc giữ quá lâu
  if (dx > 20 || dy > 20 || dt > 350) { console.log("[TAP] not a tap (drag/long)"); return; }

  // Tính vị trí tap so với Plyr container (không phải <video> vì Plyr ẩn nó)
  const refEl = plyr.elements?.container;
  const refRect = (refEl && refEl.getBoundingClientRect().width > 0)
    ? refEl.getBoundingClientRect()
    : els.video.getBoundingClientRect();
  console.log("[TAP] refRect", { w: refRect.width, h: refRect.height, l: refRect.left, t: refRect.top, src: refEl ? "plyr" : "video" });
  if (refRect.width === 0 || refRect.height === 0) { console.log("[TAP] ref rect empty"); return; }

  const relX = (touch.clientX - refRect.left) / refRect.width;
  const relY = (touch.clientY - refRect.top) / refRect.height;

  console.log("[TAP] relX:", relX.toFixed(3), "relY:", relY.toFixed(3), "zones: <", TAP_ZONE_RATIO, ">", 1 - TAP_ZONE_RATIO);

  // Chỉ tua nếu tap nằm trong vùng video và ở hai bên
  if (relY >= 0 && relY <= 1) {
    if (relX < TAP_ZONE_RATIO) {
      console.log("[TAP] ✅ SEEK LEFT");
      e.preventDefault();
      e.stopPropagation();
      handleTapSeek("left");
      return;
    }
    if (relX > 1 - TAP_ZONE_RATIO) {
      console.log("[TAP] ✅ SEEK RIGHT");
      e.preventDefault();
      e.stopPropagation();
      handleTapSeek("right");
      return;
    }
    console.log("[TAP] center zone, pass through");
  } else {
    console.log("[TAP] outside video Y range");
  }
}, { passive: false, capture: true });

// attachTapToSeek giờ chỉ là no-op (giữ để ensurePlyr không lỗi)
function attachTapToSeek() {}

// Reset xoay khi đóng player bằng nút Đóng
els.closeBtn.addEventListener("click", resetRotation);

// ------------------------------------------------------------------------------- boot
loadMovies(true);
loadContinue();
