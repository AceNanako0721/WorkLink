"""Package only approved tracked files from an unchanged tagged checkout."""
import hashlib
from pathlib import Path
import re
import subprocess
import zipfile

root = Path(__file__).resolve().parent.parent
version = (root / 'VERSION').read_text().strip()
if not re.fullmatch(r'\d+\.\d+\.\d+(?:-[a-z0-9.-]+)?', version):
    raise SystemExit('Invalid VERSION.')
tag = subprocess.check_output(['git', 'describe', '--tags', '--exact-match', 'HEAD'], cwd=root, text=True).strip()
if tag != 'v' + version:
    raise SystemExit('Tag does not match VERSION.')
if subprocess.check_output(['git', 'status', '--porcelain'], cwd=root):
    raise SystemExit('Checkout must be clean.')
dist = root / 'dist'
dist.mkdir(exist_ok=True)
archive = dist / f'WorkLink-{version}.zip'
allowed_files = {'README.md', 'VERSION', 'LICENSE', 'config.example.json', 'AGENTS.md', '.gitignore'}
allowed_dirs = {'src', 'doc', 'skills', 'scripts', 'tests'}
names = subprocess.check_output(['git', 'ls-files', '-z'], cwd=root).decode().split('\0')
with zipfile.ZipFile(archive, 'x', compression=zipfile.ZIP_DEFLATED) as output:
    for name in names:
        if name and (name in allowed_files or Path(name).parts[0] in allowed_dirs):
            output.writestr('WorkLink/' + name, subprocess.check_output(['git', 'show', 'HEAD:' + name], cwd=root))
digest = hashlib.sha256(archive.read_bytes()).hexdigest()
(dist / 'SHA256SUMS.txt').write_text(f'{digest}  {archive.name}\n', encoding='utf-8')
print(f'Packaged {archive.name} using approved tracked files.')
