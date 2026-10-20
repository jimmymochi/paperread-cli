/* A separate, read-only preview. It never enters paper blocks or exports. */
(function (PR) {
  "use strict";
  const small = new Map();
  PR.renderTranslationPreview = function () {
    const node = document.getElementById("translationPreview");
    if (!node) return;
    const batches = [...(PR.state.preview.batches || []), ...small.values()].filter(b => (b.rows || []).length);
    node.hidden = !batches.length;
    node.innerHTML = batches.length ? '<div class="preview-label" role="status">' +
      PR.t("譯文預覽 · 完成檢查後會自動存入正文") + '</div><div class="preview-content">' +
      batches.sort((a, b) => a.pages[0] - b.pages[0]).map(b => '<div class="preview-batch"><div class="hint">' +
        PR.esc(PR.t("第 {n} 頁", { n: b.pages.join("–") })) + '</div>' +
        b.rows.map(row => '<p>' + PR.esc(row.text) + '</p>').join("") + '</div>').join("") + '</div>' : "";
  };
  PR.on("rendered", PR.renderTranslationPreview);
  PR.on("remote", changed => { if (changed.includes("preview")) PR.renderTranslationPreview(); });
  PR.on("job-progress", job => {
    if (job.kind !== "retranslate") return;
    if (job.preview && (job.preview.rows || []).length) small.set(job.id, job.preview);
    else small.delete(job.id);
    PR.renderTranslationPreview();
  });
  PR.on("job-finished", job => { small.delete(job.id); PR.renderTranslationPreview(); });
})(window.PR);
