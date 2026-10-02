/* Display provider-supplied reasoning summaries and executed-tool states only. */
import { readFile, writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';

export function requestReasoningSummary(payload, model, enabled) {
  if (!enabled || model?.provider !== 'openai-codex' || model.compat?.supportsReasoningSummary === false ||
      !payload || typeof payload !== 'object' || Array.isArray(payload) || payload.model !== model.id ||
      !payload.reasoning || !payload.reasoning.effort || ['none', 'off'].includes(payload.reasoning.effort)) return;
  return {...payload, reasoning: {...payload.reasoning, summary: 'detailed'}};
}

export function createActivityHandler(options) {
  const runs = new Map();
  return async (event, context) => {
    if (event.source !== 'telegram') return;
    const run = runs.get(event.activityId) || {reasoning: [], tools: new Map()};
    runs.set(event.activityId, run);
    if (event.type === 'reasoning-end' && options.showReasoning && event.text?.trim()) {
      if (run.reasoning.length < 8) run.reasoning.push(event.text.trim().slice(0, 2200));
    }
    if (options.showToolProgress && ['tool-start', 'tool-end'].includes(event.type)) {
      if (run.tools.size < 20 || run.tools.has(event.toolCallId)) {
        run.tools.set(event.toolCallId, {name: event.toolName, state: event.type === 'tool-start' ? '运行中' : event.isError ? '失败' : '完成'});
      }
    }
    if (event.type !== 'agent-settled') return;
    runs.delete(event.activityId);
    const parts = [];
    if (options.showReasoning) {
      parts.push(run.reasoning.length ? '思考摘要（模型提供）\n' + run.reasoning.join('\n\n') : '处理状态：已完成；模型未提供可展示的思考摘要。');
    }
    if (run.tools.size) parts.push('工具执行\n' + [...run.tools.values()].map(t => `${t.name}：${t.state}`).join('\n'));
    if (parts.length) {
      // Send one bounded report, avoiding repeated streaming edits. The service
      // serializes this report with the native final-reply effect.
      const text = parts.join('\n\n').replace(/[ \t\u00a0]+/g, ' ').slice(0, 3500);
      const result = await context.send({text, parseMode: 'plain'});
      if (!result.ok) throw new Error('WorkLink activity report was not delivered');
    }
  };
}

export default async function (pi) {
  const plugin = process.env.WORKLINK_TELEGRAM_PLUGIN_DIR;
  const profile = process.env.PI_CODING_AGENT_DIR;
  if (!plugin || !profile) throw new Error('Use the WorkLink OMP launcher');
  const { registerTelegramActivityHandler } = await import(pathToFileURL(join(plugin, 'api/activity.ts')).href);
  let dispose, config = {};
  pi.on('before_provider_request', async (event, context) => {
    const model = context.model || context.models?.current?.();
    const result = requestReasoningSummary(event.payload, model, config.showReasoning === true);
    // Local diagnostics contain only booleans, never requests or model content.
    await writeFile(join(profile, 'worklink-activity-diagnostics.json'), JSON.stringify({
      enabled: config.showReasoning === true, codexProvider: model?.provider === 'openai-codex',
      matchingModel: event.payload?.model === model?.id,
      hasEffort: !!event.payload?.reasoning?.effort, summaryRequested: !!result
    })).catch(() => {});
    return result;
  });
  pi.on('session_start', async () => {
    dispose?.();
    config = JSON.parse(await readFile(join(profile, 'worklink-activity.json'), 'utf8'));
    dispose = registerTelegramActivityHandler({id: 'worklink.activity', handle: createActivityHandler(config)});
  });
  pi.on('session_shutdown', () => { dispose?.(); dispose = undefined; });
}
