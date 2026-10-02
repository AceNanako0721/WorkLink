"""Apply a narrow, reproducible local endpoint patch to a pinned upstream clone."""
import argparse
import hashlib
from pathlib import Path
import subprocess

COMMIT = '033a92f3bb56cef5e71a6389b99ca72e2132a77a'
SOURCE_SHA256 = '785181eed0212960b2634de4f1164ebc0fdc6c9c594da817662f0366f63834f9'
COMMAND_SHA256 = 'cfa60657e6b11ee4e307a3085b701422952a353b11ad067553e840842f93d5ee'
ROUTING_SHA256 = 'a26e0f6a3fa41dc8b8a81531f5c2c9b07e3cf967ff5cf6d2e39f413654ccb02c'
STATUS_SHA256 = 'a0c1cac67bda436ccf08b2acdabf5316b1ec1e46e02991610188561a15b26374'
MARKER = '// WorkLink loopback endpoint patch v1'
BASE = '''// WorkLink loopback endpoint patch v1
function workLinkTelegramBase(): string {
  const override = process.env.WORKLINK_TELEGRAM_API_BASE;
  if (!override) return "https://api.telegram.org";
  const url = new URL(override);
  if (url.protocol !== "http:" || url.hostname !== "127.0.0.1" ||
      url.pathname !== "/" || url.search || url.hash || url.username || url.password) {
    throw new Error("WorkLink requires a loopback HTTP endpoint");
  }
  return url.origin;
}
export const TELEGRAM_API_BASE = workLinkTelegramBase();'''
COMMAND_HOOK = '''  onUnhandledCommand?: (
    command: ParsedTelegramCommand,
    message: TMessage,
    ctx: TContext,
  ) => Promise<boolean>;
'''
COMMAND_GUARD = '''      if (command && deps.onUnhandledCommand &&
          await deps.onUnhandledCommand(command, firstMessage, ctx)) return;
'''
ROUTING_GUARD = '''    onUnhandledCommand: async (command, message) => {
      if (!process.env.WORKLINK_TELEGRAM_API_BASE) return false;
      await deps.sendTextReply(
        message.chat.id, message.message_id,
        `This command is not exposed by the Telegram plugin: /${command.name}`,
        { target: Updates.getTelegramMessageTarget(message) },
      );
      return true;
    },
'''


def patch(plugin):
    plugin = Path(plugin).resolve()
    root = Path(__file__).resolve().parents[1]
    if plugin == root or root in plugin.parents:
        raise ValueError('Keep the upstream clone outside the public checkout')
    commit = subprocess.check_output(['git', '-C', str(plugin), 'rev-parse', 'HEAD'], text=True).strip()
    if commit != COMMIT:
        raise ValueError('Unsupported upstream revision; use the documented pinned commit')
    path = plugin / 'lib' / 'telegram-api.ts'
    original = path.read_text(encoding='utf-8')
    baseline = subprocess.check_output(['git', '-C', str(plugin), 'show', 'HEAD:lib/telegram-api.ts']).decode().replace('\r\n', '\n')
    if hashlib.sha256(baseline.encode()).hexdigest() != SOURCE_SHA256:
        raise ValueError('Unexpected upstream transport source')
    expected = baseline.replace('export const TELEGRAM_API_BASE = "https://api.telegram.org";', BASE)
    expected = expected.replace('import { request as requestHttps } from "node:https";', 'import { request as requestHttps } from "node:https";\nimport { request as requestHttp } from "node:http";')
    expected = expected.replace('const req = requestHttps(', 'const req = (url.protocol === "http:" ? requestHttp : requestHttps)(')
    edits = [(path, original, baseline, expected)]
    for name, digest in [('commands.ts', COMMAND_SHA256), ('routing.ts', ROUTING_SHA256), ('status.ts', STATUS_SHA256)]:
        source = subprocess.check_output(['git', '-C', str(plugin), 'show', 'HEAD:lib/' + name]).decode().replace('\r\n', '\n')
        if hashlib.sha256(source.encode()).hexdigest() != digest:
            raise ValueError('Unexpected command routing source')
        target = source
        if name == 'commands.ts':
            target = target.replace('  replaceMessageText: (message: TMessage, text: string) => TMessage;', COMMAND_HOOK + '  replaceMessageText: (message: TMessage, text: string) => TMessage;')
            target = target.replace('      await deps.enqueueTurn(messages, ctx);', COMMAND_GUARD + '      await deps.enqueueTurn(messages, ctx);')
        elif name == 'routing.ts':
            target = target.replace('    enqueueTurn: promptEnqueue,\n  });', ROUTING_GUARD + '    enqueueTurn: promptEnqueue,\n  });')
        else:
            # Some installed OMP command contexts expose only the model query
            # facade. Missing optional statistics must not break /help or /status.
            target = target.replace('ctx.sessionManager.getEntries()', '(ctx.sessionManager?.getEntries?.() ?? [])')
            target = target.replace('ctx.getContextUsage()', 'ctx.getContextUsage?.()')
            target = target.replace('ctx.modelRegistry.isUsingOAuth(activeModel)', '(ctx.modelRegistry?.isUsingOAuth?.(activeModel) ?? false)')
        file = plugin/'lib'/name
        edits.append((file, file.read_text(encoding='utf-8'), source, target))
    # Validate every file before changing any of them; preserve unrelated local edits.
    if any(current not in {source, target} for _, current, source, target in edits):
        raise ValueError('Plugin has local changes; preserve them and review manually')
    changed = False
    for file, current, source, target in edits:
        if current != target:
            with file.open('w', encoding='utf-8', newline='\n') as output:
                output.write(target)
            changed = True
    return changed


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('plugin', type=Path)
    args = parser.parse_args()
    try:
        changed = patch(args.plugin)
    except (ValueError, OSError, subprocess.CalledProcessError):
        raise SystemExit('Patch refused: verify the pinned revision and preserve existing local changes.')
    print('Local endpoint patch applied.' if changed else 'Local endpoint patch already matches.')
