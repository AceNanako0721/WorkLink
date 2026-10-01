/* Read-only transport probe against an already running local service. */
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';

const [plugin, tokenFile] = process.argv.slice(2);
if (!plugin || !tokenFile || !process.env.WORKLINK_TELEGRAM_API_BASE) {
  throw new Error('Provide the external plugin directory, token file, and loopback endpoint environment variable.');
}
const token = (await readFile(tokenFile, 'utf8')).trim();
const api = await import(pathToFileURL(resolve(plugin, 'lib/telegram-api.ts')));
assert.equal(api.TELEGRAM_API_BASE, process.env.WORKLINK_TELEGRAM_API_BASE.replace(/\/$/, ''));
const identity = await api.fetchTelegramBotIdentity(token);
assert.equal(identity.ok, true);
assert.equal(identity.result.id, 9000001);
assert.equal(identity.result.has_topics_enabled, false);
const chat = await api.callTelegram(token, 'getChat', {chat_id: 1001});
assert.equal(chat.id, 1001);
assert.equal(chat.type, 'private');
console.log('Pinned plugin -> local API: identity and chat checks passed. No Teams message sent.');
