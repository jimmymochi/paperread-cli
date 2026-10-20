const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const path = require("node:path");
const { test } = require("node:test");

test("previews are escaped, separate from saved blocks, and removed when finished", () => {
  const handlers = {}, node = { hidden: true, innerHTML: "" };
  const blocks = [{ id: "p1", zh: "saved text" }];
  const PR = {
    state: { paper: { blocks }, preview: { batches: [{ pages: [1, 2], rows: [{ text: '<img src=x onerror="bad()">' }] }] } },
    t: (text, v) => v ? text.replace("{n}", v.n) : text,
    esc: text => String(text).replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll('"', "&quot;"),
    on: (name, fn) => { handlers[name] = fn; },
  };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, "../easyread/web/js/reader/translation-preview.js"), "utf8"), {
    window: { PR }, document: { getElementById: () => node },
  });
  handlers.rendered();
  assert.equal(node.hidden, false);
  assert.match(node.innerHTML, /&lt;img/);
  assert.doesNotMatch(node.innerHTML, /<img/);
  assert.equal(PR.state.paper.blocks, blocks);
  assert.equal(blocks[0].zh, "saved text");
  PR.state.preview = { batches: [] };
  handlers.remote(["preview"]);
  assert.equal(node.hidden, true);
  handlers["job-progress"]({ id: "j1", kind: "retranslate", preview: { pages: [1], rows: [{ text: "new preview" }] } });
  assert.match(node.innerHTML, /new preview/);
  handlers["job-finished"]({ id: "j1" });
  assert.equal(node.hidden, true);
  assert.equal(node.innerHTML, "");
});
