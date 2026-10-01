"""Inspect staged Git content and reachable history without printing secret values."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent
ALLOWED_ROOTS = {'doc', 'skills', 'scripts', 'tests', 'src', '.github'}
ALLOWED_FILES = {'README.md', 'VERSION', 'LICENSE', 'config.example.json', '.gitignore', '.gitattributes', 'AGENTS.md', 'package.json', 'package-lock.json', 'pyproject.toml', 'requirements.txt'}
FORBIDDEN_SUFFIXES = {'.png', '.jpg', '.jpeg', '.zip', '.db', '.sqlite', '.sqlite3', '.log', '.pem', '.key', '.pfx', '.har', '.xlsm', '.docx', '.pdf'}
FORBIDDEN_PARTS = {'private', '.private', 'artifacts', 'screenshots', 'downloads', 'logs', 'browser-profile', 'node_modules', '.venv', '__pycache__'}
PATTERNS = {
    'credential': re.compile(r'\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|AKIA[A-Z0-9]{16}|sk-[A-Za-z0-9_-]{24,})\b'),
    'private-key': re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----'),
    'absolute-windows-path': re.compile(r'(?i)\b[A-Z]:[\\/][A-Za-z0-9_\u0080-\uffff]'),
    'unc-path': re.compile(r'\\\\(?:\d{1,3}\.){3}\d{1,3}\\'),
    'telegram-token': re.compile(r'\b\d{6,12}:[A-Za-z0-9_-]{30,}\b'),
}
EMAIL = re.compile(r'[A-Za-z0-9_.+%-]+@([A-Za-z0-9.-]+\.[A-Za-z]{2,})')
TENANT = re.compile(r'https?://([A-Za-z0-9-]+)\.sharepoint\.com', re.I)

def git(*args):
    return subprocess.check_output(['git', *args], cwd=ROOT)

def path_problem(name):
    parts = Path(name).parts
    if not parts or any(p in FORBIDDEN_PARTS for p in parts):
        return 'private/runtime path'
    if parts[0] not in ALLOWED_ROOTS and name not in ALLOWED_FILES:
        return 'outside public file allowlist'
    if Path(name).suffix.lower() in FORBIDDEN_SUFFIXES or Path(name).name.startswith('.env'):
        return 'private/binary file type'
    if Path(name).name == 'config.json' or Path(name).name.startswith('config.local.'):
        return 'local configuration'
    return None

def content_problems(data, deny=()):
    if len(data) > 1024 * 1024:
        return ['file exceeds review size limit']
    try:
        text = data.decode('utf-8')
    except UnicodeDecodeError:
        return ['non-UTF-8 or binary content']
    if '\x00' in text:
        return ['binary content']
    found = [label for label, pattern in PATTERNS.items() if pattern.search(text)]
    if any(m.group(1).lower() not in {'example.invalid', 'example.com', 'users.noreply.github.com'} for m in EMAIL.finditer(text)):
        found.append('non-example/non-noreply email')
    if any(m.group(1).lower() != 'example' for m in TENANT.finditer(text)):
        found.append('real SharePoint tenant')
    if any(value.casefold() in text.casefold() for value in deny):
        found.append('local private deny value')
    return found

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--history', action='store_true')
    options = parser.parse_args()
    deny_path = Path(os.environ.get('WORKLINK_PRIVACY_DENY_FILE', str(ROOT.parent / 'WorkLink-private' / 'privacy-deny.json')))
    deny = []
    if deny_path.exists():
        deny = json.loads(deny_path.read_text(encoding='utf-8'))
        if not isinstance(deny, list) or any(not isinstance(v, str) or not v for v in deny):
            raise ValueError('Private deny file must contain nonempty strings.')
    failures = []
    for name in git('ls-files', '-z').decode('utf-8').split('\0'):
        if not name:
            continue
        reason = path_problem(name)
        if reason:
            failures.append((name, reason))
        for problem in content_problems(git('show', ':' + name), deny):
            failures.append((name, problem))
    if options.history:
        for line in git('rev-list', '--objects', '--all').decode('utf-8').splitlines():
            oid, _, name = line.partition(' ')
            if git('cat-file', '-t', oid).strip() != b'blob':
                continue
            reason = path_problem(name)
            if reason:
                failures.append((name, 'history: ' + reason))
            for problem in content_problems(git('cat-file', 'blob', oid), deny):
                failures.append((name, 'history: ' + problem))
        for oid in git('rev-list', '--all').decode().splitlines():
            # Inspect author/committer identities and commit messages as well.
            for problem in content_problems(git('cat-file', 'commit', oid), deny):
                failures.append((oid[:12], 'commit metadata: ' + problem))
    for name, reason in sorted(set(failures)):
        print(f'BLOCKED: {name}: {reason}', file=sys.stderr)
    if failures:
        return 1
    print('Public-content checks passed (index' + (' and reachable history' if options.history else '') + ').')
    return 0

if __name__ == '__main__':
    sys.exit(main())
