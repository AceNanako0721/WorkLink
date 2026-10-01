"""Manual offline recovery only after the operator checks the real Teams outcome."""
import argparse
import json
from pathlib import Path
import sqlite3
from urllib.request import urlopen

root = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument('--config', type=Path, default=root.parent/'WorkLink-private'/'config.json')
parser.add_argument('--job')
parser.add_argument('--confirm-not-applied', action='store_true')
args = parser.parse_args()
config_path = args.config.resolve()
if config_path == root or root in config_path.parents:
    raise SystemExit('Use external configuration.')
config = json.loads(config_path.read_text(encoding='utf-8'))
private = Path(config['privateDataDirectory']).resolve()
if private == root or root in private.parents:
    raise SystemExit('Use external runtime state.')
if args.confirm_not_applied:
    try:
        with urlopen('http://127.0.0.1:'+str(config.get('service',{}).get('port',8765))+'/health', timeout=1):
            pass
    except OSError:
        pass
    else:
        raise SystemExit('Stop the service before manual recovery.')
database = (private/'worklink.sqlite').resolve()
if database == root or root in database.parents:
    raise SystemExit('Use external runtime state, including linked targets.')
with sqlite3.connect(database) as db:
    db.row_factory = sqlite3.Row
    jobs = db.execute("SELECT id,kind,state FROM jobs WHERE state IN ('pending','leased','unknown')").fetchall()
    if not args.confirm_not_applied:
        print(json.dumps([dict(row) for row in jobs], indent=2))
    else:
        if not args.job or not any(row['id']==args.job for row in jobs):
            raise SystemExit('Specify an unfinished operation from the listing.')
        # No replay and no fabricated successful message identity.
        db.execute("UPDATE jobs SET state='failed' WHERE id=?", (args.job,))
        print('Operation marked not applied. Clear its browser pending record before restarting.')
