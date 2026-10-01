"""Write a dedicated external OMP profile; never modify an existing OMP profile."""
import argparse
import json
from pathlib import Path

root = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument('--config', type=Path, default=root.parent/'WorkLink-private'/'config.json')
args = parser.parse_args()
config_path = args.config.resolve()
if config_path == root or root in config_path.parents:
    raise SystemExit('Use external private configuration.')
config = json.loads(config_path.read_text(encoding='utf-8'))
agent_dir = (config_path.parent/'omp-agent').resolve()
if agent_dir == root or root in agent_dir.parents:
    raise SystemExit('Use an external OMP agent directory.')
agent_dir.mkdir(exist_ok=True)
target = agent_dir/'telegram.json'
if target.exists():
    raise SystemExit('Dedicated OMP configuration already exists; keep its session and offset state.')
token_file = Path(config['keys']['botTokenFile']).resolve()
if token_file == root or root in token_file.parents:
    raise SystemExit('Use external keys.')
profile = {'botToken': token_file.read_text(encoding='utf-8').strip(), 'botId': 9000001,
           'botUsername': 'worklink_bot', 'allowedUserId': config.get('ownerId', 1001),
           'assistant': {'draftPreviews': False, 'rendering': 'html'}}
target.write_text(json.dumps(profile, indent=2)+'\n', encoding='utf-8')
print('Dedicated external OMP profile prepared. Draft previews are disabled.')
