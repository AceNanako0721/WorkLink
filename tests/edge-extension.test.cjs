/* Synthetic Teams DOM shapes, including edit composers outside message bodies. */
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');
const dom = require('../src/edge-extension/dom.js');

function fixture({title='Synthetic Owner (you)', participants=1, draft='', kind='send', changed=false, input='normal', replacedSend=false, toolbar=false, visibility='visible'} = {}) {
  const state = {rows: [], menu: null, editing: null, saved: null, calls: [], finished: false};
  const heading = {innerText: title, querySelectorAll: () => Array(participants).fill({})};
  function editor(text) { return {innerText: text, focus() {state.focus = this; if (input === "user-edit") setTimeout(() => {this.innerText = "New personal draft";}, 10);},
    replaceChildren() {throw new Error('Controlled-editor DOM must not be replaced');},
    dispatchEvent(event) {assert.equal(event.type, 'paste'); state.pasteEvents = (state.pasteEvents || 0) + 1;
      const text = input === 'paragraphs' ? event.clipboardData.getData('text/plain').replace(/\n\n/g, '\n\n \u00a0\n\n').replace(/ {2}/g, '\u00a0 ') : event.clipboardData.getData('text/plain');
      if (input === 'delayed') setTimeout(() => {this.innerText = text;}, 40);
      else this.innerText = this.innerText && !state.selectionSettled ? text + this.innerText : text;}}; }
  const compose = editor(draft);
  const job = {id: 'synthetic_job', lease: 'synthetic_lease', kind, teams_id: kind === 'send' ? null : '1900000000001', text: 'omp：Fixture reply\nOption: /choose k000000000001 1', expected_text: 'omp：Old reply'};
  if (input === 'paragraphs') job.text = 'omp：Fixture reply\n\nSecond  paragraph';
  function message(id, text, {sent=true, edited=false, deleted=false} = {}) {
    const row = {
      id, innerText: deleted ? '此消息已删除。\n撤消' : text, text: {innerText: text}, sent, edited, deleted,
      getAttribute: key => key === 'data-mid' ? id : null,
      closest: () => ({querySelector: () => ({dateTime: '2030-01-01T00:00:00Z'})}),
      querySelector: key => key === dom.selectors.text ? row.text : key === dom.selectors.menu ? {click: () => openMenu(row)} : null,
      focus() {}, dispatchEvent() {openMenu(row);}
    };
    return row;
  }
  function openMenu(row) {
    const edit = {innerText: '编辑\nE', getAttribute: key => key === 'aria-label' ? '编辑' : null, click() {
      state.menu = null;
      state.editing = editor(row.text.innerText);
    }};
    const remove = {innerText: '删除\nDelete', getAttribute: key => key === 'aria-label' ? '删除此消息' : null, click() {
      state.menu = null; row.deleted = true; row.innerText = '此消息已删除。\n撤消'; row.text.innerText = '';
    }};
    const items = {querySelectorAll: () => [edit, remove]};
    if (toolbar) state.toolbar = {querySelector: selector => selector === '[data-tid="message-actions-edit"]' ? edit : selector === '[data-tid="message-actions-more"]' ? {click() {state.menu = items; state.moreClicks = (state.moreClicks || 0) + 1;}} : null};
    else state.menu = items;
  }
  if (kind !== 'send') state.rows.push(message(job.teams_id, changed ? 'omp：Manually changed' : job.expected_text));
  const send = {getAttribute: key => key === 'aria-label' ? '发送 (Ctrl+Enter)' : null, get disabled() {return !compose.innerText;}, click() {
    state.rows.push(message('1900000000002', compose.innerText)); compose.innerText = '';
  }};
  const save = {click() {
    const row = state.rows.find(row => row.id === job.teams_id);
    row.text.innerText = state.editing.innerText; row.innerText = state.editing.innerText; row.edited = true;
    state.editing = null;
  }};
  const document = {
    visibilityState: visibility,
    querySelector(selector) {
      if (selector === dom.selectors.title) return heading;
      if (selector === dom.selectors.composer) return compose;
      if (selector === dom.selectors.send) return replacedSend ? {disabled: false, click() {state.staleClicks = (state.staleClicks || 0) + 1;}} : send;
      if (selector === '[role="menu"]') return state.menu;
      return null;
    },
    querySelectorAll(selector) {
      if (selector === dom.selectors.send + ', [data-tid="newMessageCommands-send"]') return [send];
      if (selector === dom.selectors.messages) return state.rows;
      if (selector === dom.selectors.composer) return state.editing ? [state.editing, compose] : [compose];
      if (selector === '[role="menu"]') return state.menu ? [state.menu] : [];
      if (selector === '[data-tid="newMessageCommands-send"]') return state.editing ? [save] : [];
      return [];
    },
    getElementById(id) {
      if (id === job.teams_id + '-popover-surface') return state.toolbar || null;
      const row = state.rows.find(row => id.endsWith(row.id));
      if (!row) return null;
      if (id.startsWith('read-status-icon-') && row.sent) return {getAttribute: () => '已发送'};
      if (id.startsWith('edited-') && row.edited) return {};
      return null;
    },
    createRange: () => ({selectNodeContents() {}}),
    createElement(tag) {return {children: [], append(node) {this.children.push(node);}, get innerText() {return tag === 'br' ? '\n' : this.children.map(node => node.innerText).join('');}};},
    createTextNode(text) {return {innerText: text};},
    execCommand() {throw new Error('DOM-only insertion must not be used');}
  };
  const chrome = {runtime: {async sendMessage(message) {
    if (message.type === 'pending-set') {state.saved = message.operation; return {ok: true};}
    if (message.type === 'pending-get') return {ok: true, result: state.saved};
    state.calls.push(message);
    if (message.route === 'settings') return {ok: true, result: {selfChatDisplayName: 'Synthetic Owner (you)'}};
    if (message.route === 'observe') return {ok: true, result: {accepted: 0}};
    if (message.route === 'next') return {ok: true, result: job};
    if (message.route === 'complete') {state.finished = true; return {ok: true, result: true};}
    throw new Error('Unexpected route');
  }}};
  return {state, document, chrome, compose, job, message};
}

