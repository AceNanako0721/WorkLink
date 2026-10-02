const {test} = require('node:test');
const assert = require('node:assert/strict');
const handlerModule = import('../src/omp-extension/activity.mjs');

test('Explicit summary requests stay limited to the supported reasoning model and opt-in setting', async () => {
  const {requestReasoningSummary} = await handlerModule;
  const payload={model:'synthetic-reasoner',reasoning:{effort:'medium'},input:[]};
  const model={provider:'openai-codex',id:'synthetic-reasoner'};
  assert.equal(requestReasoningSummary(payload,model,true).reasoning.summary,'detailed');
  assert.equal(payload.reasoning.summary,undefined);
  assert.equal(requestReasoningSummary(payload,model,false),undefined);
  assert.equal(requestReasoningSummary(payload,{...model,provider:'other'},true),undefined);
  assert.equal(requestReasoningSummary(payload,{...model,compat:{supportsReasoningSummary:false}},true),undefined);
  assert.equal(requestReasoningSummary({...payload,reasoning:{effort:'none'}},model,true),undefined);
});

test('Only provided reasoning and bounded tool states are displayed after settlement', async () => {
  const {createActivityHandler} = await handlerModule;
  const handle = createActivityHandler({showReasoning: true, showToolProgress: true});
  const views = [];
  const ctx = {send: async view => {views.push(view); return {ok: true};}};
  const event = (type, fields={}) => ({type, source: 'telegram', activityId: 'fixture', ...fields});
  await handle(event('reasoning-end', {text: 'Synthetic provider summary'}), ctx);
  await handle(event('tool-start', {toolCallId: 'tool1', toolName: 'read', args: 'Private fixture argument'}), ctx);
  await handle(event('tool-end', {toolCallId: 'tool1', toolName: 'read', result: 'Private fixture result', isError: false}), ctx);
  assert.equal(views.length, 0);
  await handle(event('agent-settled'), ctx);
  assert.equal(views.length, 1);
  assert.match(views[0].text, /Synthetic provider summary/);
  assert.match(views[0].text, /read：完成/);
  assert.doesNotMatch(views[0].text, /Private fixture/);
});

test('Missing reasoning is reported honestly; local runs and disabled summaries are not exposed', async () => {
  const {createActivityHandler} = await handlerModule;
  const views=[];
  const ctx={send: async view => {views.push(view); return {ok:true};}};
  const handle=createActivityHandler({showReasoning:true});
  await handle({type:'agent-settled',source:'local',activityId:'local'},ctx);
  assert.equal(views.length,0);
  await handle({type:'agent-settled',source:'telegram',activityId:'remote'},ctx);
  assert.match(views[0].text,/模型未提供/);
  const disabled=createActivityHandler({showReasoning:false,showToolProgress:false});
  await disabled({type:'reasoning-end',source:'telegram',activityId:'off',text:'Synthetic hidden summary'},ctx);
  await disabled({type:'agent-settled',source:'telegram',activityId:'off'},ctx);
  assert.equal(views.length,1);
});
