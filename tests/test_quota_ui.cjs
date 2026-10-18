const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const { test } = require('node:test');

function setup(api = async () => ({})) {
  const PR = { t: (s, args={}) => s.replace(/\{(\w+)\}/g, (_,key) => String(args[key])),
    esc: s => String(s).replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('"', '&quot;'),
    on() {}, emit() {}, api, lang:'zh-TW', $: () => null };
  for (const file of ['usage','quota']) vm.runInNewContext(fs.readFileSync('easyread/web/js/common/'+file+'.js','utf8'), {window:{PR}, Date});
  vm.runInNewContext(fs.readFileSync('easyread/web/js/library/quota-summary.js','utf8'), {window:{PR}, Date});
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

test('home summary keeps provider and AGY model groups separate', () => {
  const PR = setup();
  PR.quotaState.agy = {rows: [
    {group:'Gemini Models',label:'Five Hour Limit Remaining',remaining_percent:80},
    {group:'Gemini Models',label:'Weekly Limit Remaining',remaining_percent:60},
    {group:'Claude and GPT models',label:'Weekly Limit Remaining',remaining_percent:20},
  ]};
  PR.quotaState.codex = {rows:[{group:'codex',window_minutes:10080,remaining_percent:74}]};
  const gemini = PR.homeQuotaSnapshot('agy','Gemini Models');
  assert.equal(gemini.windows.five.value,80);
  assert.equal(gemini.windows.week.value,60);
  const claude = PR.homeQuotaSnapshot('agy','Claude and GPT models');
  assert.equal(claude.windows.week.value,20);
  assert.equal(claude.windows.five,undefined);
  assert.equal(PR.homeQuotaSnapshot('codex').windows.week.value,74);
  assert.equal(PR.homeQuotaSnapshot('codex').windows.five,undefined);
});

test('home summary uses actual durations and never invents a missing 5hr window', () => {
  const PR = setup();
  PR.quotaState.codex = {rows:[
    {group:'codex',label:'primary',window_minutes:10080,remaining_percent:70},
    {group:'codex',label:'secondary',window_minutes:60,remaining_percent:95},
    {group:'codex',label:'Five Hour Limit Remaining',remaining_percent:99},
  ]};
  const state = PR.homeQuotaSnapshot('codex');
  assert.equal(state.windows.week.value,70);
  assert.equal(state.windows.five,undefined);
});

test('home summary preserves zero while excluding null and non-finite values', () => {
  const PR = setup();
  assert.equal(Object.keys(PR.homeQuotaSnapshot('agy').windows).length,0);
  PR.quotaState.codex = {rows:[
    {window_minutes:300,remaining_percent:null},
    {window_minutes:300,remaining_percent:NaN},
    {window_minutes:10080,remaining_percent:0},
  ]};
  const state = PR.homeQuotaSnapshot('codex');
  assert.equal(state.windows.five,undefined);
  assert.equal(state.windows.week.value,0);
});

test('home summary labels expired and failed snapshots, and recovers a removed group', () => {
  const PR = setup();
  PR.quotaState.agy = {rows:[{group:'Gemini 4 Argon',label:'Gemini 4 Argon Weekly Limit',remaining_percent:30,resets_at:1}]};
  let state = PR.homeQuotaSnapshot('agy','Removed group');
  assert.equal(state.group,'Gemini 4 Argon');
  assert.equal(state.windows.week.stale,true);
  assert.equal(state.stale,true);
  PR.quotaState.agy.rows[0].resets_at = Date.now()/1000+3600;
  PR.quotaState.agy.status = 'stale';
  state = PR.homeQuotaSnapshot('agy');
  assert.equal(state.windows.week.value,30);
  assert.equal(state.stale,true);
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

function homeHarness() {
  let now = 1000000, nextId = 0;
  const events = {}, visibility = {}, timers = new Map(), stored = new Map(), calls = [], savedPrefs = [];
  const node = () => ({innerHTML:'',value:'',hidden:false,setAttribute(){},addEventListener(name,fn){this[name]=fn;},focus(){},setPointerCapture(){},hasPointerCapture(){return false;}});
  const nodes = Object.fromEntries(['.hq-group','.hq-refresh','.hq-values','.hq-note','.hq-drag','.hq-toggle','#homeQuotaBody'].map(key => [key,node()]));
  const host = {...node(),style:{},classList:{toggle(){},add(){},remove(){}},querySelector:key => nodes[key],querySelectorAll:() => [],
    getBoundingClientRect(){return {width:208,height:180,left:parseFloat(this.style.left)||700,top:parseFloat(this.style.top)||500};}};
  const doc = {hidden:false,addEventListener(name,fn){visibility[name]=fn;}};
  const PR = setup();
  Object.assign(PR, {
    $:() => host, lib:{engine:'agy'},
    ls:{get:(key,fallback) => stored.get(key) ?? fallback,set:(key,value) => stored.set(key,value)},
    savePrefs:(section,patch) => savedPrefs.push(JSON.parse(JSON.stringify({section,patch}))),
    on:(event,fn) => {events[event]=fn;},
    loadQuota:async (engine,force) => { calls.push([engine,force]); PR.quotaState[engine] = {checked_at:now/1000}; },
  });
  const win = {PR,innerWidth:1000,innerHeight:800,addEventListener(name,fn){this[name]=fn;}};
  vm.runInNewContext(fs.readFileSync('easyread/web/js/library/quota-summary.js','utf8'), {
    window:win,document:doc,Date:{now:() => now},
    setTimeout:(fn,delay) => {const id=++nextId;timers.set(id,{fn,delay});return id;},clearTimeout:id => timers.delete(id),
  });
  const click = selector => host.click({target:{closest:s => s===selector ? {} : null}});
  const choose = engine => host.click({target:{closest:selector => selector === '[data-hq-engine]' ? {dataset:{hqEngine:engine}} : null}});
  return {events,visibility,timers,stored,calls,nodes,host,doc,PR,win,savedPrefs,click,choose,advance:ms => {now+=ms;}};
}

test('home refresh waits for preferences and authentication, caches switches, and pauses when hidden', async () => {
  const {events,visibility,timers,stored,calls,doc,choose,click,advance} = homeHarness();
  const settle = () => new Promise(resolve => setImmediate(resolve));
  assert.equal(calls.length,0);
  events['library-loaded'](); await settle();
  assert.equal(calls.length,0);
  events['library-prefs']({}); await settle();
  assert.equal(calls.length,1);
  assert.equal(calls[0][0],'agy');
  events['library-loaded'](); await settle();
  assert.equal(calls.length,1);
  choose('codex'); await settle();
  choose('agy'); await settle();
  assert.equal(calls.length,2);
  assert.equal(stored.get('paperread-home-quota-engine'),'agy');
  doc.hidden=true; visibility.visibilitychange();
  assert.equal(timers.size,0);
  advance(300001); doc.hidden=false; visibility.visibilitychange(); await settle();
  assert.equal(calls.length,3);
  click('.hq-refresh'); await settle();
  assert.equal(calls.at(-1)[1],true);
});

test('collapsed cards restore from server preferences, pause queries, and save expansion', async () => {
  const p = homeHarness();
  p.events['library-prefs']({library:{quota_card:{engine:'codex',collapsed:true,position:{x:0,y:0},groups:{agy:'Gemini Models'}}}});
  p.events['library-loaded']();
  assert.equal(p.nodes['#homeQuotaBody'].hidden,true);
  assert.equal(p.calls.length,0);
  assert.equal(p.host.style.left,'8px');
  p.click('.hq-toggle'); await new Promise(resolve => setImmediate(resolve));
  assert.equal(p.nodes['#homeQuotaBody'].hidden,false);
  assert.equal(p.calls[0][0],'codex');
  assert.equal(p.savedPrefs.at(-1).patch.quota_card.collapsed,false);
  p.click('.hq-toggle');
  assert.equal(p.timers.size,0);
  assert.equal(p.savedPrefs.at(-1).patch.quota_card.collapsed,true);
});

test('dragging from the card body clamps to the viewport, survives resize, and Home resets the position', () => {
  const p = homeHarness();
  p.events['library-prefs']({}); p.events['library-loaded']();
  const handle = p.nodes['.hq-drag'];
  const down = {button:0,isPrimary:true,target:{closest:()=>null},preventDefault(){}};
  p.host.pointerdown({...down,pointerId:7,clientX:750,clientY:520});
  p.host.pointermove({pointerId:7,clientX:-300,clientY:-300});
  p.host.pointerup({pointerId:7});
  assert.equal(p.host.style.left,'8px');
  assert.equal(p.host.style.top,'8px');
  assert.deepEqual(p.savedPrefs.at(-1).patch.quota_card.position,{x:0,y:0});
  p.host.pointerdown({...down,pointerId:8,clientX:20,clientY:20});
  p.host.pointermove({pointerId:8,clientX:3000,clientY:3000});
  p.host.pointercancel({pointerId:8});
  assert.equal(p.host.style.left,'784px');
  assert.equal(p.host.style.top,'612px');
  p.win.innerWidth=400; p.win.innerHeight=300; p.win.resize();
  assert.equal(p.host.style.left,'184px');
  assert.equal(p.host.style.top,'112px');
  handle.keydown({key:'Home',preventDefault(){},stopPropagation(){}});
  assert.equal(p.host.style.left,'');
  assert.equal(p.savedPrefs.at(-1).patch.quota_card.position,null);
});

test('quota controls remain clickable while the title and empty space can be dragged', () => {
  const p = homeHarness();
  p.events['library-prefs']({}); p.events['library-loaded']();
  let prevented=0;
  const down = {button:0,isPrimary:true,pointerId:2,clientX:700,clientY:500,preventDefault(){prevented++;}};
  p.host.pointerdown({...down,target:{closest:()=>p.nodes['.hq-group']}});
  p.host.pointermove({pointerId:2,clientX:200,clientY:200});
  p.host.pointerup({pointerId:2});
  assert.equal(prevented,0);
  assert.equal(p.savedPrefs.length,0);
  p.host.pointerdown({...down,target:{closest:()=>p.nodes['.hq-drag']}});
  p.host.pointermove({pointerId:2,clientX:200,clientY:200});
  p.host.pointerup({pointerId:2});
  assert.equal(prevented,1);
  assert.equal(p.savedPrefs.at(-1).patch.quota_card.position.x>0,true);
});