async function run(options) {
  const f = fixture(options);
  const context = {WorkLinkDOM: dom, document: f.document, chrome: f.chrome,
    window: {getSelection: () => ({removeAllRanges() {f.state.selectionSettled = false;}, addRange() {setTimeout(() => {f.state.selectionSettled = true;}, 20);}})},
    setInterval() {}, setTimeout, Date, KeyboardEvent: class {},
    DataTransfer: class {constructor() {this.data = new Map();} setData(key, value) {this.data.set(key, value);} getData(key) {return this.data.get(key);}},
    ClipboardEvent: class {constructor(type, options) {this.type = type; this.clipboardData = options.clipboardData;}}};
  vm.runInNewContext(readFileSync(path.join(__dirname, '../src/edge-extension/content.js'), 'utf8'), context);
  for (let i = 0; i < 250 && !f.state.finished; i++) {
    if (dom.title(f.document) !== 'Synthetic Owner (you)' && f.state.calls.length) break;
    await new Promise(resolve => setTimeout(resolve, 10));
  }
  return f;
}

test('Send fills the editor before testing the disabled send button and confirms a stable sent ID', async () => {
  const f = await run();
  const complete = f.state.calls.find(call => call.route === 'complete');
  assert.equal(complete.body.state, 'succeeded');
  assert.equal(complete.body.teamsId, '1900000000002');
  assert.equal(f.state.rows[0].text.innerText, f.job.text);
  assert.equal(f.state.saved, null);
});
test('Inline edit outside the message body uses its own save control and retains ID', async () => {
  const f = await run({kind: 'edit'});
  assert.equal(f.state.calls.find(call => call.route === 'complete').body.state, 'succeeded');
  assert.equal(f.state.rows[0].text.innerText, f.job.text);
  assert.equal(dom.messages(f.document)[0].edited, true);
});

test('Paste replaces the compact send button; only the current accessible send control is used', async () => {
  const f = await run({input: 'cancel', replacedSend: true});
  assert.equal(f.state.calls.find(call => call.route === 'complete').body.state, 'succeeded');
  assert.equal(f.state.staleClicks || 0, 0);
  assert.equal(f.state.rows.length, 1);
});
test('Delete requires an explicit deleted tombstone with the original ID', async () => {
  const f = await run({kind: 'delete'});
  const complete = f.state.calls.find(call => call.route === 'complete');
  assert.equal(complete.body.state, 'succeeded');
  assert.equal(complete.body.teamsId, f.job.teams_id);
  assert.equal(dom.messages(f.document)[0].deleted, true);
});

