const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const { test } = require('node:test');

function setup(api = async () => ({})) {
  const PR = { t: (s, args={}) => s.replace(/\{(\w+)\}/g, (_,key) => String(args[key])),
    esc: s => String(s).replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('"', '&quot;'),
    on() {}, emit() {}, api, lang:'zh-TW' };
  for (const file of ['usage','quota']) vm.runInNewContext(fs.readFileSync('easyread/web/js/common/'+file+'.js','utf8'), {window:{PR}, Date});
  return PR;
}

test('quotas show remaining capacity, actual windows, and escaped source names', () => {
  const PR = setup();
  PR.quotaState.codex = {status:'ready',rows:[{group:'<script>',label:'primary',window_minutes:10080,remaining_percent:78,resets_at:Date.now()/1000+3600}]};
  const html = PR.quotaHtml('codex');
  assert.match(html,/剩餘 78%/);
  assert.match(html,/每週額度/);
  assert.match(html,/aria-valuenow="78"/);
  assert.match(html,/&lt;script>/);
  assert.doesNotMatch(html,/<script>|5 小時/);
});

test('unknown quota is not rendered as zero and expired snapshots are labeled', () => {
  const PR = setup();
  PR.quotaState.agy = {status:'unavailable',reason:'timeout',rows:[{remaining_percent:null}]};
  assert.doesNotMatch(PR.quotaHtml('agy'),/剩餘 0%|role="meter"/);
  PR.quotaState.agy = {status:'ready',rows:[{remaining_percent:40,resets_at:1}]};
  assert.match(PR.quotaHtml('agy'),/上次剩餘 40%/);
  assert.match(PR.quotaHtml('agy'),/已到重置時間/);
});

test('missing context limit never draws a guessed capacity bar', () => {
  const PR = setup();
  const html = PR.usagePop(null,[{usage:{calls:1,context:{cached:100,fresh:500,output:20}}}],'agy');
  assert.match(html,/620 token/);
  assert.match(html,/無法計算剩餘容量/);
  assert.doesNotMatch(html,/us-stack|200000|200K/);
});

test('switching provider does not label Claude subscription limits as AGY', () => {
  const PR = setup();
  const html = PR.usagePop({limits:{five_hour:{used:.8}},at:1},[],'agy');
  assert.doesNotMatch(html,/Claude 订阅/);
  assert.match(html,/Antigravity CLI/);
});

test('concurrent refreshes are coalesced and failed refresh retains stale data', async () => {
  let finish, calls=0;
  const PR = setup(() => { calls++; return new Promise(resolve => {finish=resolve;}); });
  const first = PR.loadQuota('agy');
  assert.equal(PR.loadQuota('agy',true), first);
  assert.equal(calls,1);
  finish({engine:'agy',status:'ready',rows:[{remaining_percent:91}],updated_at:123});
  await first;
  PR.api = async () => { throw new Error('offline'); };
  await PR.loadQuota('agy',true);
  assert.equal(PR.quotaState.agy.status,'stale');
  assert.equal(PR.quotaState.agy.rows[0].remaining_percent,91);
  assert.equal(PR.quotaState.agy.updated_at,123);
  assert.match(PR.quotaHtml('agy'),/上次剩餘 91%/);
});
