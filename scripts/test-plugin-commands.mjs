/* Exercise the pinned plugin's command dispatcher without an OMP/model session. */
import assert from 'node:assert/strict';
import {resolve} from 'node:path';
import {pathToFileURL} from 'node:url';
const [plugin] = process.argv.slice(2);
if (!plugin) throw new Error('Provide the external pinned plugin directory.');
const api = await import(pathToFileURL(resolve(plugin, 'lib/commands.ts')));
const events = [];
const runtime = api.createTelegramCommandOrPromptRuntime({
  extractRawText: messages => messages[0].text,
  handleCommand: async name => {if (name === 'model') {events.push('model-control'); return true;} return false;},
  executeExtensionCommand: async command => {if (command.name === 'fixture') {events.push(command.args);return true;}return false;},
  expandPromptTemplateCommand: name => name === 'review' ? 'Review fixture' : undefined,
  replaceMessageText: (message, text) => ({...message, text}),
  onUnhandledCommand: async command => {events.push('unsupported:' + command.name);return true;},
  enqueueTurn: async messages => events.push('prompt:' + messages[0].text)
});
for (const text of ['/model', '/fixture keep args', '/review', '/not_exposed', 'Hello']) {
  await runtime.dispatchMessages([{text, message_id: 1, chat: {id: 1001}}], {});
}
assert.deepEqual(events, ['model-control', 'keep args', 'prompt:Review fixture', 'unsupported:not_exposed', 'prompt:Hello']);
console.log('Pinned plugin command/control/template dispatch and unknown-command rejection passed.');
