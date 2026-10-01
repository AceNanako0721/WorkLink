"""Exercise the actual archive builder against a tagged synthetic Git checkout."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]


class ReleasePackageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root/'scripts').mkdir()
        shutil.copyfile(ROOT/'scripts/package-release.py', self.root/'scripts/package-release.py')
        for name, content in {'README.md': 'Synthetic release', 'VERSION': '0.1.0\n',
                              '.gitignore': 'private/\ndist/\n', 'config.example.json': '{}',
                              'unexpected.txt': 'Synthetic excluded file'}.items():
            (self.root/name).write_text(content, encoding='utf-8')
        (self.root/'src').mkdir()
        (self.root/'src/service.py').write_text('print("synthetic")\n')
        self.git('init', '-q')
        self.git('config', 'user.name', 'Synthetic')
        self.git('config', 'user.email', 'fixture@example.invalid')
        self.git('add', '.')
        self.git('-c', 'commit.gpgsign=false', 'commit', '-qm', 'Synthetic release')

    def tearDown(self):
        self.temp.cleanup()

    def git(self, *args):
        return subprocess.run(['git', *args], cwd=self.root, check=True, capture_output=True)

    def build(self):
        import sys
        return subprocess.run([sys.executable, 'scripts/package-release.py'], cwd=self.root, capture_output=True)

    def test_archive_contains_only_approved_tracked_files(self):
        self.git('tag', 'v0.1.0')
        (self.root/'private').mkdir()
        (self.root/'private/token.txt').write_text('synthetic private fixture')
        built = self.build()
        self.assertEqual(built.returncode, 0, built.stderr.decode(errors='replace'))
        with zipfile.ZipFile(self.root/'dist/WorkLink-0.1.0.zip') as archive:
            names = archive.namelist()
            self.assertIn('WorkLink/src/service.py', names)
            self.assertIn('WorkLink/config.example.json', names)
            self.assertNotIn('WorkLink/unexpected.txt', names)
            self.assertFalse(any('private/' in name for name in names))
        self.assertTrue((self.root/'dist/SHA256SUMS.txt').exists())

    def test_builder_refuses_untagged_mismatched_or_dirty_checkout(self):
        self.assertNotEqual(self.build().returncode, 0)
        self.git('tag', 'v0.2.0')
        self.assertNotEqual(self.build().returncode, 0)
        self.git('tag', '-d', 'v0.2.0')
        self.git('tag', 'v0.1.0')
        (self.root/'README.md').write_text('Dirty fixture')
        self.assertNotEqual(self.build().returncode, 0)
