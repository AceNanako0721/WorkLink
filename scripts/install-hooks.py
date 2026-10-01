"""Install repository-local Git checks; does not alter global Git settings."""
from pathlib import Path
import shlex
import subprocess
import sys

root = Path(__file__).resolve().parent.parent
hooks = root / '.git' / 'hooks'
if not hooks.is_dir():
    raise SystemExit('Initialize this Git repository first.')
for name, arguments in [('pre-commit', ''), ('pre-push', '--history')]:
    target = hooks / name
    marker = '# WorkLink public-content guard'
    if target.exists() and marker not in target.read_text(encoding='utf-8'):
        raise SystemExit(f'Existing {name} hook preserved; install guard manually.')
    text = '#!/bin/sh\n' + marker + '\nexec ' + shlex.quote(sys.executable.replace('\\', '/')) + ' scripts/check-public.py ' + arguments + '\n'
    target.write_text(text, encoding='utf-8', newline='\n')
    target.chmod(0o755)
print('Installed local pre-commit and pre-push privacy checks.')