test('Message-bound toolbar edits directly and opens overflow for deletion', async () => {
  for (const kind of ['edit', 'delete']) {
    const f = await run({kind, toolbar: true});
    assert.equal(f.state.calls.find(call => call.route === 'complete').body.state, 'succeeded');
    assert.equal(f.state.moreClicks || 0, kind === 'delete' ? 1 : 0);
    assert.equal(f.state.calls.find(call => call.route === 'complete').body.teamsId, f.job.teams_id);
  }
});

test('Reply confirmation tolerates Teams expanding blank paragraphs', async () => {
  for (const kind of ['send', 'edit']) {
    const f = await run({kind, input: 'paragraphs'});
    assert.equal(f.state.calls.find(call => call.route === 'complete').body.state, 'succeeded');
    assert.equal(f.state.rows[0].text.innerText, f.job.text.replace(/\n\n/g, '\n\n \u00a0\n\n').replace(/ {2}/g, '\u00a0 '));
  }
});
test('Existing draft survives and no send occurs', async () => {
  const f = await run({draft: 'Personal unfinished draft'});
  assert.equal(f.compose.innerText, 'Personal unfinished draft');
  assert.equal(f.state.rows.length, 0);
  assert.equal(f.state.calls.find(call => call.route === 'complete').body.state, 'failed');
});

test('A hidden Teams tab continues observing and confirming replies with the same identity guards', async () => {
  const f = await run({visibility: 'hidden'});
  assert.equal(f.state.calls.some(call => call.route === 'observe'), true);
  assert.equal(f.state.calls.find(call => call.route === 'complete').body.state, 'succeeded');
});
test('Model paste pipeline handles multiline content without DOM-only insertion', async () => {
  const f = await run({input: 'cancel'});
  assert.equal(f.state.calls.find(call => call.route === 'complete').body.state, 'succeeded');
  assert.equal(f.state.pasteEvents, 1);
  assert.equal(f.state.rows[0].text.innerText, f.job.text);
});
test('Delayed model paste settles without a second replacement', async () => {
  const f = await run({input: 'delayed'});
  assert.equal(f.state.calls.find(call => call.route === 'complete').body.state, 'succeeded');
  assert.equal(f.state.pasteEvents, 1);
});
test('Concurrent personal input is preserved and cannot trigger fallback or send', async () => {
  const f = await run({input: 'user-edit'});
  assert.equal(f.state.rows.length, 0);
  assert.equal(f.compose.innerText, 'New personal draft');
  assert.equal(f.state.pasteEvents || 0, 0);
  assert.equal(f.state.calls.find(call => call.route === 'complete').body.failureCode, 'editor_text_mismatch');
});
test('Reply externally edited by the user cannot be changed by this operation', async () => {
  const f = await run({kind: 'edit', changed: true});
  assert.equal(f.state.rows[0].text.innerText, 'omp：Manually changed');
  assert.equal(f.state.calls.find(call => call.route === 'complete').body.state, 'failed');
});
test('Another chat or a group with matching text never observes or leases operations', async () => {
  for (const options of [{title: 'Other person'}, {participants: 2}]) {
    const f = await run(options);
    assert.equal(f.state.calls.some(call => call.route === 'observe' || call.route === 'next'), false);
  }
});
test('Ordinary personal text is removed from observations', async () => {
  const f = fixture();
  f.state.rows.push(f.message('1900000000001', 'Personal private note'));
  let interval;
  const context = {WorkLinkDOM: dom, document: f.document, chrome: f.chrome,
    window: {getSelection: () => ({removeAllRanges() {}, addRange() {}})},
    setInterval(fn) {interval = fn;}, setTimeout, Date};
  vm.runInNewContext(readFileSync(path.join(__dirname, '../src/edge-extension/content.js'), 'utf8'), context);
  for (let i = 0; i < 100 && !f.state.finished; i++) await new Promise(resolve => setTimeout(resolve, 10));
  const observation = f.state.calls.find(call => call.route === 'observe');
  assert.equal(observation.body.messages[0].text, '');
});
