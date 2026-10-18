/* Account quotas are read-only snapshots from each user's logged-in CLI. */
(function (PR) {
  "use strict";
  const records = {}, pending = {};
  const supported = (engine) => engine === "agy" || engine === "codex";
  const finite = (n) => typeof n === "number" && Number.isFinite(n);
  PR.quotaState = records;

  PR.loadQuota = function (engine, refresh = false) {
    if (!supported(engine)) return Promise.resolve(null);
    if (pending[engine]) return pending[engine];
    const previous = records[engine];
    records[engine] = { ...previous, loading: true };
    pending[engine] = PR.api("/api/usage/limits", { method: "POST", body: { engine, refresh } })
      .then((data) => { records[engine] = data; })
      .catch(() => {
        records[engine] = { ...previous, status: previous && previous.rows && previous.rows.length ? "stale" : "unavailable", reason: "network" };
      }).finally(() => {
        delete pending[engine];
        PR.emit("quota-updated", engine);
      });
    PR.emit("quota-updated", engine);
    return pending[engine];
  };

  function reasonText(reason) {
    if (reason === "cli_missing") return PR.t("尚未找到 CLI，請先安裝並登入。");
    if (reason === "timeout") return PR.t("查詢逾時，請稍後更新。");
    if (reason === "network") return PR.t("無法連線至本機服務，請確認 PaperRead 仍在執行。");
    if (reason === "query_failed") return PR.t("查詢失敗，請確認 CLI 已登入且網路正常。");
    return PR.t("此 CLI 或帳號目前未提供額度資料。");
  }

  function label(row) {
    if (row.window_minutes === 10080) return PR.t("每週額度");
    if (row.window_minutes === 300) return PR.t("5 小時額度");
    if (finite(row.window_minutes) && row.window_minutes > 0) {
      return row.window_minutes % 60 === 0 ? PR.t("{n} 小時額度", { n: row.window_minutes / 60 }) : PR.t("{n} 分鐘額度", { n: row.window_minutes });
    }
    if (row.label === "Weekly Limit Remaining") return PR.t("每週額度");
    if (row.label === "Five Hour Limit Remaining") return PR.t("5 小時額度");
    if (row.label === "Gemini 4 Argon Weekly Limit") return PR.t("每週額度");
    if (row.label === "primary") return PR.t("主要額度");
    if (row.label === "secondary") return PR.t("次要額度");
    return row.label || PR.t("額度");
  }

  function timeText(stamp) {
    if (!finite(stamp)) return "";
    const date = new Date(stamp * 1000);
    return Number.isNaN(date.getTime()) ? "" : date.toLocaleString(PR.lang === "en" ? "en-US" : "zh-TW", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" });
  }

  PR.quotaHtml = function (engine) {
    if (!supported(engine)) return '<p class="us-foot">' + PR.t("此來源未提供帳號額度查詢。") + "</p>";
    const data = records[engine] || {}, name = engine === "agy" ? "Antigravity CLI" : "Codex";
    const rows = (data.rows || []).filter((r) => finite(r.remaining_percent));
    let body = "";
    if (data.reason) body += '<p class="quota-note" role="status">' + reasonText(data.reason) + (rows.length ? " " + PR.t("以下為上次取得的資料。") : "") + "</p>";
    if (data.loading) body += '<p class="quota-note" role="status">' + PR.t("正在讀取帳號額度…") + "</p>";
    if (!rows.length && !data.reason && !data.loading) body += '<p class="quota-note">' + PR.t("按更新讀取帳號額度。") + "</p>";
    body += rows.map((row) => {
      const remaining = Math.max(0, Math.min(100, row.remaining_percent));
      const expired = finite(row.resets_at) && row.resets_at * 1000 <= Date.now();
      const stale = expired || data.status === "stale";
      const group = row.group === "codex" ? "Codex" : row.group;
      const title = [group, label(row)].filter(Boolean).join(" · ");
      const reset = expired ? PR.t("已到重置時間，請更新。") : timeText(row.resets_at) ? PR.t("{time} 重置", { time: timeText(row.resets_at) }) : PR.t("未提供重置時間");
      const percent = { n: Math.round(remaining * 10) / 10 };
      const value = stale ? PR.t("上次剩餘 {n}%", percent) : PR.t("剩餘 {n}%", percent);
      return '<div class="quota-row' + (stale ? " stale" : remaining <= 5 ? " critical" : remaining <= 20 ? " low" : "") + '">' +
        '<div class="quota-line"><span>' + PR.esc(title) + "</span><b>" + PR.esc(value) + "</b></div>" +
        '<div class="us-bar" role="meter" aria-label="' + PR.esc(title + ": " + value) + '" aria-valuemin="0" aria-valuemax="100" aria-valuenow="' + remaining + '"><i style="width:' + remaining + '%"></i></div>' +
        '<div class="quota-reset">' + PR.esc(reset) + "</div></div>";
    }).join("");
    return '<section class="quota-provider"><div class="quota-heading"><h3>' + name + '</h3><button class="btn sm" data-quota-refresh="' + engine + '" data-c="quota-refresh"' + (data.loading ? " disabled" : "") + '>' + PR.t("更新額度") + "</button></div>" + body +
      (data.updated_at ? '<p class="us-foot">' + PR.esc(PR.t("帳號共用 · 更新於 {time}", { time: timeText(data.updated_at) })) + "</p>" : "") + "</section>";
  };

  PR.settingsTabs = PR.settingsTabs || {};
  PR.settingsTabs.quota = {
    render() {
      return '<p class="hint">' + PR.t("顯示整個帳號的剩餘額度，包含在其他程式的使用。更新不會請求 AI 回答。") + '</p><div class="quota-settings">' + PR.quotaHtml("agy") + PR.quotaHtml("codex") + "</div>";
    },
    load() { PR.loadQuota("agy"); PR.loadQuota("codex"); },
    click(event) {
      const button = event.target.closest("[data-quota-refresh]");
      if (!button) return false;
      PR.loadQuota(button.dataset.quotaRefresh, true);
      return true;
    },
  };
  PR.on("quota-updated", () => {
    if (PR.settingsState && PR.settingsState.tab === "quota" && PR.$("#settingsDlg").classList.contains("open")) PR.settingsRender();
  });
})(window.PR);
