/* Small, read-only account switcher on the library page. */
(function (PR) {
  "use strict";
  const finite = (n) => typeof n === "number" && Number.isFinite(n);
  const supported = (engine) => engine === "agy" || engine === "codex";
  const period = (engine, row) => {
    if (row.window_minutes === 300) return "five";
    if (row.window_minutes === 10080) return "week";
    if (engine !== "agy" || finite(row.window_minutes)) return null;
    if (row.label === "Five Hour Limit Remaining") return "five";
    if (["Weekly Limit Remaining", "Gemini 4 Argon Weekly Limit"].includes(row.label)) return "week";
    return null;
  };

  PR.homeQuotaSnapshot = function (engine, preferredGroup) {
    const data = PR.quotaState[engine] || {};
    const rows = (data.rows || []).filter((r) => period(engine, r));
    const groups = [...new Set(rows.map((r) => r.group || engine))];
    const group = groups.includes(preferredGroup) ? preferredGroup : groups[0];
    const windows = {};
    for (const row of rows.filter((r) => (r.group || engine) === group)) {
      const key = period(engine, row);
      if (windows[key] || !finite(row.remaining_percent)) continue;
      windows[key] = {
        value: Math.round(Math.max(0, Math.min(100, row.remaining_percent)) * 10) / 10,
        stale: data.status === "stale" || (finite(row.resets_at) && row.resets_at * 1000 <= Date.now()),
      };
    }
    return { groups, group, windows, loading: !!data.loading, stale: Object.values(windows).some((w) => w.stale) };
  };

  const clamp = (n) => Math.max(0, Math.min(1, finite(n) ? n : 1));
  PR.quotaCardPosition = (position, viewport, size) => ({
    left: 8 + clamp(position.x) * Math.max(0, viewport.width - size.width - 16),
    top: 8 + clamp(position.y) * Math.max(0, viewport.height - size.height - 16),
  });
  const host = PR.$("#homeQuota");
  if (!host) return;
  const saved = PR.ls.get("paperread-home-quota-engine", null);
  let engine = supported(saved) ? saved : null, ready = false, timer, libraryReady = false, prefsReady = false;
  let collapsed = false, position = null, drag = null;
  const attempted = {}, groups = {}, interval = 300000;
  host.setAttribute("aria-label", PR.t("AI 額度"));
  host.innerHTML = '<div class="hq-titlebar"><button type="button" class="hq-drag" title="' + PR.t("拖曳移動；方向鍵微調；Home 歸位") + '">' + PR.t("AI 額度") + '</button><button type="button" class="hq-toggle" aria-controls="homeQuotaBody"></button></div>' +
    '<div id="homeQuotaBody"><div class="hq-head"><div class="hq-engines" role="group" aria-label="' + PR.t("額度來源") + '">' +
    '<button type="button" data-hq-engine="codex">Codex</button><button type="button" data-hq-engine="agy">AGY</button></div>' +
    '<button type="button" class="hq-refresh" title="' + PR.t("更新額度") + '" aria-label="' + PR.t("更新額度") + '">↻</button></div>' +
    '<select class="hq-group" aria-label="' + PR.t("額度群組") + '" hidden></select><div class="hq-values"></div><div class="hq-note" role="status"></div></div>';
  const selector = host.querySelector(".hq-group"), refreshButton = host.querySelector(".hq-refresh");
  const handle = host.querySelector(".hq-drag"), toggle = host.querySelector(".hq-toggle");

  function persist() {
    PR.savePrefs("library", { quota_card: { engine, groups: { ...groups }, collapsed, position } });
  }
  function place() {
    if (!position) {
      ["left", "top", "right", "bottom"].forEach((key) => { host.style[key] = ""; });
      return;
    }
    const point = PR.quotaCardPosition(position, { width: window.innerWidth, height: window.innerHeight }, host.getBoundingClientRect());
    Object.assign(host.style, { left: point.left + "px", top: point.top + "px", right: "auto", bottom: "auto" });
  }
  function move(left, top) {
    const size = host.getBoundingClientRect();
    position = {
      x: clamp((left - 8) / Math.max(1, window.innerWidth - size.width - 16)),
      y: clamp((top - 8) / Math.max(1, window.innerHeight - size.height - 16)),
    };
    place();
  }

  function groupName(name) {
    return { "Gemini Models": "Gemini", "Claude and GPT models": "Claude / GPT", "codex": "Codex" }[name] || name;
  }
  function render() {
    if (!ready) return;
    host.hidden = false;
    host.classList.toggle("collapsed", collapsed);
    host.querySelector("#homeQuotaBody").hidden = collapsed;
    handle.textContent = collapsed ? (engine === "agy" ? "AGY" : "Codex") + " · " + PR.t("額度") : PR.t("AI 額度");
    toggle.textContent = collapsed ? "+" : "−";
    toggle.setAttribute("aria-expanded", String(!collapsed));
    toggle.setAttribute("aria-label", collapsed ? PR.t("展開額度卡") : PR.t("收起額度卡"));
    toggle.title = collapsed ? PR.t("展開額度卡") : PR.t("收起額度卡");
    const state = PR.homeQuotaSnapshot(engine, groups[engine] || PR.ls.get("paperread-home-quota-group-" + engine, null));
    if (state.group) groups[engine] = state.group;
    host.querySelectorAll("[data-hq-engine]").forEach((button) => button.setAttribute("aria-pressed", String(button.dataset.hqEngine === engine)));
    refreshButton.disabled = state.loading;
    const options = state.groups.map((g) => '<option value="' + PR.esc(g) + '">' + PR.esc(groupName(g)) + '</option>').join("");
    if (selector.innerHTML !== options) selector.innerHTML = options;
    selector.hidden = state.groups.length < 2;
    selector.value = state.group || "";
    host.querySelector(".hq-values").innerHTML = [["five", "5hr"], ["week", "Week"]].map(([key, label]) => {
      const w = state.windows[key];
      const value = w ? w.value + "%" + (w.stale ? "*" : "") : "—";
      const hint = !w ? PR.t("未提供此時段額度") : w.stale ? PR.t("上次剩餘 {n}%", { n: w.value }) : PR.t("剩餘 {n}%", { n: w.value });
      return '<div class="hq-window' + (w && w.stale ? ' stale' : w && w.value <= 20 ? ' low' : '') + '" title="' + PR.esc(hint) + '"><span>' + label + '</span><b aria-label="' + PR.esc(label + ': ' + hint) + '">' + value + '</b></div>';
    }).join("");
    host.querySelector(".hq-note").textContent = state.loading ? PR.t("更新中…") : state.stale ? PR.t("* 上次資料") : Object.keys(state.windows).length ? PR.t("剩餘額度") : PR.t("額度暫不可用");
    place();
  }

  function refresh(force = false) {
    clearTimeout(timer);
    if (!ready || document.hidden || collapsed) return;
    const data = PR.quotaState[engine] || {};
    const last = Math.max(attempted[engine] || 0, finite(data.checked_at) ? data.checked_at * 1000 : 0);
    const wait = interval - (Date.now() - last);
    if (!force && wait > 0) { timer = setTimeout(refresh, wait); return; }
    const requested = engine;
    attempted[requested] = Date.now();
    PR.loadQuota(requested, force).finally(() => { if (requested === engine) refresh(); });
  }
  host.addEventListener("click", (event) => {
    const button = event.target.closest("[data-hq-engine]");
    if (button) {
      engine = button.dataset.hqEngine;
      PR.ls.set("paperread-home-quota-engine", engine);
      render();
      persist();
      refresh();
    } else if (event.target.closest(".hq-refresh")) refresh(true);
    else if (event.target.closest(".hq-toggle")) {
      const anchor = position && host.getBoundingClientRect();
      collapsed = !collapsed;
      render();
      if (anchor) move(anchor.left, anchor.top);
      persist();
      refresh();
    }
  });
  selector.addEventListener("change", () => {
    groups[engine] = selector.value;
    PR.ls.set("paperread-home-quota-group-" + engine, selector.value);
    render();
    persist();
  });
  host.addEventListener("pointerdown", (event) => {
    if (event.button !== 0 || event.isPrimary === false) return;
    const control = event.target.closest("button, select, input, a, textarea, [contenteditable]");
    if (control && control !== handle) return;
    event.preventDefault();
    if (control === handle) handle.focus({ preventScroll: true });
    const rect = host.getBoundingClientRect();
    drag = { id: event.pointerId, x: event.clientX, y: event.clientY, left: rect.left, top: rect.top, moved: false };
    host.setPointerCapture(event.pointerId);
  });
  host.addEventListener("pointermove", (event) => {
    if (!drag || event.pointerId !== drag.id) return;
    const dx = event.clientX - drag.x, dy = event.clientY - drag.y;
    if (!drag.moved && Math.hypot(dx, dy) < 4) return;
    drag.moved = true;
    host.classList.add("dragging");
    move(drag.left + dx, drag.top + dy);
  });
  function endDrag(event) {
    if (!drag || event.pointerId !== drag.id) return;
    const moved = drag.moved;
    drag = null;
    if (moved) persist();
    if (host.hasPointerCapture(event.pointerId)) host.releasePointerCapture(event.pointerId);
    host.classList.remove("dragging");
  }
  ["pointerup", "pointercancel", "lostpointercapture"].forEach((name) => host.addEventListener(name, endDrag));
  handle.addEventListener("keydown", (event) => {
    const delta = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] }[event.key];
    if (event.key === "Home") { position = null; place(); }
    else if (delta) {
      const rect = host.getBoundingClientRect(), step = event.shiftKey ? 40 : 10;
      move(rect.left + delta[0] * step, rect.top + delta[1] * step);
    } else return;
    event.preventDefault();
    event.stopPropagation();
    persist();
  });
  window.addEventListener("resize", place);
  PR.on("quota-updated", (changed) => { if (changed === engine) render(); });
  function start() {
    if (ready || !libraryReady || !prefsReady) return;
    ready = true;
    engine = engine || (supported(PR.lib.engine) ? PR.lib.engine : "codex");
    render();
    refresh();
  }
  PR.on("library-prefs", (prefs) => {
    if (prefsReady) return;
    const value = prefs.library && prefs.library.quota_card;
    if (value && typeof value === "object") {
      if (supported(value.engine)) engine = value.engine;
      collapsed = value.collapsed === true;
      if (value.position && finite(value.position.x) && finite(value.position.y)) position = { x: clamp(value.position.x), y: clamp(value.position.y) };
      for (const provider of ["agy", "codex"]) {
        if (value.groups && typeof value.groups[provider] === "string") groups[provider] = value.groups[provider].slice(0, 160);
      }
    }
    prefsReady = true;
    start();
  });
  PR.on("library-loaded", () => { libraryReady = true; start(); });
  document.addEventListener("visibilitychange", () => { render(); refresh(); });
})(window.PR);
