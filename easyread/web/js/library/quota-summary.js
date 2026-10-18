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

  const host = PR.$("#homeQuota");
  if (!host) return;
  const saved = PR.ls.get("paperread-home-quota-engine", null);
  let engine = supported(saved) ? saved : null, ready = false, timer;
  const attempted = {}, groups = {}, interval = 300000;
  host.setAttribute("aria-label", PR.t("AI 額度"));
  host.innerHTML = '<div class="hq-head"><div class="hq-engines" role="group" aria-label="' + PR.t("額度來源") + '">' +
    '<button type="button" data-hq-engine="codex">Codex</button><button type="button" data-hq-engine="agy">AGY</button></div>' +
    '<button type="button" class="hq-refresh" title="' + PR.t("更新額度") + '" aria-label="' + PR.t("更新額度") + '">↻</button></div>' +
    '<select class="hq-group" aria-label="' + PR.t("額度群組") + '" hidden></select><div class="hq-values"></div><div class="hq-note" role="status"></div>';
  const selector = host.querySelector(".hq-group"), refreshButton = host.querySelector(".hq-refresh");

  function groupName(name) {
    return { "Gemini Models": "Gemini", "Claude and GPT models": "Claude / GPT", "codex": "Codex" }[name] || name;
  }
  function render() {
    if (!ready) return;
    host.hidden = false;
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
  }

  function refresh(force = false) {
    clearTimeout(timer);
    if (!ready || document.hidden) return;
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
      refresh();
    } else if (event.target.closest(".hq-refresh")) refresh(true);
  });
  selector.addEventListener("change", () => {
    groups[engine] = selector.value;
    PR.ls.set("paperread-home-quota-group-" + engine, selector.value);
    render();
  });
  PR.on("quota-updated", (changed) => { if (changed === engine) render(); });
  PR.on("library-loaded", () => {
    if (ready) return;
    ready = true;
    engine = engine || (supported(PR.lib.engine) ? PR.lib.engine : "codex");
    render();
    refresh();
  });
  document.addEventListener("visibilitychange", () => { render(); refresh(); });
})(window.PR);
