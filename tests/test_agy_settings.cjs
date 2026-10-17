const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const { test } = require('node:test');

function setup() {
  const PR = { t: x => x, esc: String, icon: () => '', settingsTabs: {},
    $: () => ({addEventListener() {}}) };
  const ctx = {window: {PR, addEventListener() {}}, document: {addEventListener() {}}};
  for (const name of ['settings', 'settings-models', 'settings-chat']) {
    vm.runInNewContext(fs.readFileSync('easyread/web/js/common/' + name + '.js', 'utf8'), ctx);
  }
  return PR;
}

test('AGY has native CLI form with real model IDs, no key/address/fast fields', () => {
  const PR = setup();
  const card = {id:'agy', engine:'agy', name:'AGY', model:'flash-medium'};
  const s = {cfg: {engine:'agy', agy:{model:'flash-medium'}, openai:{}, claude:{}, codex:{}},
    chat:{models:[card],default:'agy'}, pinned:true, transChecked:true, editing:'agy',
    form:{kind:'agy',model:'flash-medium'}, found:{agy:{found:true}},
    models:{agy:{models:[{id:'flash-medium',name:'Flash Medium'}]}}};
  const html = PR.settingsTabs.chat.render(s);
  assert.match(html, /Antigravity CLI/);
  assert.match(html, /value="flash-medium" selected>Flash Medium/);
  assert.match(html, /跟隨 AGY CLI 預設/);
  assert.doesNotMatch(html, /data-af=|data-reasoning=|Fast:|openai.vision/);
  assert.equal(PR.settingsTabs.engine.collect(s).agy.model, 'flash-medium');
  assert.equal(PR.cliPin(s, 'agy', ''), '');
});

test('AGY settings show missing CLI and stale catalog errors', () => {
  const PR = setup();
  const s = {cfg:{engine:'agy',agy:{},openai:{}},chat:{models:[]},pinned:true,transChecked:true,
    editing:'new',form:{kind:'agy'},models:{agy:{models:[],error:'請先登入 AGY'}}};
  assert.match(PR.settingsTabs.chat.render(s), /請先登入 AGY/);
});
