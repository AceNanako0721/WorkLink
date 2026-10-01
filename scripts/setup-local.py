"""Initialize external configuration without displaying either access key."""
import argparse
import json
from pathlib import Path
import secrets

root = Path(__file__).resolve().parent.parent
parser = argparse.ArgumentParser()
parser.add_argument('--private-dir', type=Path, default=root.parent / 'WorkLink-private')
parser.add_argument('--self-chat-title')
args = parser.parse_args()
private = args.private_dir.resolve()
def external(path):
    path = path.resolve()
    if path == root or root in path.parents:
        raise SystemExit('Private files must be outside the checkout, including linked targets.')
    return path
external(private)
private.mkdir(parents=True, exist_ok=True)
config_file = external(private / 'config.json')
config = json.loads(config_file.read_text(encoding='utf-8')) if config_file.exists() else json.loads((root / 'config.example.json').read_text())
if args.self_chat_title:
    config['teams']['selfChatDisplayName'] = args.self_chat_title
config['privateDataDirectory'] = str(external(private / 'data'))
external(private / 'secrets').mkdir(exist_ok=True)
bot = external(private / 'secrets' / 'bot-token.txt')
adapter = external(private / 'secrets' / 'adapter-token.txt')
for file, value in [(bot, '9000001:' + secrets.token_urlsafe(32)), (adapter, secrets.token_urlsafe(32))]:
    if not file.exists():
        file.write_text(value, encoding='utf-8')
config['keys'] = {'botTokenFile': str(bot), 'adapterTokenFile': str(adapter)}
config_file.write_text(json.dumps(config, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
print('External configuration prepared; existing settings and keys preserved.')
